from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import request
from zoneinfo import ZoneInfo

ARCHON_API_BASE_URL = os.getenv("ARCHON_API_BASE_URL", "http://archon:8080")
OPENCLAW_BIN = os.getenv("OPENCLAW_BIN", "openclaw")


@dataclass
class OpenClawResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    command: list[str]


def archon_get(path: str) -> dict[str, Any]:
    with request.urlopen(f"{ARCHON_API_BASE_URL}{path}", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def archon_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = request.Request(
        f"{ARCHON_API_BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_openclaw_agent(*, agent: str, message: str, timeout_seconds: int) -> OpenClawResult:
    command = [OPENCLAW_BIN, "agent", "--agent", agent, "--message", message, "--json", "--timeout", str(timeout_seconds)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 15)
    return OpenClawResult(
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=command,
    )


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        return None
    return None


def build_worker_message(task: dict[str, Any]) -> str:
    return (
        "/skill archon-worker\n\n"
        f"Task #{task['id']}: {task['title']}\n\n"
        f"Description:\n{task.get('description', '')}\n\n"
        f"Metadata:\n{json.dumps(task.get('metadata', {}), indent=2)}\n"
    )


def build_review_message(task: dict[str, Any]) -> str:
    return (
        "/skill codex-reviewer\n\n"
        f"Task #{task['id']}: {task['title']}\n\n"
        f"Description:\n{task.get('description', '')}\n\n"
        f"Metadata:\n{json.dumps(task.get('metadata', {}), indent=2)}\n"
    )


def _part_matches(part: str, value: int, minimum: int, maximum: int) -> bool:
    if part == "*":
        return True
    if part.startswith("*/"):
        step = int(part[2:])
        return (value - minimum) % step == 0
    matched = False
    for piece in part.split(","):
        if "-" in piece:
            start_str, end_str = piece.split("-", 1)
            if int(start_str) <= value <= int(end_str):
                matched = True
        elif piece == str(value):
            matched = True
    return matched


def cron_matches(expr: str, current: datetime, timezone_name: str) -> bool:
    local = current.astimezone(ZoneInfo(timezone_name))
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"unsupported cron expression: {expr}")
    minute, hour, day, month, weekday = parts
    weekday_value = (local.weekday() + 1) % 7
    return all(
        [
            _part_matches(minute, local.minute, 0, 59),
            _part_matches(hour, local.hour, 0, 23),
            _part_matches(day, local.day, 1, 31),
            _part_matches(month, local.month, 1, 12),
            _part_matches(weekday, weekday_value, 0, 6),
        ]
    )
