# Enterprise Hardening Epics and Stories

Date: 2026-04-24
Project: `harness-openclaw`
Prepared by: Mary with BMAD party-mode roundtable input from John, Winston, Amelia, and Murat

## Objective

Close the highest-risk enterprise-readiness gaps in the control plane and runtime stack with a delivery sequence that reduces real risk first:

1. Make unauthenticated and untraceable control-plane actions impossible.
2. Establish scoped service identity and request traceability.
3. Build durable auditability and safe retention defaults.
4. Enforce the new controls with automated tests and CI gates.
5. Add operational runbooks and follow-through hardening after the core controls are real.

## Guiding Position

The first epic must be:

**Fail-Closed Archon Control Plane with Scoped Service Auth and Request Traceability**

Do not start with PostgreSQL migration, SIEM export, broad RBAC expansion, dashboards, or generic “enterprise security improvements.” Those belong after the trust boundary is real.

## Recommended Delivery Order

1. Epic 1: Fail-Closed Control Plane Authentication and Service Identity
2. Epic 2: Request Traceability and Structured Observability
3. Epic 3: Durable Audit Trail and Accountability
4. Epic 5: Automated Verification and Security Regression Coverage
5. Epic 4: Data Retention, Privacy, and Safe Defaults
6. Epic 6: Operational Readiness and Security Runbooks

## Release Slices

### Release A: Trusted Control Plane

Includes:

- Epic 1 stories 1.1 through 1.4
- Epic 5 story 5.1

Success metric:

- Zero successful unauthenticated privileged control-plane calls in test and staging.
- Missing required auth config prevents startup outside explicitly insecure local development mode.

### Release B: Traceable Actions

Includes:

- Epic 2 stories 2.1 through 2.3
- Epic 3 stories 3.1 and 3.2
- Epic 5 stories 5.2 and 5.3

Success metric:

- A privileged request can be traced end-to-end across Archon, worker, and reviewer using a shared request ID.
- Auth decisions and privileged actions are reconstructable from logs plus audit records.

### Release C: Operable and Defensible

Includes:

- Epic 3 stories 3.3 and 3.4
- Epic 4 stories 4.1 through 4.4
- Epic 5 story 5.4
- Epic 6 stories 6.1 through 6.3

Success metric:

- The team can rotate a service credential, investigate suspicious activity, and validate retention behavior without ad hoc code inspection.

## Epic 1: Fail-Closed Control Plane Authentication and Service Identity

Outcome:
No Archon control-plane request is accepted without valid authentication, and every caller has a distinct scoped identity.

### Story 1.1: Enforce fail-closed auth in the Archon request pipeline

Problem reduced:
Unauthorized or ambiguous access to the control plane.

Acceptance criteria:

- Any request missing a token is rejected with `401` or `403`.
- Any malformed, expired, unverifiable, or wrong-audience token is rejected.
- No code path allows “missing token means trusted/internal.”
- Startup fails in non-dev modes if required auth configuration is missing.
- Health behavior is explicit: public `healthz` only if intentionally allowed; privileged readiness remains protected if needed.

Non-goals:

- Full SSO or human-facing RBAC.

Dependencies:

- None.

### Story 1.2: Replace the shared token with per-service identities

Problem reduced:
Shared-secret blast radius and inability to attribute service actions.

Acceptance criteria:

- `worker`, `reviewer`, `operator/admin`, and `readonly` identities are distinct.
- Service credentials are independently revocable.
- Service identity is visible in request metadata and available to audit/logging layers.
- Worker credentials cannot perform reviewer or operator actions.
- Reviewer credentials cannot perform worker or operator actions.

Non-goals:

- External enterprise IAM federation.

Dependencies:

- Story 1.1.

### Story 1.3: Define and enforce authorization scopes per identity

Problem reduced:
Authenticated but over-privileged callers.

Acceptance criteria:

- A scope matrix maps identities to allowed endpoints/actions.
- Archon enforces least privilege at route/action level.
- Unauthorized but authenticated requests are denied and audited.
- The scope model is documented and versioned.
- Negative-path tests cover wrong-scope requests for all defined identities.

Non-goals:

- Fine-grained end-user permissions.

Dependencies:

- Story 1.2.

### Story 1.4: Harden bootstrap and secret handling for dev and non-dev modes

Problem reduced:
Implicit insecure defaults and unclear secret precedence.

Acceptance criteria:

- Startup fails clearly when required auth secrets are absent in non-dev modes.
- Local development has an explicit, documented bootstrap path.
- Insecure local bypass requires an explicit flag and is noisy in logs/health diagnostics.
- Compose examples stop normalizing a shared static secret as the default pattern.
- Secret sources and precedence are documented.

Non-goals:

- External secret manager integration by default.

Dependencies:

- Stories 1.1 through 1.3.

## Epic 2: Request Traceability and Structured Observability

Outcome:
Every meaningful control-plane action can be followed across services with structured logs and correlation identifiers.

### Story 2.1: Add request ID generation, validation, and propagation

