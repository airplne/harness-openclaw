#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
import uvicorn

from runner_common import (
    archon_post,
    build_review_message,
    cron_matches,
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

app = FastAPI(title="OpenClaw Reviewer Runner", version="0.2.0")
STATE: dict[str, Any] = {"last_run_at": None, "last_result": None, "last_error": None, "last_cron_minute": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_one() -> dict[str, Any]:
    claimed = archon_post(
        "/tasks/claim",
        {
            "kind": "review",
            "owner": OWNER_NAME,
            "ttl_seconds": OPENCLAW_REVIEW_TIMEOUT_SECONDS,
            "eligible_statuses": ["review_requested"],
        },
    )
    task = claimed.get("item")
    if not task:
        return {"processed": 0}

    task_id = int(task["id"])
    archon_post(f"/tasks/{task_id}/transition", {"status": "reviewing", "notes": "claimed by reviewer"})
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
                "status": "pending_human_approval",
                "summary": "Codex review command failed.",
                "findings": [result.stderr.strip() or "review command failed without stderr"],
                "follow_up": ["Inspect OpenClaw auth and provider setup."],
                "requires_human_approval": True,
                "raw_output": result.stdout,
            },
        )
        archon_post(f"/tasks/{task_id}/release", {"owner": OWNER_NAME, "status": persisted["status"], "last_error": result.stderr.strip()})
        return {"processed": 1, "task_id": task_id, "status": persisted["status"]}

    payload = extract_first_json_object(result.stdout) or {}
    status = payload.get("status", "pending_human_approval")
    if status not in {"approved", "needs_changes", "rejected", "pending_human_approval"}:
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
            "raw_output": result.stdout,
        },
    )
    archon_post(f"/tasks/{task_id}/release", {"owner": OWNER_NAME, "status": persisted["status"]})
    return {"processed": 1, "task_id": task_id, "status": persisted["status"], "summary": summary}


def loop() -> None:
    while True:
        now = datetime.now(timezone.utc)
        minute_key = now.astimezone(ZoneInfo(REVIEW_TIMEZONE)).strftime("%Y-%m-%dT%H:%M")
        if cron_matches(REVIEW_CRON, now, REVIEW_TIMEZONE) and STATE.get("last_cron_minute") != minute_key:
            STATE["last_cron_minute"] = minute_key
            STATE["last_run_at"] = _now()
            try:
                STATE["last_result"] = process_one()
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
def health() -> dict[str, Any]:
    return {
        "ok": OPENCLAW_REVIEW_MODEL.startswith("openai-codex/"),
        "agent": REVIEW_AGENT,
        "model": OPENCLAW_REVIEW_MODEL,
        "cron": REVIEW_CRON,
        "timezone": REVIEW_TIMEZONE,
        **STATE,
    }


@app.post("/run-once")
def run_once(_: dict[str, Any] | None = None) -> dict[str, Any]:
    STATE["last_run_at"] = _now()
    try:
        STATE["last_result"] = process_one()
        STATE["last_error"] = None
    except Exception as exc:
        STATE["last_error"] = str(exc)
        raise
    return STATE["last_result"]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8092)
