# Governed BMAD integration plan for harness-openclaw

Status: planned  
Owner: GPT dev planning team  
Scope: planning package for follow-up implementation PRs

## Executive recommendation

Add BMAD as a governed hybrid layer inside the existing harness instead of as an unmanaged OpenClaw plugin, a new worker service, or a parallel approval path. The recommended architecture is BMAD adapter skills plus Archon task metadata, executed through the existing `archon-worker` and reviewed by the existing `codex-reviewer`. A future optional `bmad-planner` agent may be added only after MVP evidence shows value. OpenClaw thinking controls should be used through explicit, metadata-controlled `--thinking high` support rather than a global ultrathink default.

This planning PR does not implement BMAD runtime behavior. It records the architecture, epics, stories, security model, validation expectations, and follow-up PR sequence.

## Problem statement

BMAD provides staged product and engineering workflows such as analysis, planning, solutioning, implementation, story creation, QA assistance, corrective-course work, status checks, and retrospectives. The harness already has a strict governed lifecycle: Archon owns task, review, approval, and visibility state; OpenClaw runs dedicated agents; Ollama handles routine worker execution; Codex handles review-only work through OAuth.

The challenge is to introduce BMAD workflow value without weakening the existing guarantees around worker/reviewer separation, persisted state, governed reviewer credentials, deterministic configuration, and operator validation.

## Goals

- Keep BMAD inside the existing Archon-controlled worker/reviewer lifecycle.
- Start with curated BMAD adapter skills under `skills/*`.
- Use task metadata as the BMAD control surface.
- Preserve `archon-worker` as the implementation executor for the MVP.
- Preserve `codex-reviewer` as the authoritative reviewer.
- Add optional OpenClaw `--thinking high` support only when explicitly selected.
- Extend existing smoke/live validation evidence rather than introducing a separate validation subsystem.
- Document a staged implementation path with small, reviewable PRs.

## Non-goals

- No unmanaged BMAD/OpenClaw community plugin execution in the MVP.
- No new worker service in the MVP.
- No new Archon statuses in the MVP.
- No approval or review bypass.
- No global ultrathink enablement.
- No manual `api_key` or `token` credentials for the governed `openai-codex` reviewer path.
- No `shell=True` or shell interpolation of task content.
- No vendored third-party BMAD/OpenClaw plugin code.

## Current harness constraints

The repository currently implements a governed hybrid OpenClaw harness with:

- `openclaw-worker` invoking the real OpenClaw CLI with the dedicated `archon-worker` agent.
- `openclaw-reviewer` invoking the real OpenClaw CLI with the dedicated `codex-reviewer` agent.
- Archon persisting tasks, task claims, worker runs, reviews, approvals, and final task state.
- Canonical statuses: `queued`, `working`, `review_requested`, `reviewing`, `pending_human_approval`, `approved`, `needs_changes`, `rejected`, and `failed`.
- OAuth-only governed reviewer access for the `openai-codex` provider.
- Runtime validation of configured agent/model bindings.
- Argv-based subprocess execution of `openclaw`, with no shell interpolation.
- A config renderer that canonically rebuilds controlled OpenClaw sections and strips deprecated/uncontrolled keys.
- Live validation artifacts under `.data/validation/latest`.

BMAD must extend these contracts rather than bypass them.

## BMAD architecture summary

BMAD is best treated as a workflow-and-skill oriented system. Its useful concepts for this harness are:

- staged phases: analysis, planning, solutioning, implementation, and QA assistance;
- workflow artifacts: PRDs, architecture docs, stories, corrective-course notes, status checks, and retrospectives;
- role-like skills or agents: analyst, PM, architect, developer, QA assistant, and orchestrator patterns;
- controlled output paths such as `_bmad-output`;
- project-context style guidance that can act as an implementation constitution.

The MVP should implement a small adapter layer rather than attempting to mirror every BMAD persona as a separate OpenClaw agent.

## Recommended integration approach

### MVP architecture

1. Add `skills/bmad-orchestrator/SKILL.md` as the first curated adapter skill.
2. Update `skills/archon-worker/SKILL.md` to recognize BMAD metadata and route its planning/implementation response accordingly.
3. Update `skills/codex-reviewer/SKILL.md` so Codex can review BMAD artifacts without giving BMAD QA approval authority.
4. Define a strict task metadata contract for BMAD phase, workflow, artifact path, and reasoning mode.
5. Add optional runner support for `--thinking`, default disabled.
6. Add smoke/live validation evidence for BMAD-enabled tasks in follow-up PRs.