Problem reduced:
Inability to trace a single action across Archon, worker, and reviewer.

Acceptance criteria:

- Every inbound request gets a request ID if one is not already present and trusted.
- Request IDs propagate across Archon, worker, reviewer, and internal callback paths.
- Request IDs are included in responses, logs, and audit records where appropriate.
- Integration tests verify end-to-end propagation.

Non-goals:

- Full distributed tracing platform rollout.

Dependencies:

- Epic 1 complete enough to identify callers.

### Story 2.2: Implement structured logging for privileged and security-critical events

Problem reduced:
Poor incident reconstruction and weak machine-readable telemetry.

Acceptance criteria:

- Logs are emitted in a defined structured format.
- Required fields include timestamp, service, environment, request ID, caller identity, action, outcome, and error classification.
- Auth failures, authz denials, config failures, and privileged actions are logged.
- Secrets and sensitive payloads are redacted by policy.
- Logging schema and examples are documented.

Non-goals:

- Dashboarding or SIEM integration.

Dependencies:

- Story 2.1.

### Story 2.3: Standardize error classification and operator diagnostics

Problem reduced:
Operators cannot distinguish malicious traffic from system failure.

Acceptance criteria:

- Errors are categorized consistently across services: `auth`, `authz`, `validation`, `dependency`, `internal`.
- Logs include actionable reason codes without leaking secrets.
- Diagnostics can distinguish caller fault from platform fault.
- Tests verify key error cases emit the expected class and code.

Non-goals:

- Advanced observability workflows.

Dependencies:

- Stories 2.1 and 2.2.

## Epic 3: Durable Audit Trail and Accountability

Outcome:
Security-relevant actions produce durable, queryable audit records with actor, action, target, time, and outcome.

### Story 3.1: Define the audit event schema

Problem reduced:
Current audit records are too shallow for enterprise investigation and accountability.

Acceptance criteria:

- Schema includes actor identity, actor type, action, target/resource, request ID, timestamp, outcome, and reason.
- Required audited events are enumerated: auth failures, authz denials, task claims/releases, worker runs, reviews, approvals, privileged config changes, retention/purge operations.
- Schema versioning strategy is defined.
- Team signoff confirms the schema supports incident review.

Non-goals:

- SIEM export.

Dependencies:

- Epic 2 request identity and correlation fields.

### Story 3.2: Persist audit events durably

Problem reduced:
Audit evidence disappears with process restarts or transient log sinks.

Acceptance criteria:

- Audit events are stored durably, not only emitted to stdout.
- Failed audit writes surface operationally and are handled explicitly.
- Audit storage survives restarts.
- Query access exists for time, actor, action, resource, and request ID.

Non-goals:

- Replacing all storage layers.

Dependencies:

- Story 3.1.

### Story 3.3: Ensure audit completeness for privileged workflows

Problem reduced:
Critical paths can execute without accountable evidence.

Acceptance criteria:

- Every privileged endpoint and workflow emits audit records on success and on denial/failure where appropriate.
- Missing audit coverage is test-detectable.
- Integration tests validate emitted audit records for core workflows.
- Audit data can reconstruct approval and review flows.

Non-goals:

- Full tamper-evidence chain outside documented scope.

Dependencies:

- Stories 3.1 and 3.2.

### Story 3.4: Add audit retention and export controls

Problem reduced:
Audit data lacks governance and operational extraction paths.

Acceptance criteria:

- Audit retention period is explicit, configurable, and documented.
- Export path exists for incident review and compliance needs.
- Export and retention-changing actions are themselves restricted and audited.

Non-goals:

- Full external compliance platform integration.

Dependencies:

- Story 3.2.

## Epic 4: Data Retention, Privacy, and Safe Defaults

Outcome:
The system stops retaining sensitive operational data indefinitely or by accident.

### Story 4.1: Inventory retained data by type and purpose

Problem reduced:
Unknown retained data creates unmanaged compliance and security risk.

Acceptance criteria:

- Stored data classes are documented: logs, audits, tokens/metadata, task payloads, findings, approvals, artifacts.
- Each class has an owner, purpose, and retention recommendation.
- Unknown or unowned retained data is flagged for removal or policy definition.

Non-goals:

- Customer-specific policy customization.

Dependencies:

- None, but should align with Epic 3 schema design.

### Story 4.2: Set explicit retention defaults for logs and audit data

Problem reduced:
Indefinite retention by default.

Acceptance criteria:

- Default retention is finite and environment-specific.
- Retention settings are configurable without code changes.
- Expiration and cleanup behavior is documented and tested.
- Dangerous or unbounded defaults require explicit override.

Non-goals:

- Long-term archival redesign.

Dependencies:

- Story 4.1 and audit persistence decisions from Epic 3.

### Story 4.3: Minimize sensitive data in logs and stored artifacts

Problem reduced:
Secret and sensitive data leakage through telemetry and stored outputs.

Acceptance criteria:

