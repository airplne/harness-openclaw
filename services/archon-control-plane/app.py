from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib import request as urlrequest

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("ARCHON_DB_PATH", "/data/archon.sqlite3"))
REQUIRE_HUMAN_APPROVAL = os.getenv("ARCHON_REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"
WORKER_API_URL = os.getenv("ARCHON_WORKER_API_URL", "")
REVIEWER_API_URL = os.getenv("ARCHON_REVIEWER_API_URL", "")
ARCHON_AUTH_CONFIG_FILE = os.getenv("ARCHON_AUTH_CONFIG_FILE", "/openclaw-config/archon-auth.json").strip()
ARCHON_AUTH_REQUIRED = os.getenv("ARCHON_AUTH_REQUIRED", "true").lower() == "true"
ARCHON_ALLOW_INSECURE_DEV = os.getenv("ARCHON_ALLOW_INSECURE_DEV", "false").lower() == "true"
ARCHON_ENVIRONMENT = os.getenv("ARCHON_ENVIRONMENT", "dev").strip().lower() or "dev"
ARCHON_RUNNER_TOKEN_FILE = os.getenv("ARCHON_RUNNER_TOKEN_FILE", "").strip()
ARCHON_RATE_LIMIT_PER_MINUTE = int(os.getenv("ARCHON_RATE_LIMIT_PER_MINUTE", "120"))
ARCHON_RETENTION_DAYS = int(os.getenv("ARCHON_RETENTION_DAYS", "0"))
ARCHON_RAW_OUTPUT_MODE = os.getenv("ARCHON_RAW_OUTPUT_MODE", "discard").strip().lower() or "discard"
ARCHON_EXISTING_RAW_OUTPUT_POLICY = os.getenv("ARCHON_EXISTING_RAW_OUTPUT_POLICY", "redact").strip().lower() or "redact"
ARCHON_AUDIT_DEGRADED_MODE = os.getenv("ARCHON_AUDIT_DEGRADED_MODE", "false").lower() == "true"

REQUEST_ID_HEADER = "X-Request-ID"
MCP_SCOPE_HEADER = "X-Archon-Tool-Scope"
PUBLIC_PATHS = {"/health", "/healthz", "/readyz"}

_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW_SEC = 60.0

TaskStatus = Literal[
    "queued",
    "working",
    "review_requested",
    "reviewing",
    "pending_human_approval",
    "approved",
    "needs_changes",
    "rejected",
    "failed",
]
ApprovalDecision = Literal["approved", "needs_changes", "rejected"]
WorkerRunStatus = Literal["completed", "blocked", "failed"]
ReviewStatus = Literal["approved", "needs_changes", "rejected", "pending_human_approval", "failed"]
RunKind = Literal["worker", "review"]
Identity = Literal["operator", "worker", "reviewer", "mcp", "readonly", "archon"]

ALLOWED_IDENTITIES: set[str] = {"operator", "worker", "reviewer", "mcp", "readonly", "archon"}
ALLOWED_CREDENTIAL_STATES: set[str] = {"active", "next", "retired", "revoked"}
ALLOWED_RAW_OUTPUT_MODES: set[str] = {"discard", "store"}

MCP_TOOL_TO_ROUTE_SCOPE = {
    "archon_create_task": ("tasks:create",),
    "archon_list_tasks": ("tasks:read",),
    "archon_transition_task": ("tasks:transition:mcp",),
    "archon_record_review": ("reviews:create:mcp",),
    "archon_request_approval": ("approvals:create:mcp",),
}

WORKER_DIRECT_TRANSITIONS = {("queued", "working"), ("needs_changes", "working")}
REVIEWER_DIRECT_TRANSITIONS = {("review_requested", "reviewing")}
MCP_DIRECT_TRANSITIONS = {("failed", "needs_changes"), ("rejected", "queued"), ("needs_changes", "queued")}

RUN_STATUS_BY_KIND: dict[str, set[str]] = {
    "worker": {"review_requested", "needs_changes", "failed"},
    "review": {"approved", "needs_changes", "rejected", "pending_human_approval", "failed"},
}

app = FastAPI(title="Archon Control Plane", version="0.4.0")


@dataclass(frozen=True)
class CredentialRecord:
    key_id: str
    identity: str
    scopes: frozenset[str]
    state: str
    token_hash: str


@dataclass(frozen=True)
class AuthStore:
    path: Path
    credentials_by_hash: dict[str, CredentialRecord]
    by_identity: dict[str, list[CredentialRecord]]
    version: int


@dataclass(frozen=True)
class Principal:
    key_id: str
    identity: str
    scopes: frozenset[str]
    state: str


class TaskIn(BaseModel):
    title: str
    description: str = ""
    status: TaskStatus = "queued"
    source: str = "manual"
    external_id: str | None = None
    assignee: str | None = None
    review_after: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    assignee: str | None = None
    review_after: str | None = None
    metadata: dict[str, Any] | None = None
    last_error: str | None = None


class TransitionIn(BaseModel):
    status: TaskStatus
    notes: str | None = None


class ClaimIn(BaseModel):
    kind: RunKind
    owner: str | None = None
    ttl_seconds: int = 600
    eligible_statuses: list[TaskStatus] = Field(default_factory=list)


class ReleaseIn(BaseModel):
    owner: str | None = None
    status: TaskStatus | None = None
    last_error: str | None = None


class ApprovalIn(BaseModel):
    task_id: int
    decision: ApprovalDecision
    reviewer: str | None = None
    notes: str | None = None


class WorkerRunIn(BaseModel):
    task_id: int
    agent: str
    model: str
    status: WorkerRunStatus
    summary: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    follow_up: list[str] = Field(default_factory=list)
    raw_output: str | None = None


class ReviewIn(BaseModel):
    task_id: int
    review_queue: str = "codex-review"
    agent: str = "codex-reviewer"
    model: str = "openai-codex/gpt-5.4"
    status: ReviewStatus
    summary: str | None = None
    findings: list[str] = Field(default_factory=list)
    follow_up: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True
    raw_output: str | None = None


_REDACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(authorization)\s*:\s*bearer\s+[^\s]+"),
    re.compile(r"(?i)\b(x-archon-token)\s*:\s*[^\s]+"),
    re.compile(r"(?i)\b([a-z0-9_]*(api[_-]?key|token|secret))\s*=\s*([^\s]+)"),
    re.compile(r"(?i)\b(aws_secret_access_key|aws_access_key_id)\s*=\s*[^\s]+"),
    re.compile(r"(?i)\bghp_[a-z0-9]{20,}\b"),
    re.compile(r"(?i)\bgho_[a-z0-9]{20,}\b"),
    re.compile(r"(?i)\bxox[baprs]-[a-z0-9-]+\b"),
    re.compile(r"\beyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
]


def _redact_text(value: str | None) -> str | None:
    if not value:
        return value
    text = value
    for pat in _REDACT_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_request_id() -> str:
    return uuid.uuid4().hex


def _canonical_request_id(value: str | None) -> str:
    if not value:
        return _generate_request_id()
    text = value.strip()
    if not text or len(text) > 128 or any(ch.isspace() for ch in text):
        return _generate_request_id()
    return text


def _rate_limit_check(client_host: str) -> None:
    if ARCHON_RATE_LIMIT_PER_MINUTE <= 0:
        return
    now = time.monotonic()
    bucket = _RATE_BUCKETS[client_host]
    while bucket and now - bucket[0] > _RATE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= ARCHON_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    bucket.append(now)


@contextmanager
def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                assignee TEXT,
                review_after TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                claim_kind TEXT,
                claim_owner TEXT,
                claim_until TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS worker_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                agent TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                artifacts_json TEXT NOT NULL DEFAULT '[]',
                follow_up_json TEXT NOT NULL DEFAULT '[]',
                raw_output TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                review_queue TEXT NOT NULL,
                agent TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                findings_json TEXT NOT NULL DEFAULT '[]',
                follow_up_json TEXT NOT NULL DEFAULT '[]',
                requires_human_approval INTEGER NOT NULL DEFAULT 1,
                raw_output TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reviewer TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                request_id TEXT NOT NULL,
                principal_key_id TEXT,
                principal_identity TEXT,
                action TEXT NOT NULL,
                path TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                outcome TEXT NOT NULL,
                reason TEXT,
                status_code INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status_updated_at ON tasks(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_claim_owner_until ON tasks(claim_owner, claim_until);
            CREATE INDEX IF NOT EXISTS idx_worker_runs_task_created_at ON worker_runs(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_reviews_task_created_at ON reviews(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_approvals_task_created_at ON approvals(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_events_request_id ON audit_events(request_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_action_created_at ON audit_events(action, created_at);
            """
        )
        row = conn.execute("SELECT version FROM schema_version ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version, updated_at) VALUES (?, ?)", (1, utcnow()))


def _load_runner_token() -> str:
    if not ARCHON_RUNNER_TOKEN_FILE:
        return ""
    try:
        return Path(ARCHON_RUNNER_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _load_auth_store() -> AuthStore:
    if not ARCHON_AUTH_REQUIRED:
        if ARCHON_ALLOW_INSECURE_DEV and ARCHON_ENVIRONMENT == "dev":
            return AuthStore(path=Path(ARCHON_AUTH_CONFIG_FILE), credentials_by_hash={}, by_identity={}, version=1)
        raise RuntimeError("archon auth is disabled but insecure dev mode is not allowed")
    if not ARCHON_AUTH_CONFIG_FILE:
        raise RuntimeError("ARCHON_AUTH_CONFIG_FILE is required")
    path = Path(ARCHON_AUTH_CONFIG_FILE)
    if not path.exists():
        raise RuntimeError(f"auth config missing: {path}")
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise RuntimeError(f"auth config empty: {path}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("auth config must be a JSON object")
    version = int(payload.get("version", 1))
    credentials = payload.get("credentials")
    if not isinstance(credentials, list) or not credentials:
        raise RuntimeError("auth config must define non-empty credentials")
    by_hash: dict[str, CredentialRecord] = {}
    by_identity: dict[str, list[CredentialRecord]] = defaultdict(list)
    for item in credentials:
        if not isinstance(item, dict):
            raise RuntimeError("credential entry must be an object")
        key_id = str(item.get("key_id") or "").strip()
        identity = str(item.get("identity") or "").strip()
        state = str(item.get("state") or "").strip()
        token_hash = str(item.get("token_hash") or "").strip()
        scopes = item.get("scopes")
        if not key_id or not identity or not state or not token_hash:
            raise RuntimeError("credential entry missing key_id, identity, state, or token_hash")
        if identity not in ALLOWED_IDENTITIES:
            raise RuntimeError(f"unsupported identity: {identity}")
        if state not in ALLOWED_CREDENTIAL_STATES:
            raise RuntimeError(f"unsupported credential state: {state}")
        if not isinstance(scopes, list) or not all(isinstance(scope, str) and scope for scope in scopes):
            raise RuntimeError(f"credential scopes invalid for {key_id}")
        record = CredentialRecord(
            key_id=key_id,
            identity=identity,
            scopes=frozenset(scopes),
            state=state,
            token_hash=token_hash,
        )
        if token_hash in by_hash:
            raise RuntimeError(f"duplicate token hash for {key_id}")
        by_hash[token_hash] = record
        by_identity[identity].append(record)
    required_identities = {"operator", "worker", "reviewer", "mcp", "archon"}
    for identity in required_identities:
        records = by_identity.get(identity, [])
        if not any(record.state in {"active", "next"} for record in records):
            raise RuntimeError(f"identity {identity} has no active/next credential")
    return AuthStore(path=path, credentials_by_hash=by_hash, by_identity=dict(by_identity), version=version)


def _apply_existing_raw_output_policy() -> None:
    if ARCHON_EXISTING_RAW_OUTPUT_POLICY not in {"redact", "purge", "preserve"}:
        raise RuntimeError(f"unsupported ARCHON_EXISTING_RAW_OUTPUT_POLICY: {ARCHON_EXISTING_RAW_OUTPUT_POLICY}")
    with db() as conn:
        if ARCHON_EXISTING_RAW_OUTPUT_POLICY == "redact":
            conn.execute(
                "UPDATE worker_runs SET raw_output = ? WHERE raw_output IS NOT NULL",
                ("[REDACTED BY STARTUP POLICY]",),
            )
            conn.execute(
                "UPDATE reviews SET raw_output = ? WHERE raw_output IS NOT NULL",
                ("[REDACTED BY STARTUP POLICY]",),
            )
        elif ARCHON_EXISTING_RAW_OUTPUT_POLICY == "purge":
            conn.execute("UPDATE worker_runs SET raw_output = NULL WHERE raw_output IS NOT NULL")
            conn.execute("UPDATE reviews SET raw_output = NULL WHERE raw_output IS NOT NULL")


def purge_old_data() -> None:
    if ARCHON_RETENTION_DAYS <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ARCHON_RETENTION_DAYS)).isoformat()
    with db() as conn:
        conn.execute(
            "DELETE FROM approvals WHERE task_id IN (SELECT id FROM tasks WHERE created_at < ?)",
            (cutoff,),
        )
        conn.execute(
            "DELETE FROM worker_runs WHERE task_id IN (SELECT id FROM tasks WHERE created_at < ?)",
            (cutoff,),
        )
        conn.execute(
            "DELETE FROM reviews WHERE task_id IN (SELECT id FROM tasks WHERE created_at < ?)",
            (cutoff,),
        )
        conn.execute("DELETE FROM tasks WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff,))


def _retention_loop() -> None:
    while True:
        time.sleep(3600)
        try:
            purge_old_data()
        except Exception:
            app.state.audit_degraded_reason = "retention_loop_failed"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return principal


def _audit_event(
    conn: sqlite3.Connection,
    request: Request,
    *,
    action: str,
    outcome: str,
    status_code: int,
    reason: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    principal = getattr(request.state, "principal", None)
    conn.execute(
        """
        INSERT INTO audit_events (
            created_at, request_id, principal_key_id, principal_identity, action, path,
            target_type, target_id, outcome, reason, status_code, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utcnow(),
            getattr(request.state, "request_id", _generate_request_id()),
            principal.key_id if principal else None,
            principal.identity if principal else None,
            action,
            request.url.path,
            target_type,
            str(target_id) if target_id is not None else None,
            outcome,
            reason,
            status_code,
            json.dumps(metadata or {}),
        ),
    )


def _audit_or_raise(
    conn: sqlite3.Connection,
    request: Request,
    *,
    action: str,
    outcome: str,
    status_code: int,
    reason: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        _audit_event(
            conn,
            request,
            action=action,
            outcome=outcome,
            status_code=status_code,
            reason=reason,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        app.state.audit_degraded_reason = f"audit_failure:{exc}"
        if not ARCHON_AUDIT_DEGRADED_MODE:
            raise HTTPException(status_code=503, detail="audit unavailable") from exc


def _audit_best_effort_denial(
    request: Request,
    *,
    action: str,
    status_code: int,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        with db() as conn:
            _audit_event(
                conn,
                request,
                action=action,
                outcome="denied",
                status_code=status_code,
                reason=reason,
                metadata=metadata,
            )
    except Exception:
        app.state.audit_degraded_reason = "audit_denial_failed"


def _audit_denial_before_raise(
    conn: sqlite3.Connection,
    request: Request,
    *,
    action: str,
    status_code: int,
    reason: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _audit_or_raise(
        conn,
        request,
        action=action,
        outcome="denied",
        status_code=status_code,
        reason=reason,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )
    conn.commit()


def _task_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


def _authenticate_request(request: Request) -> Principal | None:
    if request.url.path in PUBLIC_PATHS:
        return None
    if not ARCHON_AUTH_REQUIRED and ARCHON_ALLOW_INSECURE_DEV and ARCHON_ENVIRONMENT == "dev":
        return None
    authz = request.headers.get("authorization", "").strip()
    if not authz.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authz.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    record = app.state.auth_store.credentials_by_hash.get(_hash_token(token))
    if record is None:
        raise HTTPException(status_code=401, detail="invalid token")
    if record.state == "revoked" or record.state == "retired":
        raise HTTPException(status_code=401, detail="token not active")
    return Principal(key_id=record.key_id, identity=record.identity, scopes=record.scopes, state=record.state)


def _require_scope(
    request: Request,
    *,
    scope: str,
    action: str,
    mcp_tool: str | None = None,
) -> Principal:
    principal = _principal(request)
    if scope not in principal.scopes:
        _audit_best_effort_denial(request, action=action, status_code=403, reason=f"missing_scope:{scope}")
        raise HTTPException(status_code=403, detail="forbidden")
    if principal.identity == "mcp":
        supplied_tool = request.headers.get(MCP_SCOPE_HEADER, "").strip()
        if not mcp_tool or supplied_tool != mcp_tool or f"mcp:{mcp_tool}" not in principal.scopes:
            _audit_best_effort_denial(
                request,
                action=action,
                status_code=403,
                reason="mcp_tool_scope_required",
                metadata={"supplied_tool": supplied_tool, "required_tool": mcp_tool},
            )
            raise HTTPException(status_code=403, detail="forbidden")
    return principal


def _require_identity(request: Request, *, identities: set[str], action: str) -> Principal:
    principal = _principal(request)
    if principal.identity not in identities:
        _audit_best_effort_denial(
            request,
            action=action,
            status_code=403,
            reason=f"identity_not_allowed:{principal.identity}",
        )
        raise HTTPException(status_code=403, detail="forbidden")
    return principal


def _current_task(conn: sqlite3.Connection, task_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_row_to_dict(row)


def _validate_claim_request(principal: Principal, payload: ClaimIn) -> None:
    if payload.owner and payload.owner != principal.key_id:
        raise HTTPException(status_code=403, detail="claim owner mismatch")
    if principal.identity == "worker":
        if payload.kind != "worker":
            raise HTTPException(status_code=403, detail="worker may only claim worker tasks")
        if set(payload.eligible_statuses) - {"queued", "needs_changes"}:
            raise HTTPException(status_code=403, detail="worker claim statuses invalid")
    elif principal.identity == "reviewer":
        if payload.kind != "review":
            raise HTTPException(status_code=403, detail="reviewer may only claim review tasks")
        if set(payload.eligible_statuses) - {"review_requested"}:
            raise HTTPException(status_code=403, detail="reviewer claim statuses invalid")
    else:
        raise HTTPException(status_code=403, detail="identity may not claim tasks")


def _validate_release_request(principal: Principal, task: dict[str, Any], payload: ReleaseIn) -> None:
    if payload.owner and payload.owner != principal.key_id:
        raise HTTPException(status_code=403, detail="release owner mismatch")
    if task.get("claim_owner") != principal.key_id:
        raise HTTPException(status_code=409, detail="task claimed by another identity")
    expected_kind = "worker" if principal.identity == "worker" else "review" if principal.identity == "reviewer" else None
    if task.get("claim_kind") != expected_kind:
        raise HTTPException(status_code=403, detail="claim kind mismatch")


def _validate_transition(principal: Principal, task: dict[str, Any], target: str, request: Request) -> None:
    current = str(task["status"])
    if principal.identity == "worker":
        if task.get("claim_owner") != principal.key_id or task.get("claim_kind") != "worker":
            raise HTTPException(status_code=403, detail="worker does not own active claim")
        if (current, target) not in WORKER_DIRECT_TRANSITIONS:
            raise HTTPException(status_code=403, detail="worker transition forbidden")
    elif principal.identity == "reviewer":
        if task.get("claim_owner") != principal.key_id or task.get("claim_kind") != "review":
            raise HTTPException(status_code=403, detail="reviewer does not own active claim")
        if (current, target) not in REVIEWER_DIRECT_TRANSITIONS:
            raise HTTPException(status_code=403, detail="reviewer transition forbidden")
    elif principal.identity == "operator":
        return
    elif principal.identity == "mcp":
        _require_scope(request, scope="tasks:transition:mcp", action="task.transition", mcp_tool="archon_transition_task")
        if (current, target) not in MCP_DIRECT_TRANSITIONS:
            raise HTTPException(status_code=403, detail="mcp transition forbidden")
    else:
        raise HTTPException(status_code=403, detail="transition forbidden")


def _raw_output_policy_decision(raw_output: str | None) -> tuple[str, str | None]:
    if ARCHON_RAW_OUTPUT_MODE not in ALLOWED_RAW_OUTPUT_MODES:
        raise RuntimeError(f"unsupported raw output mode: {ARCHON_RAW_OUTPUT_MODE}")
    if raw_output is None:
        return "absent", None
    if ARCHON_RAW_OUTPUT_MODE == "store":
        return "stored", _redact_text(raw_output)
    return "discarded", None


def _runner_headers(request_id: str) -> dict[str, str]:
    token = _load_runner_token()
    if not token:
        raise RuntimeError("ARCHON_RUNNER_TOKEN_FILE missing or unreadable")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}", REQUEST_ID_HEADER: request_id}


def _post_json(url: str, payload: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_runner_headers(request_id),
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request.state.request_id = _canonical_request_id(request.headers.get(REQUEST_ID_HEADER))
    if request.url.path not in PUBLIC_PATHS:
        client_host = request.client.host if request.client else "unknown"
        _rate_limit_check(client_host)
    try:
        request.state.principal = _authenticate_request(request)
    except HTTPException as exc:
        if request.url.path not in PUBLIC_PATHS:
            _audit_best_effort_denial(
                request,
                action=f"http.{request.method.lower()}",
                status_code=exc.status_code,
                reason=exc.detail if isinstance(exc.detail, str) else "unauthorized",
            )
        raise
    response: Response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request.state.request_id
    return response


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    app.state.audit_degraded_reason = None
    app.state.auth_store = _load_auth_store()
    _apply_existing_raw_output_policy()
    purge_old_data()
    if ARCHON_RETENTION_DAYS > 0:
        threading.Thread(target=_retention_loop, daemon=True).start()


@app.get("/health")
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "archon", "timestamp": utcnow()}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    return {
        "ok": app.state.audit_degraded_reason is None,
        "service": "archon",
        "environment": ARCHON_ENVIRONMENT,
        "auth_required": ARCHON_AUTH_REQUIRED,
        "audit_degraded_reason": app.state.audit_degraded_reason,
        "timestamp": utcnow(),
    }


@app.get("/audit")
def list_audit(request: Request, limit: int = 50) -> dict[str, Any]:
    _require_scope(request, scope="audit:read", action="audit.read")
    limit = min(max(limit, 1), 500)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, request_id, principal_key_id, principal_identity,
                   action, path, target_type, target_id, outcome, reason, status_code, metadata_json
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        items.append(item)
    return {"items": items}


@app.get("/tasks")
def list_tasks(request: Request, status: TaskStatus | None = None) -> dict[str, Any]:
    principal = _principal(request)
    if principal.identity == "mcp":
        _require_scope(request, scope="tasks:read", action="tasks.list", mcp_tool="archon_list_tasks")
    else:
        _require_scope(request, scope="tasks:read", action="tasks.list")
    with db() as conn:
        if status:
            rows = conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
    return {"items": [_task_row_to_dict(row) for row in rows]}


@app.get("/tasks/{task_id}")
def get_task(request: Request, task_id: int) -> dict[str, Any]:
    _require_scope(request, scope="tasks:read", action="tasks.get")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_row_to_dict(row)


@app.post("/tasks")
def create_task(request: Request, task: TaskIn) -> dict[str, Any]:
    principal = _principal(request)
    if principal.identity == "mcp":
        _require_scope(request, scope="tasks:create", action="tasks.create", mcp_tool="archon_create_task")
    else:
        _require_scope(request, scope="tasks:create", action="tasks.create")
    now = utcnow()
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (
                external_id, title, description, status, source, assignee,
                review_after, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.external_id,
                task.title,
                task.description,
                task.status,
                task.source,
                task.assignee,
                task.review_after,
                json.dumps(task.metadata),
                now,
                now,
            ),
        )
        _audit_or_raise(
            conn,
            request,
            action="tasks.create",
            outcome="allowed",
            status_code=201,
            target_type="task",
            target_id=cur.lastrowid,
        )
        task_id = cur.lastrowid
    return {"task_id": task_id, "status": task.status, "created_at": now}


@app.patch("/tasks/{task_id}")
def patch_task(request: Request, task_id: int, patch: TaskPatch) -> dict[str, Any]:
    _require_scope(request, scope="tasks:patch", action="tasks.patch")
    now = utcnow()
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        current = _task_row_to_dict(row)
        updated = {
            "title": patch.title or current["title"],
            "description": patch.description if patch.description is not None else current["description"],
            "status": patch.status or current["status"],
            "assignee": patch.assignee if patch.assignee is not None else current["assignee"],
            "review_after": patch.review_after if patch.review_after is not None else current["review_after"],
            "metadata": patch.metadata if patch.metadata is not None else current["metadata"],
            "last_error": patch.last_error if patch.last_error is not None else current.get("last_error"),
        }
        conn.execute(
            """
            UPDATE tasks
            SET title = ?, description = ?, status = ?, assignee = ?, review_after = ?,
                metadata_json = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updated["title"],
                updated["description"],
                updated["status"],
                updated["assignee"],
                updated["review_after"],
                json.dumps(updated["metadata"]),
                updated["last_error"],
                now,
                task_id,
            ),
        )
        _audit_or_raise(conn, request, action="tasks.patch", outcome="allowed", status_code=200, target_type="task", target_id=task_id)
    return {"task_id": task_id, "updated_at": now}


@app.post("/tasks/{task_id}/transition")
def transition_task(request: Request, task_id: int, transition: TransitionIn) -> dict[str, Any]:
    principal = _principal(request)
    if principal.identity == "mcp":
        _require_scope(request, scope="tasks:transition:mcp", action="tasks.transition", mcp_tool="archon_transition_task")
    elif principal.identity == "worker":
        _require_scope(request, scope="tasks:transition:working", action="tasks.transition")
    elif principal.identity == "reviewer":
        _require_scope(request, scope="tasks:transition:reviewing", action="tasks.transition")
    else:
        _require_identity(request, identities={"operator", "worker", "reviewer"}, action="tasks.transition")
    now = utcnow()
    with db() as conn:
        task = _current_task(conn, task_id)
        try:
            _validate_transition(principal, task, transition.status, request)
        except HTTPException as exc:
            _audit_denial_before_raise(
                conn,
                request,
                action="tasks.transition",
                status_code=exc.status_code,
                reason=str(exc.detail),
                target_type="task",
                target_id=task_id,
                metadata={"from": task["status"], "to": transition.status},
            )
            raise
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (transition.status, now, task_id))
        _audit_or_raise(
            conn,
            request,
            action="tasks.transition",
            outcome="allowed",
            status_code=200,
            target_type="task",
            target_id=task_id,
            metadata={"from": task["status"], "to": transition.status},
        )
    return {"task_id": task_id, "status": transition.status, "updated_at": now, "notes": transition.notes}


@app.post("/tasks/claim")
def claim_task(request: Request, payload: ClaimIn) -> dict[str, Any]:
    principal = _require_identity(request, identities={"worker", "reviewer"}, action="tasks.claim")
    if principal.identity == "worker":
        _require_scope(request, scope="tasks:claim:worker", action="tasks.claim")
    else:
        _require_scope(request, scope="tasks:claim:review", action="tasks.claim")
    try:
        _validate_claim_request(principal, payload)
    except HTTPException as exc:
        with db() as conn:
            _audit_denial_before_raise(conn, request, action="tasks.claim", status_code=exc.status_code, reason=str(exc.detail))
        raise
    if not payload.eligible_statuses:
        return {"item": None}
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    claim_until = (now_dt + timedelta(seconds=payload.ttl_seconds)).isoformat()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ({}) ORDER BY updated_at ASC".format(",".join("?" for _ in payload.eligible_statuses)),
            tuple(payload.eligible_statuses),
        ).fetchall()
        for row in rows:
            current_until = _parse_iso(row["claim_until"])
            if row["claim_owner"] and current_until and current_until > now_dt:
                continue
            conn.execute(
                "UPDATE tasks SET claim_kind = ?, claim_owner = ?, claim_until = ?, updated_at = ? WHERE id = ?",
                (payload.kind, principal.key_id, claim_until, now, row["id"]),
            )
            claimed = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            claimed_item = _task_row_to_dict(claimed)
            _audit_or_raise(
                conn,
                request,
                action="tasks.claim",
                outcome="allowed",
                status_code=200,
                target_type="task",
                target_id=claimed_item["id"],
                metadata={"claim_kind": payload.kind, "claim_owner": principal.key_id},
            )
            return {"item": claimed_item}
        _audit_or_raise(conn, request, action="tasks.claim", outcome="allowed", status_code=200, reason="no_task_available")
    return {"item": None}


@app.post("/tasks/{task_id}/release")
def release_task(request: Request, task_id: int, payload: ReleaseIn) -> dict[str, Any]:
    principal = _require_identity(request, identities={"worker", "reviewer"}, action="tasks.release")
    now = utcnow()
    with db() as conn:
        task = _current_task(conn, task_id)
        try:
            _validate_release_request(principal, task, payload)
        except HTTPException as exc:
            _audit_denial_before_raise(
                conn,
                request,
                action="tasks.release",
                status_code=exc.status_code,
                reason=str(exc.detail),
                target_type="task",
                target_id=task_id,
            )
            raise
        new_status = payload.status or task["status"]
        if new_status not in RUN_STATUS_BY_KIND[task["claim_kind"]]:
            _audit_denial_before_raise(
                conn,
                request,
                action="tasks.release",
                status_code=403,
                reason=f"invalid_release_status:{new_status}",
                target_type="task",
                target_id=task_id,
            )
            raise HTTPException(status_code=403, detail="invalid release status")
        conn.execute(
            """
            UPDATE tasks
            SET claim_kind = NULL, claim_owner = NULL, claim_until = NULL,
                status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, payload.last_error, now, task_id),
        )
        _audit_or_raise(conn, request, action="tasks.release", outcome="allowed", status_code=200, target_type="task", target_id=task_id)
    return {"task_id": task_id, "status": new_status, "updated_at": now}


@app.get("/work/queue")
def work_queue(request: Request) -> dict[str, Any]:
    _require_scope(request, scope="tasks:read", action="work.queue")
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status IN ('queued', 'needs_changes') ORDER BY updated_at ASC").fetchall()
    return {"items": [_task_row_to_dict(row) for row in rows]}


@app.get("/reviews/queue")
def review_queue(request: Request) -> dict[str, Any]:
    _require_scope(request, scope="tasks:read", action="reviews.queue")
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status = 'review_requested' ORDER BY updated_at ASC").fetchall()
    return {"items": [_task_row_to_dict(row) for row in rows], "require_human_approval": REQUIRE_HUMAN_APPROVAL}


@app.get("/claims")
def list_claims(request: Request, active_only: bool = True) -> dict[str, Any]:
    _require_scope(request, scope="claims:read", action="claims.list")
    now = utcnow()
    with db() as conn:
        if active_only:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE claim_owner IS NOT NULL
                  AND claim_until IS NOT NULL
                  AND claim_until > ?
                ORDER BY claim_until ASC
                """,
                (now,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks WHERE claim_owner IS NOT NULL ORDER BY updated_at DESC").fetchall()
    items = []
    for row in rows:
        item = _task_row_to_dict(row)
        items.append(
            {
                "task_id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "claim_kind": item["claim_kind"],
                "claim_owner": item["claim_owner"],
                "claim_until": item["claim_until"],
                "updated_at": item["updated_at"],
            }
        )
    return {"items": items}


@app.post("/worker-runs")
def create_worker_run(request: Request, run: WorkerRunIn) -> dict[str, Any]:
    principal = _require_identity(request, identities={"worker"}, action="worker-runs.create")
    _require_scope(request, scope="worker-runs:create", action="worker-runs.create")
    now = utcnow()
    decision, stored_raw = _raw_output_policy_decision(run.raw_output)
    with db() as conn:
        task = _current_task(conn, run.task_id)
        if task["claim_owner"] != principal.key_id or task["claim_kind"] != "worker":
            _audit_denial_before_raise(
                conn,
                request,
                action="worker-runs.create",
                status_code=403,
                reason="worker claim ownership required",
                target_type="task",
                target_id=run.task_id,
            )
            raise HTTPException(status_code=403, detail="worker claim ownership required")
        conn.execute(
            """
            INSERT INTO worker_runs (task_id, agent, model, status, summary, artifacts_json, follow_up_json, raw_output, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.task_id,
                run.agent,
                run.model,
                run.status,
                run.summary,
                json.dumps(run.artifacts),
                json.dumps(run.follow_up),
                stored_raw,
                now,
            ),
        )
        new_status = "review_requested" if run.status == "completed" else "needs_changes" if run.status == "blocked" else "failed"
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, run.task_id))
        _audit_or_raise(
            conn,
            request,
            action="worker-runs.create",
            outcome="allowed",
            status_code=201,
            target_type="task",
            target_id=run.task_id,
            metadata={"raw_output_decision": decision, "new_status": new_status},
        )
    return {"task_id": run.task_id, "status": new_status, "created_at": now}


@app.get("/worker-runs")
def list_worker_runs(request: Request, task_id: int | None = None) -> dict[str, Any]:
    _require_scope(request, scope="worker-runs:read", action="worker-runs.list")
    with db() as conn:
        if task_id is None:
            rows = conn.execute("SELECT * FROM worker_runs ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM worker_runs WHERE task_id = ? ORDER BY created_at DESC", (task_id,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["artifacts"] = json.loads(item.pop("artifacts_json") or "[]")
        item["follow_up"] = json.loads(item.pop("follow_up_json") or "[]")
        item["raw_output"] = _redact_text(item.get("raw_output"))
        items.append(item)
    return {"items": items}


@app.post("/reviews")
def create_review(request: Request, review: ReviewIn) -> dict[str, Any]:
    principal = _principal(request)
    if principal.identity == "mcp":
        _require_scope(request, scope="reviews:create:mcp", action="reviews.create", mcp_tool="archon_record_review")
    else:
        _require_identity(request, identities={"reviewer"}, action="reviews.create")
        _require_scope(request, scope="reviews:create", action="reviews.create")
    now = utcnow()
    decision, stored_raw = _raw_output_policy_decision(review.raw_output)
    with db() as conn:
        task = _current_task(conn, review.task_id)
        if principal.identity == "reviewer" and (task["claim_owner"] != principal.key_id or task["claim_kind"] != "review"):
            _audit_denial_before_raise(
                conn,
                request,
                action="reviews.create",
                status_code=403,
                reason="reviewer claim ownership required",
                target_type="task",
                target_id=review.task_id,
            )
            raise HTTPException(status_code=403, detail="reviewer claim ownership required")
        conn.execute(
            """
            INSERT INTO reviews (
                task_id, review_queue, agent, model, status, summary, findings_json,
                follow_up_json, requires_human_approval, raw_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.task_id,
                review.review_queue,
                review.agent,
                review.model,
                review.status,
                review.summary,
                json.dumps(review.findings),
                json.dumps(review.follow_up),
                1 if review.requires_human_approval else 0,
                stored_raw,
                now,
            ),
        )
        if review.status == "approved" and REQUIRE_HUMAN_APPROVAL and review.requires_human_approval:
            new_status = "pending_human_approval"
        else:
            new_status = review.status
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, review.task_id))
        _audit_or_raise(
            conn,
            request,
            action="reviews.create",
            outcome="allowed",
            status_code=201,
            target_type="task",
            target_id=review.task_id,
            metadata={"raw_output_decision": decision, "new_status": new_status},
        )
    return {"task_id": review.task_id, "status": new_status, "created_at": now}


@app.get("/reviews")
def list_reviews(request: Request, task_id: int | None = None) -> dict[str, Any]:
    _require_scope(request, scope="reviews:read", action="reviews.list")
    with db() as conn:
        if task_id is None:
            rows = conn.execute("SELECT * FROM reviews ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM reviews WHERE task_id = ? ORDER BY created_at DESC", (task_id,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["findings"] = json.loads(item.pop("findings_json") or "[]")
        item["follow_up"] = json.loads(item.pop("follow_up_json") or "[]")
        item["requires_human_approval"] = bool(item["requires_human_approval"])
        item["raw_output"] = _redact_text(item.get("raw_output"))
        items.append(item)
    return {"items": items}


@app.get("/approvals")
def list_approvals(request: Request, task_id: int | None = None) -> dict[str, Any]:
    _require_scope(request, scope="approvals:read", action="approvals.list")
    with db() as conn:
        if task_id is None:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at DESC", (task_id,)).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/approvals")
def create_approval(request: Request, approval: ApprovalIn) -> dict[str, Any]:
    principal = _principal(request)
    if principal.identity == "mcp":
        _require_scope(request, scope="approvals:create:mcp", action="approvals.create", mcp_tool="archon_request_approval")
    else:
        _require_scope(request, scope="approvals:create", action="approvals.create")
    now = utcnow()
    with db() as conn:
        _current_task(conn, approval.task_id)
        conn.execute(
            "INSERT INTO approvals (task_id, decision, reviewer, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (approval.task_id, approval.decision, approval.reviewer or principal.identity, approval.notes, now),
        )
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (approval.decision, now, approval.task_id))
        _audit_or_raise(conn, request, action="approvals.create", outcome="allowed", status_code=201, target_type="task", target_id=approval.task_id)
    return {"task_id": approval.task_id, "decision": approval.decision, "created_at": now}


@app.post("/work/run")
def run_work(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _require_scope(request, scope="work:run", action="work.run")
    if not WORKER_API_URL:
        raise HTTPException(status_code=503, detail="worker api url not configured")
    with db() as conn:
        result = _post_json(f"{WORKER_API_URL}/run-once", payload or {}, request_id=request.state.request_id)
        _audit_or_raise(conn, request, action="work.run", outcome="allowed", status_code=200)
    return result


@app.post("/reviews/run")
def run_review(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _require_scope(request, scope="reviews:run", action="reviews.run")
    if not REVIEWER_API_URL:
        raise HTTPException(status_code=503, detail="reviewer api url not configured")
    with db() as conn:
        result = _post_json(f"{REVIEWER_API_URL}/run-once", payload or {}, request_id=request.state.request_id)
        _audit_or_raise(conn, request, action="reviews.run", outcome="allowed", status_code=200)
    return result
