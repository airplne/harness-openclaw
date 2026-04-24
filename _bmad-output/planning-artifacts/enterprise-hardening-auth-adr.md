# ADR: Release A Credential Mechanism and Service Identity

Status: Proposed for backlog approval
Date: 2026-04-24

## Decision

Release A will use distinct opaque bearer service credentials with server-side metadata and file-based bootstrap for local and staging environments.

This ADR intentionally chooses a simple mechanism for the first secure-by-default release instead of leaving the auth layer ambiguous.

## Why This Mechanism

Opaque bearer credentials are chosen because they are:

- simple to bootstrap in a Docker Compose harness
- easy to rotate independently by caller identity
- compatible with current operator curl and script workflows
- sufficient for Release A goals without forcing JWT, OIDC, or mTLS complexity into the first control-plane hardening release

## Identities Covered

Release A identities:

- `archon`
- `worker`
- `reviewer`
- `mcp`
- `operator`
- `readonly`

Each identity gets:

- one credential
- one explicit scope set
- independent rotation and revocation

## Storage and Injection

- Local and staging bootstrap may use file-mounted credential material.
- Callers read credentials from explicit token files or equivalent explicit secret injection points.
- No caller may rely on inherited trust from network location or container namespace.

## Validation Model

For Release A, the server validates:

- credential presence
- credential validity
- credential revocation state
- caller identity
- caller scope against the route/tool scope matrix

Release A does not require self-describing JWT claims.

## Rotation and Revocation

- Rotation uses an overlap window where new credentials are accepted before old credentials are revoked.
- Revocation invalidates a single caller credential without forcing unrelated service restart beyond their own rotation path.
- Rotation and revocation procedures must be documented in the rollout runbooks.

## Local Development

- Local development may use explicit bootstrap-generated credentials.
- Any insecure bypass must be opt-in, noisy, and disallowed in non-dev mode.

## Deferred Alternatives

Deferred beyond Release A unless later stories intentionally promote them:

- JWT or OIDC service tokens
- mTLS workload identity
- external secret manager by default
- full human SSO integration

## Consequences

Positive:

- engineers can implement a consistent Release A auth model
- acceptance criteria can use precise language: missing, invalid, revoked, wrong-scope

Tradeoff:

- audience and expiry claims are not first-release primitives unless later adopted intentionally

