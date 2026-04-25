#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
import uvicorn

from runner_common import (
    authenticate_runner_bearer,
    archon_post,
    assert_runtime_ready,
    build_runtime_diagnostics,
    build_worker_message,
    create_request_id,
    extract_first_json_object,
    run_openclaw_agent,
)

WORKER_AGENT = os.getenv("WORKER_AGENT", "archon-worker")
OPENCLAW_WORKER_MODEL = os.getenv("OPENCLAW_WORKER_MODEL", "ollama/llama3.1:8b")
WORKER_POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "15"))
OPENCLAW_WORKER_TIMEOUT_SECONDS = int(os.getenv("OPENCLAW_WORKER_TIMEOUT_SECONDS", "600"))
OWNER_NAME = os.getenv("WORKER_OWNER_NAME", "openclaw-worker")
RUN_LOOP = os.getenv("WORKER_BACKGROUND_LOOP", "true").lower() == "true"

app = FastAPI(title="OpenClaw Worker Runner", version="0.3.0")
STATE: dict[str, Any] = {"last_run_at": None, "last_result": None, "last_error": None, "audit_events": []}


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
    assert_runtime_ready(agent_id=WORKER_AGENT, expected_model=OPENCLAW_WORKER_MODEL)
    claimed = archon_post(
        "/tasks/claim",
        {
            "kind": "worker",
            "ttl_seconds": OPENCLAW_WORKER_TIMEOUT_SECONDS,
            "eligible_statuses": ["queued", "needs_changes"],
        },
        request_id=request_id,
    )
    task = claimed.get("item")
    if not task:
        return {"processed": 0}

    task_id = int(task["id"])
    archon_post(f"/tasks/{task_id}/transition", {"status": "working", "notes": "claimed by worker"}, request_id=request_id)
    result = run_openclaw_agent(
        agent=WORKER_AGENT,
        message=build_worker_message(task),
        timeout_seconds=OPENCLAW_WORKER_TIMEOUT_SECONDS,
    )

    if not result.ok:
        archon_post(
            "/worker-runs",
            {
                "task_id": task_id,
                "agent": WORKER_AGENT,
                "model": OPENCLAW_WORKER_MODEL,
                "status": "failed",
                "summary": "OpenClaw worker command failed.",
                "artifacts": [],
                "follow_up": [result.stderr.strip() or "worker failed without stderr"],
            },
            request_id=request_id,
        )
        archon_post(
            f"/tasks/{task_id}/release",
            {
                "owner": OWNER_NAME,
                "status": "failed",
                "last_error": result.stderr.strip() or "worker command failed",
            },
            request_id=request_id,
        )
        return {"processed": 1, "task_id": task_id, "status": "failed"}

    payload = extract_first_json_object(result.stdout) or {}
    status = payload.get("status", "completed")
    if status not in {"completed", "blocked", "failed"}:
        status = "completed"
    summary = payload.get("summary") or "Worker run completed."
    artifacts = [str(item) for item in (payload.get("artifacts") or [])]
    follow_up = [str(item) for item in (payload.get("follow_up") or [])]

    persisted = archon_post(
        "/worker-runs",
        {
            "task_id": task_id,
            "agent": WORKER_AGENT,
            "model": OPENCLAW_WORKER_MODEL,
            "status": status,
            "summary": summary,
            "artifacts": artifacts,
            "follow_up": follow_up,
        },
        request_id=request_id,
    )
    archon_post(f"/tasks/{task_id}/release", {"owner": OWNER_NAME, "status": persisted["status"]}, request_id=request_id)
    return {"processed": 1, "task_id": task_id, "status": persisted["status"], "summary": summary}


def loop() -> None:
    while True:
        STATE["last_run_at"] = _now()
        try:
            STATE["last_result"] = process_one(request_id=create_request_id())
            STATE["last_error"] = None
        except Exception as exc:
            STATE["last_error"] = str(exc)
        time.sleep(WORKER_POLL_SECONDS)


@app.on_event("startup")
def startup() -> None:
    if RUN_LOOP:
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()


@app.get("/health")
@app.get("/healthz")
def health() -> dict[str, Any]:
    diagnostics = build_runtime_diagnostics(agent_id=WORKER_AGENT, expected_model=OPENCLAW_WORKER_MODEL)
    return {
        "ok": diagnostics["ok"],
        "agent": WORKER_AGENT,
        "model": OPENCLAW_WORKER_MODEL,
        "poll_seconds": WORKER_POLL_SECONDS,
        "runtime": diagnostics,
        **STATE,
    }


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    diagnostics = build_runtime_diagnostics(agent_id=WORKER_AGENT, expected_model=OPENCLAW_WORKER_MODEL)
    return {"ok": diagnostics["ok"], "service": "worker-runner", "runtime": diagnostics}


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
    uvicorn.run(app, host="0.0.0.0", port=8091)
