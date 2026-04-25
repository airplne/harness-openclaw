# Enterprise Hardening Release A Status

Date: 2026-04-25  
Project: `harness-openclaw`

## Companion docs

- [enterprise-hardening-current-state-and-assumptions.md](./enterprise-hardening-current-state-and-assumptions.md)
- [enterprise-hardening-auth-adr.md](./enterprise-hardening-auth-adr.md)
- [enterprise-hardening-scope-matrix.md](./enterprise-hardening-scope-matrix.md)
- [enterprise-hardening-release-validation-and-rollout.md](./enterprise-hardening-release-validation-and-rollout.md)

## Release A implementation summary

Implemented in code:

- fail-closed Archon auth using hashed opaque bearer credentials
- distinct identities for `operator`, `worker`, `reviewer`, `mcp`, `readonly`, and `archon`
- identity-bound claims and releases
- explicit state/action matrix for worker, reviewer, and MCP transitions
- MCP tool scope enforcement and direct-route bypass denial
- request ID propagation across Archon, runners, and MCP
- server-side raw-output control with default discard
- queryable minimal audit in SQLite
- protected worker/reviewer `/run-once`
- `/healthz`, `/readyz`, and `/health` alias
- Release A security CI workflow

## Story status

| Story | Status | Evidence |
|---|---|---|
| EH-01 Credential mechanism ADR | implemented | `services/archon-control-plane/app.py`, `scripts/bootstrap.sh`, `./enterprise-hardening-auth-adr.md` |
| EH-02 Route and tool scope matrix | implemented | `services/archon-control-plane/app.py`, `services/archon-mcp/server.py`, `./enterprise-hardening-scope-matrix.md` |
| EH-03 Schema versioning foundation | implemented | `schema_version` table in `services/archon-control-plane/app.py` |
| EH-A1 Fail-closed Archon ingress | implemented | auth store load, bearer validation, startup failure behavior |
| EH-A2 Runner ingress protection | implemented | `services/openclaw-runtime/worker_loop.py`, `services/openclaw-runtime/review_loop.py` |
| EH-A3 Per-caller credentials | implemented | `scripts/bootstrap.sh`, `docker-compose.yml`, `README.md` |
| EH-A4 MCP authz and tracing | implemented | `services/archon-mcp/server.py`, MCP scope enforcement in Archon |
| EH-A5 Operator vs readonly | implemented | token scopes plus README/operator command updates |
| EH-A6 Request IDs | implemented | `X-Request-ID` propagation in Archon, runners, MCP |
| EH-A7 Minimal audit | implemented | `audit_events` table and `/audit` route |
| EH-A8 Raw output control | implemented | server-side discard/store policy plus startup redaction policy |
| EH-A9 Health migration | implemented | `/healthz`, `/readyz`, `/health` alias, compose/script updates |
| EH-A10 Release A CI gate | implemented | `./.github/workflows/release-a-security.yml`, `tests/test_release_a_security.py` |

## Remaining post-Release-A work

- durable audit export and external sink integration
- richer before/after state hashing
- hot-reload credential rotation
- PostgreSQL migration
- retention enforcement beyond current SQLite startup/hourly loops
- branch-protection enforcement around the new workflow in GitHub settings
