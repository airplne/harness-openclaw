from __future__ import annotations

import json
import os
import re
import subprocess
import time
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request
from zoneinfo import ZoneInfo

ARCHON_API_BASE_URL = os.getenv("ARCHON_API_BASE_URL", "http://archon:8080")
ARCHON_API_TOKEN = os.getenv("ARCHON_API_TOKEN", "")
ARCHON_API_TOKEN_FILE = os.getenv("ARCHON_API_TOKEN_FILE", "")
ARCHON_AUTH_CONFIG_FILE = os.getenv("ARCHON_AUTH_CONFIG_FILE", "/home/node/.openclaw/archon-auth.json")
ARCHON_HTTP_RETRIES = max(1, int(os.getenv("ARCHON_HTTP_RETRIES", "4")))
ARCHON_HTTP_RETRY_BACKOFF = float(os.getenv("ARCHON_HTTP_RETRY_BACKOFF", "0.35"))
OPENCLAW_BIN = os.getenv("OPENCLAW_BIN", "openclaw")
OPENCLAW_STATE_DIR = Path(os.getenv("OPENCLAW_STATE_DIR", "/home/node/.openclaw"))
OPENCLAW_WORKSPACE_DIR = os.getenv("OPENCLAW_WORKSPACE_DIR", "/workspace")
REQUEST_ID_HEADER = "X-Request-ID"


@dataclass
class OpenClawResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    command: list[str]


@dataclass(frozen=True)
class RunnerPrincipal:
    key_id: str
    identity: str
    scopes: frozenset[str]


