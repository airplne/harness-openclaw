# Secure-by-Default Control Plane Backlog

Date: 2026-04-24
Project: `harness-openclaw`
Prepared by: Mary with BMAD party-mode input and revised against GPT review feedback

## Purpose

Introduce the minimum secure-by-default controls required to operate the Archon control plane, MCP integration, and runner ingress paths in enterprise environments.

This backlog is intentionally narrow. It is not a generic “enterprise hardening” wishlist.

## What This PR Changes

This PR adds planning artifacts only. It does not claim the code already implements the controls below.

This revised planning set fixes the defects called out in the first GPT review:

- explicitly covers the Archon MCP trust boundary
- brings runner `/run-once` protection into Release A
- removes misleading wording about a pre-existing shared Archon token model
- moves minimal auth/authz audit into Release A to avoid the audit dependency loop
- adds early `raw_output` persistence controls
- adds rollout stories for operator commands, health checks, smoke tests, and live validation
- adds a credential mechanism ADR and a route/tool scope matrix
- adds a database migration/versioning foundation before durable audit changes
- stops assuming CI and supporting docs already exist on the PR head

## Current State

This backlog is based on the current code truth visible in the repository workspace:

| Area | Current-state evidence | Planning implication |
|---|---|---|
| Archon MCP | `services/archon-mcp/server.py` exposes mutating tools for create task, transition task, record review, and request approval | MCP is a privileged caller and must get its own identity, scopes, tracing, and audit coverage |
| Runner ingress | `services/openclaw-runtime/worker_loop.py` and `review_loop.py` expose `POST /run-once` | Runner ingress is part of Release A, not a later hardening step |
| Raw output persistence | worker and reviewer send `result.stdout` to Archon persistence paths | `raw_output` control must happen before telemetry expands |
| Archon persistence | `services/archon-control-plane/app.py` initializes SQLite tables directly on startup | schema versioning and migration discipline must be introduced before durable audit changes |
| Operator paths | README commands, health checks, smoke/live validation, and compose behavior will change once auth is enforced | rollout docs, scripts, and authenticated operator flows must be first-class stories |
| CI/docs in PR snapshot | this PR branch only carries planning artifacts added here | CI creation and doc expansion must be planned explicitly, not assumed as existing review context |

## Companion Planning Docs in This PR

Read these with this backlog:

- [enterprise-hardening-current-state-and-assumptions.md](/home/aip0rt/Desktop/harness-openclaw/_bmad-output/planning-artifacts/enterprise-hardening-current-state-and-assumptions.md)
- [enterprise-hardening-auth-adr.md](/home/aip0rt/Desktop/harness-openclaw/_bmad-output/planning-artifacts/enterprise-hardening-auth-adr.md)
- [enterprise-hardening-scope-matrix.md](/home/aip0rt/Desktop/harness-openclaw/_bmad-output/planning-artifacts/enterprise-hardening-scope-matrix.md)
- [enterprise-hardening-release-validation-and-rollout.md](/home/aip0rt/Desktop/harness-openclaw/_bmad-output/planning-artifacts/enterprise-hardening-release-validation-and-rollout.md)

## Release Boundaries

### Release A: Secure-by-Default Control Plane

Goal:
Remove the highest-risk exposure first: privileged actions without identity, scope enforcement, traceability, or minimal audit evidence.

Approval bar:

- no privileged Archon, MCP, or runner ingress path without identity
- no over-broad caller privilege on mutating routes or MCP tools
- no manual run trigger path without protection
- no sensitive raw model output persisted by default
- authenticated operator flow, health model, smoke checks, and validation path are documented and testable

### Release B: Durable Audit and Data Governance

Goal:
Make Release A accountable, durable, and governable.

### Release C: Adoption, CI, and Operational Safety

Goal:
Make the hardened path repeatable to deploy, validate, and operate.

## Recommended Epic Order

1. Epic 0: Foundation Decisions and Repo Truth
2. Epic 1: Release A Ingress Protection and Identity
3. Epic 2: Release A Traceability, Minimal Audit, and Raw Output Control
4. Epic 3: Release A Operator and Validation Migration
5. Epic 4: Durable Audit and Schema Migration
6. Epic 5: Data Retention and Privacy Controls
7. Epic 6: CI Security Validation Gates
8. Epic 7: Runbooks, Rollout, and Readiness

## Epic 0: Foundation Decisions and Repo Truth

Outcome:
Implementation starts from explicit design decisions and an accurate inventory, not assumptions.

### Story EH-00: Correct current-state architecture and repo inventory

Problem reduced:
Planning defects caused by assuming docs, CI, auth, or rollout assets already exist.

Acceptance criteria:

