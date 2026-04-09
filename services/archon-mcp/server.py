#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib import request
from urllib.parse import urlencode

ARCHON_API_BASE_URL = os.getenv("ARCHON_API_BASE_URL", "http://archon:8080")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "archon_create_task",
        "description": "Create a new Archon task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string"},
                "source": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "archon_list_tasks",
        "description": "List Archon tasks, optionally filtered by status.",
        "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
    },
    {
        "name": "archon_transition_task",
        "description": "Transition an Archon task to a new status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "archon_record_review",
        "description": "Persist a review result for an Archon task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "review_queue": {"type": "string"},
                "agent": {"type": "string"},
                "model": {"type": "string"},
                "status": {"type": "string"},
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}},
                "follow_up": {"type": "array", "items": {"type": "string"}},
                "requires_human_approval": {"type": "boolean"},
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "archon_request_approval",
        "description": "Record a human approval decision for an Archon task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "decision": {"type": "string"},
                "reviewer": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["task_id", "decision"],
        },
    },
]


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("utf-8").strip()
        if not text:
            break
        key, value = text.split(":", 1)
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _send(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _json_response(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _json_error(message_id: Any, code: int, text: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def _get(path: str) -> dict[str, Any]:
    with request.urlopen(f"{ARCHON_API_BASE_URL}{path}", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = request.Request(
        f"{ARCHON_API_BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "archon_create_task":
        data = _post("/tasks", arguments)
    elif name == "archon_list_tasks":
        status = arguments.get("status")
        suffix = f"?{urlencode({'status': status})}" if status else ""
        data = _get(f"/tasks{suffix}")
    elif name == "archon_transition_task":
        task_id = int(arguments["task_id"])
        data = _post(f"/tasks/{task_id}/transition", {"status": arguments["status"], "notes": arguments.get("notes")})
    elif name == "archon_record_review":
        data = _post("/reviews", arguments)
    elif name == "archon_request_approval":
        data = _post("/approvals", arguments)
    else:
        raise KeyError(name)
    return {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False}


def main() -> int:
    while True:
        message = _read_message()
        if message is None:
            return 0
        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "notifications/initialized":
            continue
        if method == "initialize":
            _send(
                _json_response(
                    message_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "archon-mcp", "version": "0.1.0"},
                        "capabilities": {"tools": {}},
                    },
                )
            )
            continue
        if method == "ping":
            _send(_json_response(message_id, {}))
            continue
        if method == "tools/list":
            _send(_json_response(message_id, {"tools": TOOLS}))
            continue
        if method == "tools/call":
            try:
                result = _call_tool(str(params.get("name")), params.get("arguments") or {})
                _send(_json_response(message_id, result))
            except KeyError:
                _send(_json_error(message_id, -32601, "unknown tool"))
            except Exception as exc:
                _send(_json_error(message_id, -32000, str(exc)))
            continue

        _send(_json_error(message_id, -32601, f"unknown method: {method}"))


if __name__ == "__main__":
    raise SystemExit(main())
