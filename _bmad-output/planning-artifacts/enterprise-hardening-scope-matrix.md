# Release A Scope and State Matrix

Status: Implemented  
Date: 2026-04-25

## Health policy

| Endpoint | Access | Behavior |
|---|---|---|
| `GET /healthz` | anonymous | liveness only |
| `GET /readyz` | anonymous | readiness only, no token/config contents |
| `GET /health` | anonymous | temporary alias to `/healthz` for Release A |
| `worker:/healthz`, `reviewer:/healthz` | anonymous | liveness |
| `worker:/readyz`, `reviewer:/readyz` | anonymous | readiness |
| `worker:/health`, `reviewer:/health` | anonymous | temporary alias to `/healthz` |

## Archon route matrix

| Route | Identity | Scope rule |
|---|---|---|
| `GET /tasks`, `GET /tasks/{id}` | `operator`, `readonly`, `worker`, `reviewer`, `mcp` | `tasks:read`; `mcp` also needs tool scope for list path |
| `GET /claims` | `operator`, `readonly` | `claims:read` |
| `GET /worker-runs` | `operator`, `readonly`, `worker` | `worker-runs:read` |
| `GET /reviews` | `operator`, `readonly`, `reviewer` | `reviews:read` |
| `GET /approvals` | `operator`, `readonly` | `approvals:read` |
| `GET /audit` | `operator`, `readonly` | `audit:read` |
| `POST /tasks` | `operator`, `mcp` | `tasks:create`; `mcp` also needs `X-Archon-Tool-Scope: archon_create_task` and `mcp:archon_create_task` |
| `PATCH /tasks/{id}` | `operator` | `tasks:patch` |
| `POST /tasks/claim` | `worker`, `reviewer` | identity-specific claim validation |
| `POST /tasks/{id}/release` | `worker`, `reviewer` | authenticated claim owner only |
| `POST /worker-runs` | `worker` | `worker` identity only, must own active worker claim |
| `POST /reviews` | `reviewer`, `mcp` | reviewer must own review claim; `mcp` requires `reviews:create:mcp` plus tool scope |
| `POST /approvals` | `operator`, `mcp` | `operator` needs `approvals:create`; `mcp` requires `approvals:create:mcp` plus tool scope |
| `POST /work/run` | `operator` | `work:run` |
| `POST /reviews/run` | `operator` | `reviews:run` |

## Runner ingress matrix

| Endpoint | Identity | Scope |
|---|---|---|
| `worker:/run-once` | `archon` | `runner:invoke` |
| `reviewer:/run-once` | `archon` | `runner:invoke` |

## MCP tool mapping

| Tool | Route | Required route scope | Required tool scope |
|---|---|---|---|
| `archon_create_task` | `POST /tasks` | `tasks:create` | `mcp:archon_create_task` |
| `archon_list_tasks` | `GET /tasks` | `tasks:read` | `mcp:archon_list_tasks` |
| `archon_transition_task` | `POST /tasks/{id}/transition` | `tasks:transition:mcp` | `mcp:archon_transition_task` |
| `archon_record_review` | `POST /reviews` | `reviews:create:mcp` | `mcp:archon_record_review` |
| `archon_request_approval` | `POST /approvals` | `approvals:create:mcp` | `mcp:archon_request_approval` |

## State and action matrix

| Identity | Allowed action | Source status | Target status | Claim requirement |
|---|---|---|---|---|
| `worker` | claim worker task | `queued`, `needs_changes` | no status change | authenticated `key_id` becomes `claim_owner`, `claim_kind=worker` |
| `worker` | transition | `queued`, `needs_changes` | `working` | must own active worker claim |
| `worker` | release | any active worker claim | `review_requested`, `needs_changes`, `failed` | must own active worker claim |
| `reviewer` | claim review task | `review_requested` | no status change | authenticated `key_id` becomes `claim_owner`, `claim_kind=review` |
| `reviewer` | transition | `review_requested` | `reviewing` | must own active review claim |
| `reviewer` | release | any active review claim | `approved`, `needs_changes`, `rejected`, `pending_human_approval`, `failed` | must own active review claim |
| `mcp` | transition | `failed` | `needs_changes` | no claim path |
| `mcp` | transition | `rejected` | `queued` | no claim path |
| `mcp` | transition | `needs_changes` | `queued` | no claim path |
| `operator` | administrative transition | any | any | no claim requirement |

## Negative rules

- `worker` cannot transition to `approved`, `rejected`, or `pending_human_approval`
- `reviewer` cannot transition to `working`
- `reviewer` cannot write worker runs
- `mcp` cannot call mutating routes without the matching tool scope header
- `mcp` cannot bypass tool scope with a broad credential alone
- request-body `owner` cannot spoof claim or release ownership
