from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request
from zoneinfo import ZoneInfo

ARCHON_API_BASE_URL = os.getenv("ARCHON_API_BASE_URL", "http://archon:8080")
OPENCLAW_BIN = os.getenv("OPENCLAW_BIN", "openclaw")
OPENCLAW_STATE_DIR = Path(os.getenv("OPENCLAW_STATE_DIR", "/home/node/.openclaw"))
OPENCLAW_WORKSPACE_DIR = os.getenv("OPENCLAW_WORKSPACE_DIR", "/workspace")


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


def run_openclaw_command(*args: str, timeout_seconds: int) -> OpenClawResult:
    command = [OPENCLAW_BIN, *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 15,
        cwd=OPENCLAW_WORKSPACE_DIR,
    )
    return OpenClawResult(
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=command,
    )


def run_openclaw_agent(*, agent: str, message: str, timeout_seconds: int) -> OpenClawResult:
    return run_openclaw_command(
        "agent",
        "--agent",
        agent,
        "--message",
        message,
        "--json",
        "--timeout",
        str(timeout_seconds),
        timeout_seconds=timeout_seconds,
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


def load_agents() -> list[dict[str, Any]]:
    result = run_openclaw_command("agents", "list", "--json", timeout_seconds=30)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or "failed to inspect agents")
    payload = extract_first_json_object(result.stdout) or {}
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def resolve_agent_model(agent_id: str) -> str | None:
    for item in load_agents():
        if item.get("id") == agent_id:
            model = item.get("model")
            return str(model) if model is not None else None
    return None


def inspect_auth_profiles() -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    if OPENCLAW_STATE_DIR.exists():
        for path in OPENCLAW_STATE_DIR.rglob("auth-profiles.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                for profile_id, credential in data.items():
                    if not isinstance(credential, dict):
                        continue
                    profiles.append(
                        {
                            "path": str(path),
                            "profile_id": str(profile_id),
                            "provider": credential.get("provider"),
                            "type": credential.get("type"),
                        }
                    )
    manual = [item for item in profiles if item.get("type") in {"api_key", "token"}]
    oauth = [item for item in profiles if item.get("type") == "oauth"]
    return {
        "profiles": profiles,
        "manual_credentials": manual,
        "oauth_profiles": oauth,
        "oauth_providers": sorted({str(item.get("provider")) for item in oauth if item.get("provider")}),
    }


def assert_runtime_ready(*, agent_id: str, expected_model: str, required_oauth_provider: str | None = None) -> dict[str, Any]:
    auth_state = inspect_auth_profiles()
    actual_model = resolve_agent_model(agent_id)
    if actual_model != expected_model:
        raise RuntimeError(f"{agent_id} model drift: {actual_model!r} != {expected_model!r}")
    manual = auth_state["manual_credentials"]
    if manual:
        raise RuntimeError("manual provider credentials remain in auth profiles")
    if required_oauth_provider and required_oauth_provider not in auth_state["oauth_providers"]:
        raise RuntimeError(f"missing OAuth profile for {required_oauth_provider}")
    return {"agent_id": agent_id, "model": actual_model, "auth_state": auth_state}


def build_runtime_diagnostics(*, agent_id: str, expected_model: str, required_oauth_provider: str | None = None) -> dict[str, Any]:
    try:
        state = assert_runtime_ready(
            agent_id=agent_id,
            expected_model=expected_model,
            required_oauth_provider=required_oauth_provider,
        )
        return {"ok": True, **state}
    except Exception as exc:
        return {
            "ok": False,
            "agent_id": agent_id,
            "expected_model": expected_model,
            "error": str(exc),
            "auth_state": inspect_auth_profiles(),
            "actual_model": resolve_agent_model(agent_id),
        }


def _latest_worker_run(task_id: int) -> dict[str, Any] | None:
    items = archon_get(f"/worker-runs?task_id={task_id}").get("items", [])
    return items[0] if items else None


def _latest_review_run(task_id: int) -> dict[str, Any] | None:
    items = archon_get(f"/reviews?task_id={task_id}").get("items", [])
    return items[0] if items else None


def build_worker_message(task: dict[str, Any]) -> str:
    task_id = int(task["id"])
    latest_review = _latest_review_run(task_id)
    sections = [
        "/skill archon-worker",
        "",
        f"Task #{task_id}: {task['title']}",
        "",
        "Description:",
        task.get("description", ""),
        "",
        "Metadata:",
        json.dumps(task.get("metadata", {}), indent=2),
    ]
    if latest_review:
        sections.extend(
            [
                "",
                "Latest review summary:",
                str(latest_review.get("summary") or ""),
                "",
                "Latest review findings:",
                json.dumps(latest_review.get("findings") or [], indent=2),
                "",
                "Latest review follow-up:",
                json.dumps(latest_review.get("follow_up") or [], indent=2),
            ]
        )
    sections.append("")
    return "\n".join(sections)


def build_review_message(task: dict[str, Any]) -> str:
    task_id = int(task["id"])
    latest_worker = _latest_worker_run(task_id)
    prior_review = _latest_review_run(task_id)
    sections = [
        "/skill codex-reviewer",
        "",
        f"Task #{task_id}: {task['title']}",
        "",
        "Description:",
        task.get("description", ""),
        "",
        "Metadata:",
        json.dumps(task.get("metadata", {}), indent=2),
    ]
    if latest_worker:
        sections.extend(
            [
                "",
                "Latest worker summary:",
                str(latest_worker.get("summary") or ""),
                "",
                "Latest worker artifacts:",
                json.dumps(latest_worker.get("artifacts") or [], indent=2),
                "",
                "Latest worker follow-up:",
                json.dumps(latest_worker.get("follow_up") or [], indent=2),
            ]
        )
    if prior_review:
        sections.extend(
            [
                "",
                "Prior review findings:",
                json.dumps(prior_review.get("findings") or [], indent=2),
                "",
                "Prior review follow-up:",
                json.dumps(prior_review.get("follow_up") or [], indent=2),
            ]
        )
    sections.append("")
    return "\n".join(sections)


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
