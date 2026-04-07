# Archon integration

This repository ships a thin Archon-style control plane for task intake, review tracking, and human approval.

## Data model
Tasks move through the following states:

- `queued`
- `working`
- `review_requested`
- `pending_human_approval`
- `approved`
- `needs_changes`
- `rejected`

Approvals are stored separately so operators can see who approved, rejected, or requested changes.

## Integration points
- `POST /tasks` creates or updates tracked work items.
- `POST /approvals` records a human gate decision.
- `POST /reviews/run` records a scheduled Codex review attempt.
- `/mcp` exposes a minimal MCP-compatible HTTP endpoint for OpenClaw-side tool access.

## MCP tools exposed
- `archon.create_task`
- `archon.request_approval`
- `archon.list_pending_approvals`
- `archon.record_review`
- `archon.transition_task`

Use these tools from OpenClaw when work needs to be surfaced for operator approval.