- Current state is documented for Archon auth, MCP, runner ingress, raw output persistence, operator flows, schema management, and CI/doc availability.
- The story explicitly states which files exist today and which are planning additions in this PR.
- The planning set does not refer to CI jobs, docs, or scripts as already present unless they are included in the PR or visible in the repo truth table.

Depends on:

- None.

### Story EH-01: Approve the credential mechanism ADR

Problem reduced:
Mechanism-vague auth requirements that invite incompatible implementations.

Acceptance criteria:

- The credential mechanism is explicitly chosen for Release A.
- Storage, injection, rotation, revocation, bootstrap, and local-dev fallback are defined.
- The chosen mechanism covers these actors: `archon`, `worker`, `reviewer`, `mcp`, `operator`, `readonly`.
- Non-goals and deferred alternatives are stated.

Depends on:

- Story EH-00.

### Story EH-02: Publish the route and tool scope matrix

Problem reduced:
Over-broad or ambiguous authorization rules.

Acceptance criteria:

- Every privileged Archon route, runner ingress endpoint, and MCP tool is mapped to allowed identities.
- MCP tools are mapped to underlying Archon actions and audit expectations.
- Public versus protected health endpoints are explicitly listed.
- Negative-path expectations exist for wrong-scope callers.

Depends on:

- Story EH-01.

### Story EH-03: Introduce database schema versioning and migration discipline

Problem reduced:
Schema changes for audit and retention without a safe upgrade path.

Acceptance criteria:

- A versioned migration approach is defined before durable audit changes land.
- Forward migration, idempotent rerun behavior, and rollback or restore strategy are documented.
- Mixed-version or staged-rollout compatibility expectations are stated.
- Migration tests against an existing SQLite baseline are required by the story.

Depends on:

- Story EH-00.

## Epic 1: Release A Ingress Protection and Identity

Outcome:
All privileged entry points are fail-closed and tied to distinct caller identities.

### Story EH-A1: Enforce fail-closed auth on Archon ingress

Problem reduced:
Privileged control-plane actions can be accepted without identity.

Acceptance criteria:

- All protected Archon routes reject missing credentials with `401`.
- Invalid or revoked credentials return `401`.
- Valid credentials with wrong scope return `403`.
- No code path treats network locality as trust.
- Startup fails in non-dev mode when required auth configuration is missing.
- Protected and public health behavior is explicit and documented.

Depends on:

- Stories EH-01 and EH-02.

### Story EH-A2: Protect runner ingress and manual execution endpoints

Problem reduced:
`/run-once` and manual trigger paths remain callable without scoped authorization.

Acceptance criteria:

- `POST /work/run` and `POST /reviews/run` are protected and scoped.
- `POST /run-once` on worker and reviewer are protected and scoped.
- Runner ingress accepts only explicitly allowed service identities.
- Missing, invalid, and wrong-scope requests are rejected and minimally audited.
- Negative tests cover all runner ingress paths.

Depends on:

- Stories EH-01 and EH-02.

### Story EH-A3: Introduce per-caller credentials and remove unauthenticated internal trust

Problem reduced:
The system relies on implicit internal trust instead of explicit caller identity.

Acceptance criteria:

- Distinct credentials exist for `archon`, `worker`, `reviewer`, `mcp`, `operator`, and `readonly`.
- Worker, reviewer, and MCP clients attach credentials on all Archon calls.
- Archon-to-runner calls attach credentials on runner ingress requests.
- Bootstrap, compose wiring, smoke tests, validation scripts, and README command examples are updated to use the new model.
- Rotation and revocation behavior is documented for Release A.

Depends on:

- Stories EH-01 and EH-A1.

### Story EH-A4: Authenticate, authorize, trace, and audit Archon MCP tool calls

Problem reduced:
MCP remains a privileged but ambiguously governed mutation path.

Acceptance criteria:

- MCP has its own service identity and allowed-scope set.
- Each mutating MCP tool is mapped to an allowed Archon action.
- MCP tool calls propagate a request ID into Archon.
- Every mutating MCP tool call emits minimal audit evidence with actor, action, target, outcome, and request ID.
- Wrong-scope and unauthenticated MCP calls are rejected and audited.

Depends on:

- Stories EH-01, EH-02, and EH-A3.

### Story EH-A5: Define and protect operator versus readonly flows

Problem reduced:
Manual usage paths remain over-privileged or undefined.

Acceptance criteria:

- `operator` and `readonly` identities are distinct.
- Mutating commands require `operator`.
- Read/list endpoints that remain accessible to `readonly` are explicitly listed in the scope matrix.
- README examples specify which identity is required for each operator command.

Depends on:

- Stories EH-02 and EH-A3.

## Epic 2: Release A Traceability, Minimal Audit, and Raw Output Control

Outcome:
Release A actions are attributable, minimally auditable, and do not leak raw model output by default.

