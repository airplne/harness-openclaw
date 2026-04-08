from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib import request

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("ARCHON_DB_PATH", "/data/archon.sqlite3"))
REQUIRE_HUMAN_APPROVAL = os.getenv("ARCHON_REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"
WORKER_API_URL = os.getenv("ARCHON_WORKER_API_URL", "")
REVIEWER_API_URL = os.getenv("ARCHON_REVIEWER_API_URL", "")

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

app = FastAPI(title="Archon Control Plane", version="0.3.0")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


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
            '''
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
            '''
        )


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
    owner: str
    ttl_seconds: int = 600
    eligible_statuses: list[TaskStatus] = Field(default_factory=list)


class ReleaseIn(BaseModel):
    owner: str
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "db_path": str(DB_PATH),
        "require_human_approval": REQUIRE_HUMAN_APPROVAL,
        "worker_api_url": WORKER_API_URL,
        "reviewer_api_url": REVIEWER_API_URL,
        "timestamp": utcnow(),
    }


def _task_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


@app.get("/tasks")
def list_tasks(status: TaskStatus | None = None) -> dict[str, Any]:
    with db() as conn:
        if status:
            rows = conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
    return {"items": [_task_row_to_dict(row) for row in rows]}


@app.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_row_to_dict(row)


@app.post("/tasks")
def create_task(task: TaskIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        cur = conn.execute(
            '''
            INSERT INTO tasks (
                external_id, title, description, status, source, assignee,
                review_after, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
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
        task_id = cur.lastrowid
    return {"task_id": task_id, "status": task.status, "created_at": now}


@app.patch("/tasks/{task_id}")
def patch_task(task_id: int, patch: TaskPatch) -> dict[str, Any]:
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
            '''
            UPDATE tasks
            SET title = ?, description = ?, status = ?, assignee = ?, review_after = ?,
                metadata_json = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            ''',
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
    return {"task_id": task_id, "updated_at": now}


@app.post("/tasks/{task_id}/transition")
def transition_task(task_id: int, transition: TransitionIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (transition.status, now, task_id))
    return {"task_id": task_id, "status": transition.status, "updated_at": now, "notes": transition.notes}


@app.post("/tasks/claim")
def claim_task(payload: ClaimIn) -> dict[str, Any]:
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
                (payload.kind, payload.owner, claim_until, now, row["id"]),
            )
            claimed = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            return {"item": _task_row_to_dict(claimed)}
    return {"item": None}


@app.post("/tasks/{task_id}/release")
def release_task(task_id: int, payload: ReleaseIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        if row["claim_owner"] not in (None, payload.owner):
            raise HTTPException(status_code=409, detail="task claimed by another worker")
        new_status = payload.status or row["status"]
        conn.execute(
            '''
            UPDATE tasks
            SET claim_kind = NULL, claim_owner = NULL, claim_until = NULL,
                status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            ''',
            (new_status, payload.last_error, now, task_id),
        )
    return {"task_id": task_id, "status": new_status, "updated_at": now}


@app.get("/work/queue")
def work_queue() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status IN ('queued', 'needs_changes') ORDER BY updated_at ASC").fetchall()
    return {"items": [_task_row_to_dict(row) for row in rows]}


@app.get("/reviews/queue")
def review_queue() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status = 'review_requested' ORDER BY updated_at ASC").fetchall()
    return {"items": [_task_row_to_dict(row) for row in rows], "require_human_approval": REQUIRE_HUMAN_APPROVAL}


@app.post("/worker-runs")
def create_worker_run(run: WorkerRunIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (run.task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        conn.execute(
            '''
            INSERT INTO worker_runs (task_id, agent, model, status, summary, artifacts_json, follow_up_json, raw_output, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                run.task_id,
                run.agent,
                run.model,
                run.status,
                run.summary,
                json.dumps(run.artifacts),
                json.dumps(run.follow_up),
                run.raw_output,
                now,
            ),
        )
        new_status = "review_requested" if run.status == "completed" else "needs_changes" if run.status == "blocked" else "failed"
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, run.task_id))
    return {"task_id": run.task_id, "status": new_status, "created_at": now}


@app.get("/worker-runs")
def list_worker_runs(task_id: int | None = None) -> dict[str, Any]:
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
        items.append(item)
    return {"items": items}


@app.post("/reviews")
def create_review(review: ReviewIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (review.task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        conn.execute(
            '''
            INSERT INTO reviews (
                task_id, review_queue, agent, model, status, summary, findings_json,
                follow_up_json, requires_human_approval, raw_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
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
                review.raw_output,
                now,
            ),
        )
        if review.status == "approved" and REQUIRE_HUMAN_APPROVAL and review.requires_human_approval:
            new_status = "pending_human_approval"
        else:
            new_status = review.status
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, review.task_id))
    return {"task_id": review.task_id, "status": new_status, "created_at": now}


@app.get("/reviews")
def list_reviews(task_id: int | None = None) -> dict[str, Any]:
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
        items.append(item)
    return {"items": items}


@app.get("/approvals")
def list_approvals(task_id: int | None = None) -> dict[str, Any]:
    with db() as conn:
        if task_id is None:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at DESC", (task_id,)).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/approvals")
def create_approval(approval: ApprovalIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (approval.task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        conn.execute(
            "INSERT INTO approvals (task_id, decision, reviewer, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (approval.task_id, approval.decision, approval.reviewer, approval.notes, now),
        )
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (approval.decision, now, approval.task_id))
    return {"task_id": approval.task_id, "decision": approval.decision, "created_at": now}


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


@app.post("/work/run")
def run_work(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not WORKER_API_URL:
        raise HTTPException(status_code=503, detail="worker api url not configured")
    return _post_json(f"{WORKER_API_URL}/run-once", payload or {})


@app.post("/reviews/run")
def run_review(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not REVIEWER_API_URL:
        raise HTTPException(status_code=503, detail="reviewer api url not configured")
    return _post_json(f"{REVIEWER_API_URL}/run-once", payload or {})
