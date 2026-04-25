# ADR: Release A Credential Lifecycle and Service Identity

Status: Implemented  
Date: 2026-04-25

## Decision

Release A uses opaque bearer service credentials with hashed server-side metadata in `archon-auth.json`.

## Files and injection points

- Archon auth metadata: `./.data/openclaw-config/archon-auth.json`
- Operator token: `./.data/openclaw-config/operator.token`
- Worker token: `./.data/openclaw-config/worker.token`
- Reviewer token: `./.data/openclaw-config/reviewer.token`
- MCP token: `./.data/openclaw-config/mcp.token`
- Readonly token: `./.data/openclaw-config/readonly.token`
- Archon runner-ingress token: `./.data/openclaw-config/archon.token`

Compose mounts these files read-only into the services that need them.

## Metadata contract

Each credential entry in `archon-auth.json` contains:

- `key_id`
- `identity`
- `scopes`
- `state`
- `token_hash`

Plaintext bearer values are never stored in `archon-auth.json`. Bootstrap writes hashed metadata plus separate token files.

## Supported identities

- `operator`
- `worker`
- `reviewer`
- `mcp`
- `readonly`
- `archon`

## Credential states

- `active`: accepted
- `next`: accepted during rotation overlap
- `retired`: rejected
- `revoked`: rejected

Rotation is implemented by publishing a new `next` credential, updating callers, then changing the old credential to `retired` or `revoked`.

## Fail-closed behavior

Archon startup fails closed when:

- `ARCHON_AUTH_CONFIG_FILE` is missing
- the file is empty
- the file is unreadable
- the file is malformed JSON
- required identities do not have an `active` or `next` credential

Runner ingress auth fails closed on missing or invalid runner credentials as well.

## Reload model

Release A uses startup load, not live reload. Credential changes require service restart or container restart to take effect.

## File permission expectations

Bootstrap writes credential material with owner-only read/write permissions. Compose mounts the config directory read-only where possible.

## Scope enforcement

Authorization is enforced by identity plus scope plus, for MCP, tool-specific scope headers and `mcp:<tool>` scopes.

## Non-goals in Release A

- JWT or OIDC claims
- mTLS workload identity
- hot-reload credential rotation
- external secret manager integration