### Story EH-A6: Generate and propagate request IDs across Archon, MCP, and runners

Problem reduced:
A single privileged action cannot be traced across services.

Acceptance criteria:

- A canonical request ID header and generation rule are defined.
- Archon creates a request ID if one is absent or untrusted.
- MCP and runner calls propagate the active request ID.
- Request IDs appear in responses, logs, and minimal audit records where relevant.
- Negative tests cover malformed or untrusted inbound request IDs.

Depends on:

- Story EH-A3.

### Story EH-A7: Add minimal auth/authz audit substrate in Release A

Problem reduced:
Release A requires deny decisions to be auditable, but durable audit was previously deferred too late.

Acceptance criteria:

- Minimal audit records exist for auth failures, authz denials, task mutations, review mutations, approval mutations, MCP mutations, and manual run triggers.
- The minimal schema includes timestamp, request ID, actor identity, action, target, outcome, and deny reason where applicable.
- Audit requirements do not create a dependency loop that blocks auth success when the durable audit system is not yet implemented.
- Release A tests assert minimal audit presence for critical paths.

Depends on:

- Stories EH-A1 through EH-A4 and EH-A6.

### Story EH-A8: Control raw model output persistence before telemetry expansion

Problem reduced:
Sensitive model output is stored in `worker_runs.raw_output` and `reviews.raw_output` by default or without bounds.

Acceptance criteria:

- `raw_output` is default-off in Release A.
- Any opt-in storage mode is explicit, documented, and limited to allowed operators.
- Maximum size, truncation behavior, and redaction policy are defined.
- Existing rows and migration behavior are explicitly addressed.
- Tests cover empty, typical, large, truncated, secret-containing, and malformed output cases.

Depends on:

- Stories EH-00 and EH-03.

### Story EH-A9: Add structured error classification for security-critical paths

Problem reduced:
Operators cannot tell auth failure, authz failure, validation failure, and system failure apart.

Acceptance criteria:

- Security-critical paths classify errors as `auth`, `authz`, `validation`, `dependency`, or `internal`.
- Error logs and audit records include machine-readable reason codes.
- Sensitive data is never included in error output.

Depends on:

- Stories EH-A6 and EH-A7.

## Epic 3: Release A Operator and Validation Migration

Outcome:
The hardened Release A path is usable and verifiable, not just theoretically secure.

### Story EH-A10: Migrate operator commands, docs, and scripts to authenticated mode

Problem reduced:
Auth hardening breaks README workflows, curl examples, or scripts and drives teams back to insecure bypasses.

Acceptance criteria:

- Operator curl examples use authenticated flows.
- Health, smoke test, and live validation commands are updated for protected versus public endpoints.
- Bootstrap steps document how each identity receives credentials in local/staging use.
- README and planning docs reference only files present on the PR branch or clearly mark future deliverables.

Depends on:

- Stories EH-A1 through EH-A5.

### Story EH-A11: Define Release A validation matrix and exit criteria

Problem reduced:
Release A approval is based on aspiration rather than measurable evidence.

Acceptance criteria:

- Release A exit criteria are explicit for Archon ingress, runner ingress, MCP, request ID propagation, minimal audit, raw output controls, and authenticated operator flows.
- Each validation probe has owner, environment, command, expected result, and blocking/non-blocking status.
- Staging-like smoke validation is part of Release A signoff.

Depends on:

- Stories EH-A6 through EH-A10.

## Epic 4: Durable Audit and Schema Migration

Outcome:
Audit becomes durable, queryable, and migration-safe.

### Story EH-B1: Implement the durable audit schema on top of versioned migrations

Problem reduced:
Audit persistence lands without schema control or upgrade discipline.

Acceptance criteria:

- Durable audit schema changes use the versioned migration framework from EH-03.
- Required durable fields include actor identity, actor type, request ID, action, target, outcome, reason, and timestamp.
- Forward migration, idempotent rerun, and rollback/restore behavior are tested.

Depends on:

- Stories EH-03 and EH-A7.

### Story EH-B2: Persist durable audit records and expose restricted query paths

Problem reduced:
Minimal audit exists but cannot support real incident review.

Acceptance criteria:

- Durable audit survives restarts.
- Query paths exist for time, actor, action, target, and request ID.
- Access to audit query/export is itself protected and audited.
- Failure behavior is defined when durable audit persistence is degraded.

Depends on:

- Story EH-B1.

### Story EH-B3: Enforce audit completeness for privileged workflows

Problem reduced:
Critical paths still ship without durable evidence.

Acceptance criteria:

- Archon, MCP, runner manual triggers, task transitions, review writes, and approvals have durable audit coverage.
- Missing audit coverage is detectable in tests.
- Audit completeness is validated for success and deny paths.

Depends on:

- Story EH-B2.

## Epic 5: Data Retention and Privacy Controls

Outcome:
Stored data classes are governed explicitly and safely.

### Story EH-B4: Inventory retained data and assign owners and purpose

Problem reduced:
Retention policy is written without a real data inventory.

Acceptance criteria:

- Inventory includes logs, minimal audit, durable audit, `raw_output`, task descriptions, metadata, findings, follow-up, approval notes, and validation artifacts.
- Each data class has owner, purpose, and retention recommendation.

Depends on:

- Story EH-00.

### Story EH-B5: Define retention defaults and purge behavior

Problem reduced:
Retention remains indefinite or inconsistent across data classes.

Acceptance criteria:

- Finite defaults are defined for each data class unless explicitly justified otherwise.
- Purge behavior, dry-run mode, and operator controls are documented.
- Tests cover policy parsing, purge selection, and retention differences between audit and `raw_output`.

Depends on:

- Stories EH-B1, EH-B4, and EH-A8.

## Epic 6: CI Security Validation Gates

Outcome:
The security model is enforced continuously by real pipelines.

### Story EH-C1: Create or reconcile the CI baseline for this hardening path

Problem reduced:
The plan refers to CI gates without proving whether the required pipeline exists in this PR/repo context.

Acceptance criteria:

- The planning set states whether CI is being created or enhanced for this path.
- Required jobs are listed explicitly.
- Cross-links reference only CI workflows present in the repo or created by the future implementation story.

Depends on:

- Story EH-00.

### Story EH-C2: Add blocking security validation gates

Problem reduced:
Critical regressions can merge without exercising auth, MCP, runner ingress, migration, or raw output behavior.

Acceptance criteria:

- CI gates cover unauthenticated and wrong-scope failures on Archon, MCP, and runner ingress paths.
- CI gates cover request ID propagation, minimal audit assertions, migration tests, and raw output regression tests.
- Blocking versus informational jobs are defined explicitly.

Depends on:

- Stories EH-A11, EH-B1, EH-A8, and EH-C1.

## Epic 7: Runbooks, Rollout, and Readiness

Outcome:
The hardened path can be rolled out, rotated, investigated, and restored safely.

### Story EH-C3: Create rollout and rollback runbooks for the hardened path

Problem reduced:
Security controls exist but adoption is unsafe or manual rollback is undefined.

Acceptance criteria:

- Runbooks cover bootstrap, rollout, validation, rollback, and restore where rollback is not supported.
- Irreversible migration and credential-change boundaries are identified.
- Staging execution of the runbooks is part of signoff.

Depends on:

- Stories EH-A10, EH-A11, and EH-B1.

### Story EH-C4: Create credential rotation and incident investigation runbooks

Problem reduced:
Operators cannot safely rotate service credentials or investigate suspicious activity.

Acceptance criteria:

- Runbooks cover provisioning, rotation, revocation, and incident tracing with request IDs and audit records.
- The procedures are exercised in a staging-like environment.

Depends on:

- Stories EH-A3, EH-A6, and EH-B2.

### Story EH-C5: Publish the production readiness checklist

Problem reduced:
Teams deploy without a consistent gate for the hardened control plane.

Acceptance criteria:

- Checklist includes ingress protection, scope enforcement, MCP coverage, request ID propagation, minimal and durable audit, raw output controls, retention settings, CI gates, and runbook validation.
- Ownership is explicit.

Depends on:

- Epics 1 through 7 materially complete.

## Risk Reduction Mapping

| Risk | Primary stories |
|---|---|
| Unauthenticated access | EH-A1, EH-A2, EH-A3, EH-A4 |
| Over-broad privilege | EH-02, EH-A4, EH-A5 |
| No traceability | EH-A6, EH-A7, EH-A11 |
| No auditability | EH-A7, EH-B1, EH-B2, EH-B3 |
| Sensitive output leakage | EH-A8, EH-B4, EH-B5 |
| Unsafe rollout | EH-A10, EH-A11, EH-C3, EH-C4 |
| Migration breakage | EH-03, EH-B1, EH-C3 |
| False CI confidence | EH-C1, EH-C2 |

## Non-Goals for Release A

- broad human RBAC or ABAC
- SIEM, SOC, or dashboard-first observability work
- PostgreSQL migration as a prerequisite
- large-scale performance or chaos testing
- enterprise-wide documentation refresh outside authenticated control-plane flows

## Cross-Epic Definition of Done

A story is not ready for review unless:

- acceptance criteria are met with at least one negative-path case for auth, trust, output, or migration stories
- dependencies and out-of-scope items are explicit
- tests are identified by level: unit, integration, migration, or ops validation
- repo-truth assumptions are explicit and accurate
- CI status expectations are documented as create versus enhance, not implied