### Medium-term architecture

- Add optional `skills/bmad-dev/SKILL.md` and `skills/bmad-qa-assist/SKILL.md` if the orchestrator contract becomes too broad.
- Add one optional `bmad-planner` OpenClaw agent only after MVP evidence shows planning quality or consistency improves.
- Add deterministic config/onboarding support only where needed.

### Long-term architecture

- Add bounded Archon workflow expansion for BMAD phase tasks.
- Prefer parent/child relationships and metadata over new statuses.
- Add bounded MCP tools only for safe task creation or workflow expansion.
- Never allow BMAD tools to approve, reject, or bypass review.

## Task metadata contract

Initial metadata shape:

```json
{
  "bmad": {
    "enabled": true,
    "phase": "planning",
    "workflow": "create-prd",
    "track": "bmad-method",
    "artifact_prefix": "_bmad-output",
    "approval_required": true
  },
  "reasoning_mode": "high"
}
```

Allowed initial BMAD phases:

- `analysis`
- `planning`
- `solutioning`
- `implementation`
- `qa_assist`

Allowed initial BMAD workflows:

- `brainstorm`
- `research`
- `create-prd`
- `create-architecture`
- `create-story`
- `implement-story`
- `qa-assist`
- `corrective-course`
- `status-check`
- `retrospective`

Invalid values should be rejected before use once runtime support is added. BMAD-disabled tasks must behave exactly as they do today.

## Ultrathink and thinking controls

OpenClaw supports per-invocation thinking controls through `openclaw agent --thinking <level>`. In this plan, `ultrathink` maps to `high`, and `ultrathink+` maps to `xhigh`.

Allowed thinking values:

- `off`
- `minimal`
- `low`
- `medium`
- `high`
- `xhigh`
- `adaptive`
- `max`

Aliases:

- `ultrathink` => `high`
- `ultrathink+` => `xhigh`

Thinking should be opt-in and metadata/env-controlled. It should not be globally enabled for routine implementation. Future validation should record whether the provider accepted, rejected, or skipped the requested level.

## Security model

BMAD artifacts are task inputs, not authority. They must not override Archon lifecycle rules, approval policy, credential policy, or shell-safety requirements.

Key security commitments:

- no unmanaged community plugin execution in MVP;
- no direct approval by BMAD QA;
- no bypass of Codex review;
- no manual credentials for governed reviewer provider;
- no shell command interpolation from task or artifact text;
- strict metadata validation before runtime use;
- deterministic config rendering;
- validation evidence for any thinking-level support.

The detailed threat model is in `docs/security/bmad-openclaw-threat-model.md`.

## Validation model

Existing validation artifacts must remain intact. Future BMAD PRs should add:

- `.data/validation/latest/bmad-config.json`
- `.data/validation/latest/bmad-agent-inventory.json`
- `.data/validation/latest/bmad-skill-inventory.json`
- `.data/validation/latest/bmad-thinking-check.json`
- `.data/validation/latest/bmad-task-lifecycle.json`

Existing artifacts such as health checks, render checks, auth profiles, agent bindings, smoke-test logs, tasks, claims, worker runs, reviews, and approvals must continue to be produced.

## Rollout plan

1. Land this docs-only planning PR.
2. Add BMAD adapter skill contracts.
3. Add additive thinking support in the runner.
4. Add optional config/onboarding support.
5. Add BMAD smoke/live validation artifacts.
6. Evaluate whether an optional `bmad-planner` agent is needed.
7. Evaluate whether Archon workflow expansion is needed.

## Rollback plan

Every implementation PR should preserve a BMAD-disabled path that matches current behavior. If BMAD support causes regressions, operators should be able to disable BMAD metadata handling and thinking overrides without changing the worker/reviewer lifecycle or Codex review policy.

## Open questions

- Does the exact OpenClaw config schema in the deployed version require renderer changes for a future optional `bmad-planner`, or are workspace skills plus metadata sufficient?
- Which deployed worker/reviewer models accept `high`, `xhigh`, `adaptive`, and `max` thinking values?
- Does metadata alone support useful BMAD phase execution, or should Archon later add explicit parent/child task relationships?
- Should official BMAD assets be generated with a pinned installer in a controlled future PR, or should the harness remain limited to minimal adapter skills?
