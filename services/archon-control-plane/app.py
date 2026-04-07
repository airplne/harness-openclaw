from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("ARCHON_DB_PATH", "/data/archon.sqlite3"))
REQUIRE_HUMAN_APPROVAL = os.getenv("ARCHON_REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"

app = FastAPI(title="Archon Control Plane", version="0.1.0")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                description TEXT,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                assignee TEXT,
                review_after TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                review_queue TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                findings_json TEXT NOT NULL DEFAULT '[]',
                raw_output TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            '''
        )


class TaskIn(BaseModel):
    title: str
    description: str = ""
    status: str = "queued"
    source: str = "manual"
    external_id: str | None = None
    assignee: str | None = None
    review_after: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransitionIn(BaseModel):
    status: str
    notes: str | None = None


class ApprovalIn(BaseModel):
    task_id: int
    decision: str
    reviewer: str | None = None
    notes: str | None = None


class ReviewIn(BaseModel):
    task_id: int | None = None
    review_queue: str = "codex-review"
    model: str = "openai-codex/gpt-5.4"
    status: str = "pending_human_approval"
    summary: str | None = None
    findings: list[str] = Field(default_factory=list)
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
        "timestamp": utcnow(),
    }


@app.get("/tasks")
def list_tasks(status: str | None = None) -> dict[str, Any]:
    with db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
    tasks = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        tasks.append(item)
    return {"items": tasks}


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


@app.post("/tasks/{task_id}/transition")
def transition_task(task_id: int, transition: TransitionIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (transition.status, now, task_id),
        )
    return {"task_id": task_id, "status": transition.status, "updated_at": now, "notes": transition.notes}


@app.get("/approvals")
def list_approvals(decision: str | None = None) -> dict[str, Any]:
    with db() as conn:
        if decision:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE decision = ? ORDER BY created_at DESC",
                (decision,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
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
        mapped_status = {
            "approved": "approved",
            "rejected": "rejected",
            "needs_changes": "needs_changes",
        }.get(approval.decision, "pending_human_approval")
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (mapped_status, now, approval.task_id),
        )
    return {"task_id": approval.task_id, "decision": approval.decision, "created_at": now}


@app.get("/reviews")
def list_reviews() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM reviews ORDER BY created_at DESC").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["findings"] = json.loads(item.pop("findings_json") or "[]")
        items.append(item)
    return {"items": items}


@app.post("/reviews")
def create_review(review: ReviewIn) -> dict[str, Any]:
    now = utcnow()
    with db() as conn:
        conn.execute(
            '''
            INSERT INTO reviews (task_id, review_queue, model, status, summary, findings_json, raw_output, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                review.task_id,
                review.review_queue,
                review.model,
                review.status,
                review.summary,
                json.dumps(review.findings),
                review.raw_output,
                now,
            ),
        )
        if review.task_id is not None:
            new_status = review.status if review.status in {"approved", "rejected", "needs_changes"} else "pending_human_approval"
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, review.task_id),
            )
    return {"task_id": review.task_id, "status": review.status, "created_at": now}


@app.get("/reviews/queue")
def review_queue() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            '''
            SELECT * FROM tasks
            WHERE status IN ('working', 'review_requested', 'queued')
            ORDER BY updated_at ASC
            '''
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        items.append(item)
    return {"items": items, "require_human_approval": REQUIRE_HUMAN_APPROVAL}


@app.post("/reviews/run")
def trigger_review(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "accepted": True,
        "reason": payload.get("reason", "manual trigger"),
        "timestamp": utcnow(),
    }


@app.post("/mcp")
def mcp(payload: dict[str, Any]) -> dict[str, Any]:
    tool = payload.get("tool")
    arguments = payload.get("arguments", {})

    if tool == "archon.create_task":
        return create_task(TaskIn(**arguments))
    if tool == "archon.request_approval":
        return create_approval(ApprovalIn(**arguments))
    if tool == "archon.list_pending_approvals":
        return list_tasks(status="pending_human_approval")
    if tool == "archon.record_review":
        return create_review(ReviewIn(**arguments))
    if tool == "archon.transition_task":
        task_id = int(arguments["task_id"])
        return transition_task(task_id, TransitionIn(status=arguments["status"], notes=arguments.get("notes")))

    raise HTTPException(status_code=400, detail=f"unknown MCP tool: {tool}")
