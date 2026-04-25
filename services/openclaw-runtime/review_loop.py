#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
import uvicorn

from runner_common import (
    authenticate_runner_bearer,
    archon_post,
    assert_runtime_ready,
    build_review_message,
    build_runtime_diagnostics,
    cron_matches,
    create_request_id,
    extract_first_json_object,
    run_openclaw_agent,
)

REVIEW_AGENT = os.getenv("REVIEW_AGENT", "codex-reviewer")
OPENCLAW_REVIEW_MODEL = os.getenv("OPENCLAW_REVIEW_MODEL", "openai-codex/gpt-5.4")
OPENCLAW_REVIEW_TIMEOUT_SECONDS = int(os.getenv("OPENCLAW_REVIEW_TIMEOUT_SECONDS", "60"))
ARCHON_REVIEW_QUEUE = os.getenv("ARCHON_REVIEW_QUEUE", "codex-review")
REVIEW_CRON = os.getenv("REVIEW_CRON", "*/5 * * * *")
REVIEW_TIMEZONE = os.getenv("REVIEW_TIMEZONE", "UTC")
OWNER_NAME = os.getenv("REVIEW_OWNER_NAME", "openclaw-reviewer")
RUN_LOOP = os.getenv("REVIEW_BACKGROUND_LOOP", "true").lower() == "true"