def _load_archon_token() -> str:
    if ARCHON_API_TOKEN:
        return ARCHON_API_TOKEN.strip()
    if ARCHON_API_TOKEN_FILE:
        try:
            return Path(ARCHON_API_TOKEN_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def _archon_headers() -> dict[str, str]:
    token = _load_archon_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def create_request_id(value: str | None = None) -> str:
    if value:
        text = value.strip()
        if text and len(text) <= 128 and not any(ch.isspace() for ch in text):
            return text
    return uuid.uuid4().hex


def _archon_should_retry(exc: BaseException, status: int | None) -> bool:
    if status is not None:
        return status in {408, 425, 429, 500, 502, 503, 504}
    return isinstance(exc, (urlerror.URLError, TimeoutError, OSError))


def _archon_request(
    *,
    method: str,
    path: str,
    body: bytes | None,
    timeout: float,
    request_id: str | None = None,
) -> dict[str, Any]:
    last_exc: BaseException | None = None
    for attempt in range(ARCHON_HTTP_RETRIES):
        req = request.Request(
            f"{ARCHON_API_BASE_URL}{path}",
            data=body,
            headers=(
                {REQUEST_ID_HEADER: create_request_id(request_id), "Content-Type": "application/json", **_archon_headers()}
                if body is not None
                else {REQUEST_ID_HEADER: create_request_id(request_id), **_archon_headers()}
            ),
            method=method,
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urlerror.HTTPError as exc:
            last_exc = exc
            status = exc.code
            if not _archon_should_retry(exc, status) or attempt == ARCHON_HTTP_RETRIES - 1:
                raise
        except Exception as exc:  # noqa: BLE001 — retry boundary for transient network failures
            last_exc = exc
            if not _archon_should_retry(exc, None) or attempt == ARCHON_HTTP_RETRIES - 1:
                raise
        delay = ARCHON_HTTP_RETRY_BACKOFF * (2**attempt)
        time.sleep(delay)
    raise RuntimeError(f"archon request failed after {ARCHON_HTTP_RETRIES} attempts: {last_exc!r}")


def archon_get(path: str, *, request_id: str | None = None) -> dict[str, Any]:
    return _archon_request(method="GET", path=path, body=None, timeout=30.0, request_id=request_id)


def archon_post(path: str, payload: dict[str, Any], *, request_id: str | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    return _archon_request(method="POST", path=path, body=body, timeout=60.0, request_id=request_id)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_auth_config() -> dict[str, Any]:
    path = Path(ARCHON_AUTH_CONFIG_FILE)
    if not path.exists():
        raise RuntimeError(f"auth config missing: {path}")
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise RuntimeError(f"auth config empty: {path}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("auth config must be a JSON object")
    credentials = payload.get("credentials")
    if not isinstance(credentials, list):
        raise RuntimeError("auth config missing credentials list")
    return payload


def authenticate_runner_bearer(token: str, *, allowed_identity: str) -> RunnerPrincipal:
    if not token:
        raise RuntimeError("missing bearer token")
    payload = _load_auth_config()
    hashed = _hash_token(token)
    for item in payload.get("credentials", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("token_hash") or "") != hashed:
            continue
        if str(item.get("state") or "") not in {"active", "next"}:
            raise RuntimeError("token not active")
        identity = str(item.get("identity") or "")
        if identity != allowed_identity:
            raise RuntimeError(f"identity mismatch: {identity}")
        scopes = item.get("scopes") or []
        return RunnerPrincipal(
            key_id=str(item.get("key_id") or ""),
            identity=identity,
            scopes=frozenset(scope for scope in scopes if isinstance(scope, str)),
        )
    raise RuntimeError("invalid token")


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


def inspect_auth_profiles(provider_filter: set[str] | None = None) -> dict[str, Any]:
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
    scoped_providers = sorted(provider_filter or set())
    if provider_filter:
        scoped_profiles = [item for item in profiles if item.get("provider") in provider_filter]
        other_profiles = [item for item in profiles if item.get("provider") not in provider_filter]
    else:
        scoped_profiles = profiles
        other_profiles = []
    manual = [item for item in scoped_profiles if item.get("type") in {"api_key", "token"}]
    oauth = [item for item in scoped_profiles if item.get("type") == "oauth"]
    return {
        "profiles": profiles,
        "scoped_providers": scoped_providers,
        "scoped_profiles": scoped_profiles,
        "other_profiles": other_profiles,
        "manual_credentials": manual,
        "oauth_profiles": oauth,
        "oauth_providers": sorted({str(item.get("provider")) for item in oauth if item.get("provider")}),
    }


def assert_runtime_ready(
    *,
    agent_id: str,
    expected_model: str,
    auth_providers_to_validate: set[str] | None = None,
    required_oauth_provider: str | None = None,
) -> dict[str, Any]:
    actual_model = resolve_agent_model(agent_id)
    if actual_model != expected_model:
        raise RuntimeError(f"{agent_id} model drift: {actual_model!r} != {expected_model!r}")

    governed_providers = set(auth_providers_to_validate or set())
    if required_oauth_provider:
        governed_providers.add(required_oauth_provider)

    result: dict[str, Any] = {
        "agent_id": agent_id,
        "model": actual_model,
        "auth_policy": {
            "mode": "provider_scoped" if governed_providers else "model_only",
            "providers": sorted(governed_providers),
            "required_oauth_provider": required_oauth_provider,
        },
    }

    if governed_providers:
        auth_state = inspect_auth_profiles(governed_providers)
        if auth_state["manual_credentials"]:
            raise RuntimeError(
                "manual provider credentials remain for governed providers: "
                + ", ".join(sorted(governed_providers))
            )
        if required_oauth_provider and required_oauth_provider not in auth_state["oauth_providers"]:
            raise RuntimeError(f"missing OAuth profile for {required_oauth_provider}")
        result["auth_state"] = auth_state

    return result


def build_runtime_diagnostics(
    *,
    agent_id: str,
    expected_model: str,
    auth_providers_to_validate: set[str] | None = None,
    required_oauth_provider: str | None = None,
) -> dict[str, Any]:
    try:
        state = assert_runtime_ready(
            agent_id=agent_id,
            expected_model=expected_model,
            auth_providers_to_validate=auth_providers_to_validate,
            required_oauth_provider=required_oauth_provider,
        )
        return {"ok": True, **state}
    except Exception as exc:
        diagnostics = {
            "ok": False,
            "agent_id": agent_id,
            "expected_model": expected_model,
            "error": str(exc),
            "actual_model": resolve_agent_model(agent_id),
            "auth_policy": {
                "mode": "provider_scoped" if (auth_providers_to_validate or required_oauth_provider) else "model_only",
                "providers": sorted(set(auth_providers_to_validate or set()) | ({required_oauth_provider} if required_oauth_provider else set())),
                "required_oauth_provider": required_oauth_provider,
            },
        }
        if auth_providers_to_validate or required_oauth_provider:
            diagnostics["auth_state"] = inspect_auth_profiles(set(auth_providers_to_validate or set()) | ({required_oauth_provider} if required_oauth_provider else set()))
        return diagnostics


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
