# Archon control plane

Archon is the task and approval system for this harness.

## Canonical task lifecycle

- `queued`
- `working`
- `review_requested`
- `reviewing`
- `pending_human_approval`
- `approved`
- `needs_changes`
- `rejected`
- `failed`

## What is persisted

- tasks
- worker runs
- review runs
- approvals
- task claims for worker/reviewer leasing

## Real integration surfaces

- REST API on `:8080`
- stdio MCP server at `services/archon-mcp/server.py`

The OpenClaw config points `mcp.servers.archon` to the stdio MCP server, which is a real MCP tool surface instead of a custom `/mcp` HTTP shim.

## Main endpoints

- `POST /tasks`
- `GET /tasks`
- `PATCH /tasks/{id}`
- `POST /tasks/{id}/transition`
- `POST /tasks/claim`
- `POST /tasks/{id}/release`
- `POST /worker-runs`
- `GET /worker-runs`
- `POST /reviews`
- `GET /reviews`
- `POST /approvals`
- `GET /approvals`
- `POST /work/run`
- `POST /reviews/run`
