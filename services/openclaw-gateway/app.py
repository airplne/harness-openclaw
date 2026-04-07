from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="OpenClaw Gateway", version="0.1.0")

ARCHON_API_BASE_URL = os.getenv("ARCHON_API_BASE_URL", "http://archon:8080")
OPENCLAW_CONFIG_PATH = os.getenv("OPENCLAW_CONFIG_PATH", "/workspace/.openclaw/openclaw.json")
RUN_CMD_TEMPLATE = os.getenv(
    "OPENCLAW_SKILL_RUN_CMD",
    'python /app/mock_openclaw_runner.py --message "{message}" --model "{model}" --config "{config}"',
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def substitute(template: str, *, message: str, model: str, config: str) -> str:
    return template.format(message=message, model=model, config=config)


class SkillRunIn(BaseModel):
    message: str = "/skill codex-reviewer"
    model: str = "openai-codex/gpt-5.4"
    task_id: int | None = None
    timeout_seconds: int = 300


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "config_path": OPENCLAW_CONFIG_PATH,
        "runner_template": RUN_CMD_TEMPLATE,
        "timestamp": utcnow(),
    }


@app.post("/run-skill")
def run_skill(payload: SkillRunIn) -> dict[str, Any]:
    command = substitute(
        RUN_CMD_TEMPLATE,
        message=payload.message,
        model=payload.model,
        config=OPENCLAW_CONFIG_PATH,
    )
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=payload.timeout_seconds,
        cwd=os.getenv("OPENCLAW_WORKSPACE", "/workspace"),
    )

    response: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": command,
    }

    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=response)

    return response


@app.post("/tasks/{task_id}/request-approval")
def request_approval(task_id: int, body: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{ARCHON_API_BASE_URL}/approvals",
        json={
            "task_id": task_id,
            "decision": body.get("decision", "pending_human_approval"),
            "reviewer": body.get("reviewer", "openclaw"),
            "notes": body.get("notes"),
        },
        timeout=20,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()
