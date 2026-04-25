# Release A Current State

Status: Implemented snapshot  
Date: 2026-04-25

## Repository truth after Release A

- Archon auth is implemented in `services/archon-control-plane/app.py`
- MCP request auth and tool-scope propagation are implemented in `services/archon-mcp/server.py`
- Worker and reviewer Archon auth propagation plus protected `/run-once` ingress are implemented in:
  - `services/openclaw-runtime/runner_common.py`
  - `services/openclaw-runtime/worker_loop.py`
  - `services/openclaw-runtime/review_loop.py`
- Bootstrap now creates hashed auth metadata and per-service token files in `scripts/bootstrap.sh`
- Compose wiring uses per-service token files in `docker-compose.yml`
- Release A security tests live in `tests/test_release_a_security.py`
- Release A blocking workflow lives in `./.github/workflows/release-a-security.yml`

## Key implementation assumptions now made explicit

- network locality is not a trust boundary
- claim ownership is derived from authenticated identity
- raw output persistence is controlled by Archon server policy
- minimal audit must be queryable and survive restart through SQLite
- health and readiness are different concerns

## Known Release A limits

- credential reload requires restart
- durable audit export is not implemented yet
- SQLite remains the persistence layer
- `/health` remains a temporary alias for `/healthz`