app = FastAPI(title="OpenClaw Reviewer Runner", version="0.3.0")
STATE: dict[str, Any] = {
    "last_run_at": None,
    "last_result": None,
    "last_error": None,
    "last_cron_minute": None,
    "audit_events": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_audit(*, action: str, outcome: str, request_id: str | None, reason: str | None = None) -> None:
    STATE["audit_events"] = (
        STATE.get("audit_events", [])
        + [
            {
                "created_at": _now(),
                "request_id": create_request_id(request_id),
                "action": action,
                "outcome": outcome,
                "reason": reason,
            }
        ]
    )[-50:]


def process_one(*, request_id: str | None = None) -> dict[str, Any]:
    request_id = create_request_id(request_id)
    assert_runtime_ready(
        agent_id=REVIEW_AGENT,
        expected_model=OPENCLAW_REVIEW_MODEL,
        required_oauth_provider="openai-codex",
    )
    claimed = archon_post(
        "/tasks/claim",
        {
            "kind": "review",
            "ttl_seconds": OPENCLAW_REVIEW_TIMEOUT_SECONDS,
            "eligible_statuses": ["review_requested"],
        },
        request_id=request_id,
    )
    task = claimed.get("item")
    if not task:
        return {"processed": 0}

    task_id = int(task["id"])
    archon_post(f"/tasks/{task_id}/transition", {"status": "reviewing", "notes": "claimed by reviewer"}, request_id=request_id)
    result = run_openclaw_agent(
        agent=REVIEW_AGENT,
        message=build_review_message(task),
        timeout_seconds=OPENCLAW_REVIEW_TIMEOUT_SECONDS,
    )

    if not result.ok:
        persisted = archon_post(
            "/reviews",
            {
                "task_id": task_id,
                "review_queue": ARCHON_REVIEW_QUEUE,
                "agent": REVIEW_AGENT,
                "model": OPENCLAW_REVIEW_MODEL,
                "status": "failed",
                "summary": "Codex review command failed.",
                "findings": [result.stderr.strip() or "review command failed without stderr"],
                "follow_up": ["Inspect OpenClaw OAuth onboarding and provider setup."],
                "requires_human_approval": False,
            },
            request_id=request_id,
        )
        archon_post(
            f"/tasks/{task_id}/release",
            {"owner": OWNER_NAME, "status": persisted["status"], "last_error": result.stderr.strip()},
            request_id=request_id,
        )
        return {"processed": 1, "task_id": task_id, "status": persisted["status"]}

    payload = extract_first_json_object(result.stdout) or {}
    status = payload.get("status", "pending_human_approval")
    if status not in {"approved", "needs_changes", "rejected", "pending_human_approval", "failed"}:
        status = "pending_human_approval"
    summary = payload.get("summary") or "Codex review finished."
    findings = [str(item) for item in (payload.get("findings") or [])]
    follow_up = [str(item) for item in (payload.get("follow_up") or [])]
    requires_human_approval = bool(payload.get("requires_human_approval", True))

    persisted = archon_post(
        "/reviews",
        {
            "task_id": task_id,
            "review_queue": ARCHON_REVIEW_QUEUE,
            "agent": REVIEW_AGENT,
            "model": OPENCLAW_REVIEW_MODEL,
            "status": status,
            "summary": summary,
            "findings": findings,
            "follow_up": follow_up,
            "requires_human_approval": requires_human_approval,
        },
        request_id=request_id,
    )
    archon_post(f"/tasks/{task_id}/release", {"owner": OWNER_NAME, "status": persisted["status"]}, request_id=request_id)
    return {"processed": 1, "task_id": task_id, "status": persisted["status"], "summary": summary}


def loop() -> None:
    while True:
        now = datetime.now(timezone.utc)
        minute_key = now.astimezone(ZoneInfo(REVIEW_TIMEZONE)).strftime("%Y-%m-%dT%H:%M")
        if cron_matches(REVIEW_CRON, now, REVIEW_TIMEZONE) and STATE.get("last_cron_minute") != minute_key:
            STATE["last_cron_minute"] = minute_key
            STATE["last_run_at"] = _now()
            try:
                STATE["last_result"] = process_one(request_id=create_request_id())
                STATE["last_error"] = None
            except Exception as exc:
                STATE["last_error"] = str(exc)
        time.sleep(1)


@app.on_event("startup")
def startup() -> None:
    if RUN_LOOP:
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()


@app.get("/health")
@app.get("/healthz")
def health() -> dict[str, Any]:
    diagnostics = build_runtime_diagnostics(
        agent_id=REVIEW_AGENT,
        expected_model=OPENCLAW_REVIEW_MODEL,
        required_oauth_provider="openai-codex",
    )
    return {
        "ok": diagnostics["ok"] and OPENCLAW_REVIEW_MODEL.startswith("openai-codex/"),
        "agent": REVIEW_AGENT,
        "model": OPENCLAW_REVIEW_MODEL,
        "cron": REVIEW_CRON,
        "timezone": REVIEW_TIMEZONE,
        "runtime": diagnostics,
        **STATE,
    }


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    diagnostics = build_runtime_diagnostics(
        agent_id=REVIEW_AGENT,
        expected_model=OPENCLAW_REVIEW_MODEL,
        required_oauth_provider="openai-codex",
    )
    return {"ok": diagnostics["ok"], "service": "reviewer-runner", "runtime": diagnostics}


@app.post("/run-once")
def run_once(request: Request, _: dict[str, Any] | None = None) -> dict[str, Any]:
    request_id = request.headers.get("X-Request-ID")
    authz = request.headers.get("authorization", "")
    if not authz.startswith("Bearer "):
        _record_audit(action="runner.run_once", outcome="denied", request_id=request_id, reason="missing_bearer_token")
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        principal = authenticate_runner_bearer(authz.removeprefix("Bearer ").strip(), allowed_identity="archon")
    except RuntimeError as exc:
        _record_audit(action="runner.run_once", outcome="denied", request_id=request_id, reason=str(exc))
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if "runner:invoke" not in principal.scopes:
        _record_audit(action="runner.run_once", outcome="denied", request_id=request_id, reason="missing_runner_invoke_scope")
        raise HTTPException(status_code=403, detail="missing runner:invoke scope")
    STATE["last_run_at"] = _now()
    try:
        STATE["last_result"] = process_one(request_id=request_id)
        STATE["last_error"] = None
        _record_audit(action="runner.run_once", outcome="allowed", request_id=request_id)
    except Exception as exc:
        STATE["last_error"] = str(exc)
        _record_audit(action="runner.run_once", outcome="failed", request_id=request_id, reason=str(exc))
        raise
    return STATE["last_result"]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8092)
