#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
import uvicorn

from runner_common import (
    archon_post,
    build_worker_message,
    extract_first_json_object,
    run_openclaw_agent,
)

WORKER_AGENT = os.getenv("WORKER_AGENT", "archon-worker")
OPENCLAW_WORKER_MODEL = os.getenv("OPENCLAW_WORKER_MODEL", "ollama/qwen3-coder:latest")
WORKER_POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "15"))
OPENCLAW_WORKER_TIMEOUT_SECONDS = int(os.getenv("OPENCLAW_WORKER_TIMEOUT_SECONDS", "600"))
OWNER_NAME = os.getenv("WORKER_OWNER_NAME", "openclaw-worker")
RUN_LOOP = os.getenv("WORKER_BACKGROUND_LOOP", "true").lower() == "true"

app = FastAPI(title="OpenClaw Worker Runner", version="0.2.0")
STATE: dict[str, Any] = {"last_run_at": None, "last_result": None, "last_error": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_one() -> dict[str, Any]:
    claimed = archon_post(
        "/tasks/claim",
        {
            "kind": "worker",
            "owner": OWNER_NAME,
            "ttl_seconds": OPENCLAW_WORKER_TIMEOUT_SECONDS,
            "eligible_statuses": ["queued", "needs_changes"],
        },
    )
    task = claimed.get("item")
    if not task:
        return {"processed": 0}

    task_id = int(task["id"])
    archon_post(f"/tasks/{task_id}/transition", {"status": "working", "notes": "claimed by worker"})
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
                "raw_output": result.stdout,
            },
        )
        archon_post(
            f"/tasks/{task_id}/release",
            {
                "owner": OWNER_NAME,
                "status": "failed",
                "last_error": result.stderr.strip() or "worker command failed",
            },
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
            "raw_output": result.stdout,
        },
    )
    archon_post(f"/tasks/{task_id}/release", {"owner": OWNER_NAME, "status": persisted["status"]})
    return {"processed": 1, "task_id": task_id, "status": persisted["status"], "summary": summary}


def loop() -> None:
    while True:
        STATE["last_run_at"] = _now()
        try:
            STATE["last_result"] = process_one()
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
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "agent": WORKER_AGENT,
        "model": OPENCLAW_WORKER_MODEL,
        "poll_seconds": WORKER_POLL_SECONDS,
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
    uvicorn.run(app, host="0.0.0.0", port=8091)
