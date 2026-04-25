# Release A Validation and Rollout

Status: Implemented for Release A  
Date: 2026-04-25

## Release A exit criteria

- Archon fails closed on missing, empty, unreadable, and malformed auth config.
- Distinct `operator`, `worker`, `reviewer`, `mcp`, `readonly`, and `archon` credentials exist.
- MCP and runner ingress are protected.
- Request IDs are generated or preserved and written to audit.
- Raw output is server-controlled and default-off.
- Minimal audit evidence is persisted in SQLite and queryable through `/audit`.
- `/healthz`, `/readyz`, and the `/health` alias are implemented.
- Release A security tests run in CI.

## Minimal audit model

Storage target:

- SQLite table: `audit_events`

Persistence expectation:

- survives request, process, and container restart as long as the SQLite volume persists

Failure behavior:

- default mode is fail-closed for privileged mutations
- if audit write fails and `ARCHON_AUDIT_DEGRADED_MODE=false`, the mutation returns `503`
- readiness reflects degraded audit state through `/readyz`

Required evidence captured in Release A:

- auth denials
- scope denials
- claim and release allow or deny
- transition allow or deny
- worker-run creation allow or deny
- review creation allow or deny
- approval creation allow or deny
- raw-output discard or store decision
- manual run triggers

## Raw output policy

- default: `ARCHON_RAW_OUTPUT_MODE=discard`
- opt-in: `ARCHON_RAW_OUTPUT_MODE=store`
- server decides persistence regardless of client payload
- stored values are redacted before persistence
- existing `raw_output` rows are redacted on startup by default through `ARCHON_EXISTING_RAW_OUTPUT_POLICY=redact`

## Health migration

- `/healthz` is the canonical liveness endpoint
- `/readyz` is the canonical readiness endpoint
- `/health` remains a Release A alias to `/healthz`
- compose healthchecks now point to `/healthz`
- validation scripts exercise both liveness and readiness where relevant

## Operator rollout

Operator examples and scripts now use:

- `operator.token` for mutating Archon commands
- `healthz` and `readyz` for liveness/readiness checks
- authenticated smoke and live-validation scripts

## CI gate

Blocking workflow:

- `./.github/workflows/release-a-security.yml`

Release A workflow coverage:

- fail-closed auth config
- credential lifecycle acceptance and rejection
- wrong-scope and wrong-identity denial
- state/action authorization matrix
- identity-bound claim and release behavior
- MCP direct-route bypass denial
- raw-output server-side default-off
- minimal audit persistence and failure behavior
- runner ingress protection
- health migration behavior
- request ID propagation

## Rollback rule

Release A rollback must not weaken fail-closed auth. If rollback is required:

- retain `ARCHON_AUTH_REQUIRED=true`
- keep per-service token files mounted
- keep `archon-auth.json` present and readable
- restore from the last known-good image and SQLite backup rather than disabling auth