- Tokens, secrets, and clearly sensitive payload fields are redacted or excluded.
- Logging and audit helpers prevent accidental raw-secret emission in known paths.
- Representative redaction tests exist.

Non-goals:

- Broad prompt-governance framework.

Dependencies:

- Epic 2 structured logging and Epic 3 audit schema.

### Story 4.4: Add operator controls for retention and purge workflows

Problem reduced:
Retention operations are opaque and potentially unsafe.

Acceptance criteria:

- Authorized operators can adjust retention within defined bounds.
- Purge and cleanup operations are logged and audited.
- Unsafe “keep forever” defaults are removed.
- Dry-run mode exists before destructive purge execution.

Non-goals:

- Customer self-service retention UI.

Dependencies:

- Stories 4.1 through 4.3.

## Epic 5: Automated Verification and Security Regression Coverage

Outcome:
Security-critical controls are enforced continuously rather than assumed.

### Story 5.1: Build an auth and authorization automated test suite

Problem reduced:
Fail-closed and least-privilege controls regress silently.

Acceptance criteria:

- Automated tests cover fail-closed behavior, service identity separation, and scope enforcement.
- Tests run in CI.
- Auth regressions block merge.
- Negative-path scenarios are first-class, not optional.

Non-goals:

- High-volume performance testing.

Dependencies:

- Epic 1.

### Story 5.2: Add integration tests for end-to-end request traceability

Problem reduced:
Traceability controls appear correct but break across service boundaries.

Acceptance criteria:

- CI validates request ID propagation across control plane, reviewer, and worker.
- Tests assert logs and audit records can be correlated for a sample workflow.
- Test failures are diagnosable from artifacts.

Non-goals:

- Full chaos testing.

Dependencies:

- Epic 2 and at least partial Epic 3.

### Story 5.3: Add audit coverage tests for privileged workflows

Problem reduced:
New or changed privileged flows ship without audit evidence.

Acceptance criteria:

- Core privileged actions assert audit record presence and schema correctness.
- Denied actions are covered too.
- Coverage includes happy path and failure path.

Non-goals:

- External compliance reporting.

Dependencies:

- Epic 3.

### Story 5.4: Raise the CI quality gate from smoke-only to control validation

Problem reduced:
Pipeline passes without validating the controls that matter.

Acceptance criteria:

- CI runs unit and integration tests for auth, traceability, and audit controls.
- Minimum required checks are documented.
- Merge policy blocks shipping when critical security checks fail.

Non-goals:

- Large-scale browser automation.

Dependencies:

- Stories 5.1 through 5.3.

## Epic 6: Operational Readiness and Security Runbooks

Outcome:
The team can rotate credentials, investigate incidents, and verify production readiness repeatably.

### Story 6.1: Create the auth and credential rotation runbook

Problem reduced:
Credential incidents require improvised recovery.

Acceptance criteria:

- Runbook covers provisioning, rotation, revocation, and recovery for service identities.
- Procedure is exercised in a non-prod environment.
- Expected runtime behavior during rotation/revocation is documented.

Non-goals:

- Full self-service credential portal.

Dependencies:

- Epic 1 complete enough to define identities.

### Story 6.2: Create the incident investigation runbook using request IDs and audit data

Problem reduced:
Incident response depends on tribal knowledge.

Acceptance criteria:

- Runbook explains how to trace a request across services.
- Includes steps for investigating auth failures, unauthorized attempts, and suspicious privileged activity.
- Validated against a staged incident scenario.

Non-goals:

- Formal SOC integration.

Dependencies:

- Epics 2 and 3.

### Story 6.3: Define the production readiness checklist for control-plane security

Problem reduced:
Security-critical deployments ship without a consistent gate.

Acceptance criteria:

- Checklist includes fail-closed auth verification, unique service identities, request traceability, audit durability, retention settings, and CI gates.
- Checklist is required before production-like deployments.
- Ownership is explicit.

Non-goals:

- Enterprise-wide governance framework.

Dependencies:

- Epics 1 through 5 materially complete.

## Cross-Epic Definition of Done

A story is not ready for review unless all of the following are true:

- Acceptance criteria are met.
- Negative-path tests exist where auth, authz, retention, or audit behavior changed.
- Any schema or config migration has a tested compatibility or rollback path.
- Logs, request IDs, and audit assertions are present for privileged flows when relevant.
- CI required checks are green and not waived.

## Explicitly Deferred

These are intentionally later-phase items, not first-wave backlog starters:

- Broad human RBAC/ABAC expansion
- External enterprise IAM breadth
- SIEM and SOC integrations
- Dashboard-first observability work
- PostgreSQL migration as an initial prerequisite
- Large-scale performance and chaos engineering
- Multi-region or advanced scaling design

## Notes for the Dev Team

- Implement vertically, not horizontally: ingress auth, then service authz, then trace propagation, then audit, then retention, then CI gate enforcement.
- Do not split “code now, tests later.”
- Do not treat operational logs and audit records as the same artifact. They serve different operators and different failure modes.
