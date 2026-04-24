# Secure-by-Default Control Plane: Current State and Assumptions

Date: 2026-04-24

## Why This Exists

The first GPT review correctly called out a planning defect: the PR backlog assumed supporting docs and CI context that were not actually present on the PR branch. This file fixes that by stating what this planning PR does and does not contain.

## PR Reality

This planning PR contains backlog and design artifacts only.

It does not claim to ship:

- implemented auth changes
- implemented migration framework
- implemented CI gates
- implemented rollout scripts

## Code Truth Used for Planning

### Archon MCP is a privileged caller

Evidence:

- `services/archon-mcp/server.py`

Implication:

- MCP cannot be treated as a passive helper.
- Mutating tools must have dedicated identity, scopes, request ID propagation, and audit coverage.

### Runner ingress is part of the trust boundary

Evidence:

- `services/openclaw-runtime/worker_loop.py`
- `services/openclaw-runtime/review_loop.py`

Implication:

- `POST /run-once` is part of Release A.
- It is not acceptable to harden only Archon and defer runner ingress.

### Raw model output is a first-order data-governance risk

Evidence:

- worker and reviewer send `result.stdout` into Archon persistence paths

Implication:

- `raw_output` control belongs in Release A or early Release B, before broader logging/audit expansion.

### Durable audit depends on schema discipline

Evidence:

- Archon initializes SQLite tables directly on startup in `services/archon-control-plane/app.py`

Implication:

- migration/versioning foundation must precede durable audit schema changes

### Auth hardening changes operator ergonomics

Evidence:

- README curl commands, health checks, smoke paths, and validation scripts are part of current operator expectations

Implication:

- rollout docs, script alignment, and authenticated operator flows must be explicit backlog items

## Explicit Assumptions

- Release A will choose a single concrete credential mechanism rather than leaving implementation teams to invent one.
- Release A will include MCP, runner ingress, and manual trigger paths.
- Release A will include minimal audit for auth/authz and mutating actions.
- CI may need to be created or reconciled; it must not be assumed.

## Explicit Non-Assumptions

- We do not assume supporting docs in this PR are already present elsewhere on the remote branch.
- We do not assume a migration framework already exists.
- We do not assume raw output persistence is acceptable by default.
- We do not assume internal network locality is a valid trust boundary.

