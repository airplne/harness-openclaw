from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI

ARCHON_API_BASE_URL = os.getenv("ARCHON_API_BASE_URL", "http://archon:8080")
OPENCLAW_GATEWAY_BASE_URL = os.getenv("OPENCLAW_GATEWAY_BASE_URL", "http://openclaw:8090")
OPENCLAW_CODEX_REVIEW_MODEL = os.getenv("OPENCLAW_CODEX_REVIEW_MODEL", "openai-codex/gpt-5.4")
OPENCLAW_REVIEW_TIMEOUT_SECONDS = int(os.getenv("OPENCLAW_REVIEW_TIMEOUT_SECONDS", "300"))
REVIEW_INTERVAL_SECONDS = int(os.getenv("REVIEW_INTERVAL_SECONDS", "300"))
ARCHON_REVIEW_QUEUE = os.getenv("ARCHON_REVIEW_QUEUE", "codex-review")

app = FastAPI(title="Review Scheduler", version="0.1.0")

STATE: dict[str, Any] = {
    "last_tick_at": None,
    "last_result": None,
    "last_error": None,
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_review(stdout: str) -> tuple[str, str | None, list[str]]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return "pending_human_approval", "No review output was returned.", []

    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError:
        return "pending_human_approval", "Review output was not valid JSON; manual inspection required.", lines[:10]

    status = payload.get("status", "pending_human_approval")
    summary = payload.get("summary")
    findings = payload.get("findings") or []
    if not isinstance(findings, list):
        findings = [str(findings)]
    return status, summary, [str(item) for item in findings]


def run_review_cycle() -> dict[str, Any]:
    queue = requests.get(f"{ARCHON_API_BASE_URL}/reviews/queue", timeout=30).json()
    processed = []

    for task in queue.get("items", []):
        result = requests.post(
            f"{OPENCLAW_GATEWAY_BASE_URL}/run-skill",
            json={
                "message": f"/skill codex-reviewer\n\nTask #{task['id']}: {task['title']}\n\n{task.get('description', '')}",
                "model": OPENCLAW_CODEX_REVIEW_MODEL,
                "task_id": task["id"],
                "timeout_seconds": OPENCLAW_REVIEW_TIMEOUT_SECONDS,
            },
            timeout=OPENCLAW_REVIEW_TIMEOUT_SECONDS + 30,
        )
        result.raise_for_status()
        run_data = result.json()
        status, summary, findings = parse_review(run_data.get("stdout", ""))

        review_payload = {
            "task_id": task["id"],
            "review_queue": ARCHON_REVIEW_QUEUE,
            "model": OPENCLAW_CODEX_REVIEW_MODEL,
            "status": status,
            "summary": summary,
            "findings": findings,
            "raw_output": run_data.get("stdout", ""),
        }
        review_resp = requests.post(f"{ARCHON_API_BASE_URL}/reviews", json=review_payload, timeout=30)
        review_resp.raise_for_status()
        processed.append({"task_id": task["id"], "status": status, "summary": summary})

    return {"processed": processed, "count": len(processed)}


def loop() -> None:
    while True:
        STATE["last_tick_at"] = utcnow()
        try:
            STATE["last_result"] = run_review_cycle()
            STATE["last_error"] = None
        except Exception as exc:  # noqa: BLE001
            STATE["last_error"] = str(exc)
        time.sleep(REVIEW_INTERVAL_SECONDS)


@app.on_event("startup")
def startup() -> None:
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, **STATE, "interval_seconds": REVIEW_INTERVAL_SECONDS}


if __name__ == "__main__":
    import uvicorn

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    uvicorn.run(app, host="0.0.0.0", port=8079)
