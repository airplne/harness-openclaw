# ADR: Governed hybrid BMAD layer

Status: Proposed

## Context

`harness-openclaw` already has a governed worker/reviewer architecture. Archon owns task, review, approval, and visibility state. `archon-worker` performs implementation work through the OpenClaw CLI. `codex-reviewer` performs authoritative review through the governed OpenAI Codex OAuth path. The repository also has deterministic config rendering, runtime agent/model validation, smoke tests, and live validation artifacts.

BMAD is a workflow-and-skill oriented development framework. It can provide planning, architecture, story, implementation, and QA assistance, but integrating it as unmanaged executable plugin code would introduce supply-chain, approval-bypass, and config-drift risk.

## Decision

Adopt BMAD as a governed hybrid layer:

- Curated BMAD adapter skills live under `skills/*`.
- Archon task metadata selects BMAD behavior.
- The existing `archon-worker` remains the MVP execution path.
- The existing `codex-reviewer` remains the authoritative review path.
- A single optional `bmad-planner` agent may be added later only if validation evidence supports it.
- OpenClaw `--thinking high` support is opt-in and metadata/env-controlled.
- Unofficial BMAD/OpenClaw community integrations are research references only and are not vendored or executed in the MVP.

## Consequences

Positive consequences:

- Preserves the current worker/reviewer split.
- Preserves Archon as the source of truth for task, review, approval, and claims.
- Avoids uncontrolled plugin execution.
- Keeps BMAD-disabled behavior unchanged.
- Allows incremental validation through existing smoke/live validation scripts.

Tradeoffs:

- The MVP may expose fewer BMAD personas than a full BMAD installation.
- Some BMAD workflow richness is deferred until optional workflow expansion.
- Runtime support for thinking levels depends on provider/model support and must be validated.

## Alternatives considered

### BMAD as unmanaged OpenClaw plugin

Rejected for MVP. It creates unnecessary supply-chain and execution risk and may conflict with deterministic config rendering.

### BMAD as six or more dedicated agents

Rejected for MVP. This creates agent sprawl, model-binding complexity, and unclear ownership before there is validation evidence.

### BMAD as a new worker service

Rejected. A new daemon would bypass or duplicate the current `openclaw-worker` claim/persist loop.

### BMAD as Archon-only workflow layer

Deferred. Workflow templates may be useful later, but the MVP should prove skill-and-metadata routing first.

### BMAD as MCP-only workflow library

Deferred. MCP tools can mutate Archon state today, including review/approval surfaces, so any BMAD MCP additions must be bounded and evidence-gated.

### Hybrid governed layer

Accepted. It matches BMAD's skill/workflow structure while preserving this repository's governance model.

## Security implications

- BMAD artifacts must be treated as untrusted task context.
- BMAD QA cannot approve tasks.
- Community plugins must not be executed in the MVP.
- Codex reviewer OAuth checks must remain unchanged.
- Shell safety must preserve argv-based subprocess execution and avoid `shell=True`.
- Thinking controls must be explicit, bounded, and recorded in validation evidence.

## Validation requirements

Future implementation PRs must preserve current validation artifacts and add BMAD-specific evidence for config, skills, optional agents, thinking support, and lifecycle persistence. BMAD-disabled smoke/live validation must remain behaviorally unchanged.
