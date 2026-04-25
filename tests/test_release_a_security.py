from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
ARCHON_APP_PATH = ROOT / "services" / "archon-control-plane" / "app.py"
WORKER_APP_PATH = ROOT / "services" / "openclaw-runtime" / "worker_loop.py"
REVIEWER_APP_PATH = ROOT / "services" / "openclaw-runtime" / "review_loop.py"
RUNTIME_DIR = ROOT / "services" / "openclaw-runtime"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _credential(key_id: str, identity: str, token: str, scopes: list[str], state: str = "active") -> dict[str, object]:
    return {
        "key_id": key_id,
        "identity": identity,
        "state": state,
        "scopes": scopes,
        "token_hash": _hash_token(token),
    }


def write_auth_config(path: Path) -> dict[str, str]:
    tokens = {
        "operator_active": "operator-active-token",
        "operator_next": "operator-next-token",
        "operator_retired": "operator-retired-token",
        "operator_revoked": "operator-revoked-token",
        "worker": "worker-token",
        "reviewer": "reviewer-token",
        "mcp": "mcp-token",
        "readonly": "readonly-token",
        "archon": "archon-token",
    }
    payload = {
        "version": 1,
        "credentials": [
            _credential(
                "operator-active",
                "operator",
                tokens["operator_active"],
                [
                    "tasks:create",
                    "tasks:read",
                    "tasks:patch",
                    "claims:read",
                    "worker-runs:read",
                    "reviews:read",
                    "approvals:read",
                    "approvals:create",
                    "work:run",
                    "reviews:run",
                    "audit:read",
                ],
            ),
            _credential(
                "operator-next",
                "operator",
                tokens["operator_next"],
                [
                    "tasks:create",
                    "tasks:read",
                    "tasks:patch",
                    "claims:read",
                    "worker-runs:read",
                    "reviews:read",
                    "approvals:read",
                    "approvals:create",
                    "work:run",
                    "reviews:run",
                    "audit:read",
                ],
                state="next",
            ),
            _credential("operator-retired", "operator", tokens["operator_retired"], ["tasks:read"], state="retired"),
            _credential("operator-revoked", "operator", tokens["operator_revoked"], ["tasks:read"], state="revoked"),
            _credential(
                "worker-active",
                "worker",
                tokens["worker"],
                [
                    "tasks:read",
                    "tasks:claim:worker",
                    "tasks:transition:working",
                    "worker-runs:create",
                    "worker-runs:read",
                ],
            ),
            _credential(
                "reviewer-active",
                "reviewer",
                tokens["reviewer"],
                [
                    "tasks:read",
                    "tasks:claim:review",
                    "tasks:transition:reviewing",
                    "reviews:create",
                    "reviews:read",
                ],
            ),
            _credential(
                "mcp-active",
                "mcp",
                tokens["mcp"],
                [
                    "tasks:read",
                    "tasks:create",
                    "tasks:transition:mcp",
                    "reviews:create:mcp",
                    "approvals:create:mcp",
                    "mcp:archon_create_task",
                    "mcp:archon_list_tasks",
                    "mcp:archon_transition_task",
                    "mcp:archon_record_review",
                    "mcp:archon_request_approval",
                ],
            ),
            _credential(
                "readonly-active",
                "readonly",
                tokens["readonly"],
                ["tasks:read", "claims:read", "worker-runs:read", "reviews:read", "approvals:read", "audit:read"],
            ),
            _credential("archon-active", "archon", tokens["archon"], ["runner:invoke"]),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return tokens


def load_module(module_path: Path, *, env: dict[str, str], module_name: str, runtime: bool = False):
    old_values = {key: os.environ.get(key) for key in env}
    for key, value in env.items():
        os.environ[key] = value
    if runtime:
        sys.modules.pop("runner_common", None)
        if str(RUNTIME_DIR) not in sys.path:
            sys.path.insert(0, str(RUNTIME_DIR))
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def db_rows(db_path: Path, query: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def make_request(module, path: str, *, token: str | None = None, request_id: str | None = None, extra_headers: dict[str, str] | None = None):
    headers: dict[str, str] = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if request_id:
        headers[module.REQUEST_ID_HEADER] = request_id
    if extra_headers:
        headers.update(extra_headers)
    request = SimpleNamespace(
        headers=headers,
        url=SimpleNamespace(path=path),
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(request_id=module._canonical_request_id(request_id), principal=None),
    )
    request.state.principal = module._authenticate_request(request)
    return request


def make_runner_request(headers: dict[str, str] | None = None):
    return SimpleNamespace(headers=headers or {})


def start_archon(tmp_path: Path, *, raw_output_mode: str = "discard"):
    auth_path = tmp_path / f"auth-{uuid.uuid4().hex}.json"
    db_path = tmp_path / f"archon-{uuid.uuid4().hex}.sqlite3"
    tokens = write_auth_config(auth_path)
    runner_token_path = tmp_path / f"runner-{uuid.uuid4().hex}.token"
    runner_token_path.write_text(tokens["archon"], encoding="utf-8")
    module = load_module(
        ARCHON_APP_PATH,
        env={
            "ARCHON_DB_PATH": str(db_path),
            "ARCHON_AUTH_CONFIG_FILE": str(auth_path),
            "ARCHON_AUTH_REQUIRED": "true",
            "ARCHON_ALLOW_INSECURE_DEV": "false",
            "ARCHON_ENVIRONMENT": "test",
            "ARCHON_RUNNER_TOKEN_FILE": str(runner_token_path),
            "ARCHON_RAW_OUTPUT_MODE": raw_output_mode,
            "ARCHON_EXISTING_RAW_OUTPUT_POLICY": "redact",
            "ARCHON_RATE_LIMIT_PER_MINUTE": "1000",
            "ARCHON_AUDIT_DEGRADED_MODE": "false",
            "ARCHON_WORKER_API_URL": "http://worker:8091",
            "ARCHON_REVIEWER_API_URL": "http://reviewer:8092",
        },
        module_name=f"archon_app_{uuid.uuid4().hex}",
    )
    module.on_startup()
    return module, tokens, db_path


def create_task(module, token: str, *, status: str = "queued", title: str = "task", request_id: str | None = None) -> int:
    request = make_request(module, "/tasks", token=token, request_id=request_id or f"req-{uuid.uuid4().hex}")
    created = module.create_task(
        request,
        module.TaskIn(title=title, description="desc", status=status, source="test"),
    )
    return int(created["task_id"])


def test_archon_fails_closed_for_bad_auth_config(tmp_path: Path) -> None:
    bad_cases = {
        "missing": tmp_path / "missing.json",
        "empty": tmp_path / "empty.json",
        "malformed": tmp_path / "malformed.json",
        "unreadable_dir": tmp_path / "authdir",
    }
    bad_cases["empty"].write_text("", encoding="utf-8")
    bad_cases["malformed"].write_text("{broken", encoding="utf-8")
    bad_cases["unreadable_dir"].mkdir()

    for label, auth_path in bad_cases.items():
        module = load_module(
            ARCHON_APP_PATH,
            env={
                "ARCHON_DB_PATH": str(tmp_path / f"{label}.sqlite3"),
                "ARCHON_AUTH_CONFIG_FILE": str(auth_path),
                "ARCHON_AUTH_REQUIRED": "true",
                "ARCHON_ALLOW_INSECURE_DEV": "false",
                "ARCHON_ENVIRONMENT": "test",
                "ARCHON_RAW_OUTPUT_MODE": "discard",
                "ARCHON_EXISTING_RAW_OUTPUT_POLICY": "redact",
                "ARCHON_RATE_LIMIT_PER_MINUTE": "1000",
                "ARCHON_AUDIT_DEGRADED_MODE": "false",
            },
            module_name=f"archon_bad_auth_{label}_{uuid.uuid4().hex}",
        )
        with pytest.raises(Exception):
            module.on_startup()


def test_credential_lifecycle_and_request_id_audit(tmp_path: Path) -> None:
    module, tokens, _db_path = start_archon(tmp_path)

    task_id = create_task(module, tokens["operator_active"], request_id="req-credential-active")
    assert task_id > 0

    next_task_id = create_task(module, tokens["operator_next"], request_id="req-credential-next")
    assert next_task_id > 0

    with pytest.raises(HTTPException) as readonly_error:
        module.create_task(
            make_request(module, "/tasks", token=tokens["readonly"], request_id="req-readonly"),
            module.TaskIn(title="readonly", description="", status="queued", source="test"),
        )
    assert readonly_error.value.status_code == 403

    for request_id, token in [("req-retired", tokens["operator_retired"]), ("req-revoked", tokens["operator_revoked"])]:
        with pytest.raises(HTTPException) as auth_error:
            make_request(module, "/tasks", token=token, request_id=request_id)
        assert auth_error.value.status_code == 401

    audit = module.list_audit(make_request(module, "/audit", token=tokens["operator_active"]))["items"]
    assert any(item["request_id"] == "req-credential-active" and item["outcome"] == "allowed" for item in audit)
    assert any(item["request_id"] == "req-readonly" and item["outcome"] == "denied" for item in audit)


def test_worker_claim_release_and_transition_matrix(tmp_path: Path) -> None:
    module, tokens, _db_path = start_archon(tmp_path)
    task_id = create_task(module, tokens["operator_active"], title="worker-flow")

    with pytest.raises(HTTPException) as spoof_claim:
        module.claim_task(
            make_request(module, "/tasks/claim", token=tokens["worker"], request_id="req-worker-spoof-claim"),
            module.ClaimIn(kind="worker", owner="spoofed-owner", eligible_statuses=["queued"]),
        )
    assert spoof_claim.value.status_code == 403

    claim = module.claim_task(
        make_request(module, "/tasks/claim", token=tokens["worker"], request_id="req-worker-claim"),
        module.ClaimIn(kind="worker", eligible_statuses=["queued"]),
    )
    assert claim["item"]["id"] == task_id
    assert claim["item"]["claim_owner"] == "worker-active"

    for forbidden in ("approved", "rejected", "pending_human_approval"):
        with pytest.raises(HTTPException) as transition_error:
            module.transition_task(
                make_request(module, f"/tasks/{task_id}/transition", token=tokens["worker"], request_id=f"req-worker-{forbidden}"),
                task_id,
                module.TransitionIn(status=forbidden),
            )
        assert transition_error.value.status_code == 403

    allowed = module.transition_task(
        make_request(module, f"/tasks/{task_id}/transition", token=tokens["worker"], request_id="req-worker-working"),
        task_id,
        module.TransitionIn(status="working"),
    )
    assert allowed["status"] == "working"

    with pytest.raises(HTTPException) as wrong_release:
        module.release_task(
            make_request(module, f"/tasks/{task_id}/release", token=tokens["worker"], request_id="req-worker-release-mismatch"),
            task_id,
            module.ReleaseIn(owner="spoofed-owner", status="review_requested"),
        )
    assert wrong_release.value.status_code == 403

    with pytest.raises(HTTPException) as reviewer_release:
        module.release_task(
            make_request(module, f"/tasks/{task_id}/release", token=tokens["reviewer"], request_id="req-reviewer-release-worker-claim"),
            task_id,
            module.ReleaseIn(status="review_requested"),
        )
    assert reviewer_release.value.status_code in {403, 409}

    release = module.release_task(
        make_request(module, f"/tasks/{task_id}/release", token=tokens["worker"], request_id="req-worker-release"),
        task_id,
        module.ReleaseIn(status="review_requested"),
    )
    assert release["status"] == "review_requested"

    audit = module.list_audit(make_request(module, "/audit", token=tokens["operator_active"]))["items"]
    assert any(item["request_id"] == "req-worker-release-mismatch" and item["outcome"] == "denied" for item in audit)


def test_reviewer_and_mcp_scope_matrix(tmp_path: Path) -> None:
    module, tokens, _db_path = start_archon(tmp_path)

    review_task_id = create_task(module, tokens["operator_active"], status="review_requested", title="review-flow")
    claim = module.claim_task(
        make_request(module, "/tasks/claim", token=tokens["reviewer"], request_id="req-review-claim"),
        module.ClaimIn(kind="review", eligible_statuses=["review_requested"]),
    )
    assert claim["item"]["id"] == review_task_id

    with pytest.raises(HTTPException) as reviewer_transition:
        module.transition_task(
            make_request(module, f"/tasks/{review_task_id}/transition", token=tokens["reviewer"], request_id="req-review-working"),
            review_task_id,
            module.TransitionIn(status="working"),
        )
    assert reviewer_transition.value.status_code == 403

    with pytest.raises(HTTPException) as reviewer_worker_run:
        module.create_worker_run(
            make_request(module, "/worker-runs", token=tokens["reviewer"], request_id="req-review-worker-run"),
            module.WorkerRunIn(task_id=review_task_id, agent="bad", model="bad", status="completed"),
        )
    assert reviewer_worker_run.value.status_code == 403

    mcp_task_id = create_task(module, tokens["operator_active"], status="failed", title="mcp-flow")
    with pytest.raises(HTTPException) as mcp_without_scope:
        module.transition_task(
            make_request(module, f"/tasks/{mcp_task_id}/transition", token=tokens["mcp"], request_id="req-mcp-no-tool"),
            mcp_task_id,
            module.TransitionIn(status="needs_changes"),
        )
    assert mcp_without_scope.value.status_code == 403

    with pytest.raises(HTTPException) as mcp_wrong_target:
        module.transition_task(
            make_request(
                module,
                f"/tasks/{mcp_task_id}/transition",
                token=tokens["mcp"],
                request_id="req-mcp-wrong-target",
                extra_headers={module.MCP_SCOPE_HEADER: "archon_transition_task"},
            ),
            mcp_task_id,
            module.TransitionIn(status="approved"),
        )
    assert mcp_wrong_target.value.status_code == 403

    allowed = module.transition_task(
        make_request(
            module,
            f"/tasks/{mcp_task_id}/transition",
            token=tokens["mcp"],
            request_id="req-mcp-allowed",
            extra_headers={module.MCP_SCOPE_HEADER: "archon_transition_task"},
        ),
        mcp_task_id,
        module.TransitionIn(status="needs_changes"),
    )
    assert allowed["status"] == "needs_changes"


def test_server_side_raw_output_default_off_and_audit(tmp_path: Path) -> None:
    module, tokens, db_path = start_archon(tmp_path)

    worker_task_id = create_task(module, tokens["operator_active"], title="raw-worker")
    module.claim_task(
        make_request(module, "/tasks/claim", token=tokens["worker"], request_id="req-raw-worker-claim"),
        module.ClaimIn(kind="worker", eligible_statuses=["queued"]),
    )
    module.transition_task(
        make_request(module, f"/tasks/{worker_task_id}/transition", token=tokens["worker"], request_id="req-raw-worker-transition"),
        worker_task_id,
        module.TransitionIn(status="working"),
    )
    worker_run = module.create_worker_run(
        make_request(module, "/worker-runs", token=tokens["worker"], request_id="req-raw-worker-run"),
        module.WorkerRunIn(
            task_id=worker_task_id,
            agent="worker",
            model="model",
            status="completed",
            summary="done",
            raw_output="Authorization: Bearer super-secret",
        ),
    )
    assert worker_run["status"] == "review_requested"
    assert db_rows(db_path, "SELECT raw_output FROM worker_runs WHERE task_id = ?", (worker_task_id,))[0]["raw_output"] is None

    review_task_id = create_task(module, tokens["operator_active"], status="review_requested", title="raw-review")
    module.claim_task(
        make_request(module, "/tasks/claim", token=tokens["reviewer"], request_id="req-raw-review-claim"),
        module.ClaimIn(kind="review", eligible_statuses=["review_requested"]),
    )
    review = module.create_review(
        make_request(module, "/reviews", token=tokens["reviewer"], request_id="req-raw-review-run"),
        module.ReviewIn(task_id=review_task_id, status="approved", summary="reviewed", raw_output="api_key=abc123"),
    )
    assert review["status"] == "pending_human_approval"
    assert db_rows(db_path, "SELECT raw_output FROM reviews WHERE task_id = ?", (review_task_id,))[0]["raw_output"] is None

    worker_runs = module.list_worker_runs(make_request(module, "/worker-runs", token=tokens["operator_active"]))["items"]
    reviews = module.list_reviews(make_request(module, "/reviews", token=tokens["operator_active"]))["items"]
    assert next(item for item in worker_runs if item["task_id"] == worker_task_id)["raw_output"] is None
    assert next(item for item in reviews if item["task_id"] == review_task_id)["raw_output"] is None

    audit = module.list_audit(make_request(module, "/audit", token=tokens["operator_active"]))["items"]
    assert any(item["request_id"] == "req-raw-worker-run" and item["metadata"]["raw_output_decision"] == "discarded" for item in audit)
    assert any(item["request_id"] == "req-raw-review-run" and item["metadata"]["raw_output_decision"] == "discarded" for item in audit)


def test_raw_output_store_mode_is_server_controlled(tmp_path: Path) -> None:
    module, tokens, db_path = start_archon(tmp_path, raw_output_mode="store")
    task_id = create_task(module, tokens["operator_active"], title="store-raw")
    module.claim_task(
        make_request(module, "/tasks/claim", token=tokens["worker"], request_id="req-store-claim"),
        module.ClaimIn(kind="worker", eligible_statuses=["queued"]),
    )
    module.transition_task(
        make_request(module, f"/tasks/{task_id}/transition", token=tokens["worker"], request_id="req-store-transition"),
        task_id,
        module.TransitionIn(status="working"),
    )
    module.create_worker_run(
        make_request(module, "/worker-runs", token=tokens["worker"], request_id="req-store-run"),
        module.WorkerRunIn(
            task_id=task_id,
            agent="worker",
            model="model",
            status="completed",
            summary="done",
            raw_output="Authorization: Bearer top-secret",
        ),
    )
    assert db_rows(db_path, "SELECT raw_output FROM worker_runs WHERE task_id = ?", (task_id,))[0]["raw_output"] == "[REDACTED]"


def test_request_id_generation_and_audit_failure_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, tokens, db_path = start_archon(tmp_path)
    created_without_request_id = create_task(module, tokens["operator_active"])
    assert created_without_request_id > 0
    audit = module.list_audit(make_request(module, "/audit", token=tokens["operator_active"]))["items"]
    generated = next(item for item in audit if item["target_id"] == str(created_without_request_id))
    assert generated["request_id"]

    before = db_rows(db_path, "SELECT COUNT(*) AS count FROM tasks")[0]["count"]

    def fail_audit(*args, **kwargs):
        raise RuntimeError("forced-audit-failure")

    monkeypatch.setattr(module, "_audit_event", fail_audit)
    with pytest.raises(HTTPException) as audit_failure:
        module.create_task(
            make_request(module, "/tasks", token=tokens["operator_active"], request_id="req-audit-failure"),
            module.TaskIn(title="should-fail", description="", status="queued", source="test"),
        )
    assert audit_failure.value.status_code == 503
    after = db_rows(db_path, "SELECT COUNT(*) AS count FROM tasks")[0]["count"]
    assert before == after


@pytest.mark.parametrize("module_path,loop_flag", [(WORKER_APP_PATH, "WORKER_BACKGROUND_LOOP"), (REVIEWER_APP_PATH, "REVIEW_BACKGROUND_LOOP")])
def test_runner_ingress_auth_health_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_path: Path,
    loop_flag: str,
) -> None:
    auth_path = tmp_path / f"{module_path.stem}-auth.json"
    tokens = write_auth_config(auth_path)
    module = load_module(
        module_path,
        env={
            "ARCHON_AUTH_CONFIG_FILE": str(auth_path),
            "ARCHON_API_TOKEN_FILE": str(tmp_path / "unused.token"),
            loop_flag: "false",
            "OPENCLAW_STATE_DIR": str(tmp_path),
            "OPENCLAW_WORKSPACE_DIR": str(tmp_path),
        },
        module_name=f"{module_path.stem}_{uuid.uuid4().hex}",
        runtime=True,
    )
    monkeypatch.setattr(module, "process_one", lambda request_id=None: {"processed": 1, "request_id": request_id})
    monkeypatch.setattr(module, "build_runtime_diagnostics", lambda **kwargs: {"ok": True, "agent_id": kwargs.get("agent_id")})

    health = module.health()
    ready = module.readyz()
    assert "ok" in health
    assert "ok" in ready

    with pytest.raises(HTTPException) as missing:
        module.run_once(make_runner_request(), {})
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong_identity:
        module.run_once(make_runner_request({"authorization": f"Bearer {tokens['worker']}", "X-Request-ID": "req-runner-wrong"}), {})
    assert wrong_identity.value.status_code == 403

    allowed = module.run_once(
        make_runner_request({"authorization": f"Bearer {tokens['archon']}", "X-Request-ID": "req-runner-allowed"}),
        {},
    )
    assert allowed["request_id"] == "req-runner-allowed"
    assert any(item["request_id"] == "req-runner-allowed" and item["outcome"] == "allowed" for item in module.STATE["audit_events"])
    assert any(item["outcome"] == "denied" for item in module.STATE["audit_events"])
