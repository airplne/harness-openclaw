# Release A Validation and Rollout Plan

Date: 2026-04-24

## Release A Exit Criteria

Release A is not approved until all of the following are true:

- Archon protected routes fail closed on missing, invalid, and wrong-scope credentials.
- Worker and reviewer `/run-once` endpoints are protected.
- MCP mutating tools run under the `mcp` identity with scope enforcement.
- Request IDs propagate across Archon, MCP, and runner paths.
- Minimal audit exists for auth failures, authz denials, task mutations, review mutations, approval mutations, MCP mutations, and manual run triggers.
- `raw_output` is default-off and tested for redaction and boundary behavior.
- Authenticated operator commands, health checks, smoke checks, and live validation steps are documented.

## Validation Matrix

| Validation area | Environment | Blocking | Evidence |
|---|---|---|---|
| Archon fail-closed auth | unit + integration | yes | negative-path tests |
| Runner ingress protection | integration | yes | wrong-scope and unauthenticated tests |
| MCP scoped mutation flow | integration | yes | request ID plus minimal audit assertions |
| Request ID propagation | integration | yes | end-to-end trace assertions |
| Minimal audit substrate | integration | yes | audit record assertions |
| Raw output controls | unit + integration | yes | redaction, truncation, and retrieval tests |
| Migration baseline | migration test env | yes before durable audit release | forward and idempotent migration tests |
| Operator authenticated flow | staging-like | yes | smoke and live-validation execution |

## CI Reality Rule

This planning set does not assume a working CI security pipeline already exists on the PR branch.

Implementation must choose one of two explicit paths:

- create the CI security validation workflow for this control-plane path, or
- reconcile this backlog against a confirmed existing workflow and name it precisely

“Update CI as needed” is not acceptable.

## Rollout Sequence

### Phase 0: Local and staging preparation

- generate credentials for all Release A identities
- update operator commands and validation scripts
- validate fail-closed behavior and request ID propagation

### Phase 1: Staging execution

- run authenticated smoke path
- run manual trigger path through Archon
- validate MCP scoped mutation path
- validate raw output default-off behavior

### Phase 2: Hardened rollout

- enable Release A controls in the target environment
- execute runbook validation
- confirm operator and readonly flows

### Phase 3: Durable audit release

- run schema migration path
- validate durable audit persistence and query paths
- execute restore or rollback procedure as documented

## Rollback and Restore Rule

If a change cannot be rolled back cleanly, the corresponding story must require a tested restore procedure instead.

That applies especially to:

- schema changes
- credential rotations
- retention or purge behavior

