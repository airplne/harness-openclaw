# Secure-by-Default Control Plane Scope Matrix

Date: 2026-04-24

## Identities

- `archon`
- `worker`
- `reviewer`
- `mcp`
- `operator`
- `readonly`
- `anonymous`

`anonymous` is allowed only on explicitly public health endpoints if Release A keeps them public.

## Archon Route Matrix

| Route or action class | Allowed identities | Notes |
|---|---|---|
| `GET /healthz` if public | `anonymous` | Optional public health only |
| `GET /readyz` or protected health | `archon`, `operator`, `readonly` | Exact model decided in implementation |
| `GET /tasks`, `GET /tasks/{id}`, `GET /worker-runs`, `GET /reviews`, `GET /approvals`, `GET /claims`, `GET /audit*` | `operator`, `readonly`, `mcp` where tool requires it | `mcp` access must be tool-scoped |
| `POST /tasks` | `operator`, `mcp` | `mcp` only via `archon_create_task` scope |
| `PATCH /tasks/{id}` | `operator` | Keep narrow unless later expanded intentionally |
| `POST /tasks/{id}/transition` | `operator`, `worker`, `reviewer`, `mcp` | scoped by action type |
| `POST /tasks/claim` | `worker`, `reviewer` | identity-specific claim scope |
| `POST /tasks/{id}/release` | `worker`, `reviewer` | release only for owned claim type |
| `POST /worker-runs` | `worker` | write-only for worker path |
| `POST /reviews` | `reviewer`, `mcp` only if explicitly allowed | default keep narrow |
| `POST /approvals` | `operator`, `mcp` only if explicitly allowed | approval mutation requires audit |
| `POST /work/run` | `operator` | manual trigger path |
| `POST /reviews/run` | `operator` | manual trigger path |

## Runner Ingress Matrix

| Endpoint | Allowed identities | Notes |
|---|---|---|
| `worker:/run-once` | `archon` | no direct anonymous or worker self-trigger by default |
| `reviewer:/run-once` | `archon` | no direct anonymous or reviewer self-trigger by default |
| `worker:/healthz` if public | `anonymous` | optional |
| `reviewer:/healthz` if public | `anonymous` | optional |

## MCP Tool Matrix

| MCP tool | Underlying Archon action | Allowed identity | Audit required |
|---|---|---|---|
| `archon_create_task` | `POST /tasks` | `mcp` | yes |
| `archon_list_tasks` | `GET /tasks` | `mcp` | read trace only |
| `archon_transition_task` | `POST /tasks/{id}/transition` | `mcp` | yes |
| `archon_record_review` | `POST /reviews` | `mcp` only if explicitly enabled | yes |
| `archon_request_approval` | `POST /approvals` | `mcp` only if explicitly enabled | yes |

## Negative Expectations

These are required failure cases in Release A:

- `worker` cannot call reviewer-only routes
- `reviewer` cannot call worker-only routes
- `mcp` cannot call arbitrary mutating routes outside mapped tools
- `readonly` cannot call mutating routes
- `anonymous` cannot call protected Archon or runner ingress routes
- wrong-scope calls must be denied with `403` and minimally audited

