# Harness OpenClaw

This repo implements a hybrid OpenClaw harness with:

- **Ollama** as the routine worker path
- **Codex** as the review-only path through ChatGPT OAuth
- **Archon** as the task, review, and approval control plane
- **OpenClaw MCP** wiring for Archon tool access

## Authentication model

This harness no longer relies on environment variables for provider credentials.

Provider auth now follows the OpenClaw auth-profile pattern:

- Auth state lives under `.data/openclaw-config/**/auth-profiles.json`
- Codex reviewer access is provisioned through **OpenAI Codex OAuth**
- The harness rejects manual `api_key` or `token` auth profiles for the governed reviewer provider `openai-codex`
- Unrelated OpenClaw auth profiles can coexist; the harness validates only the providers it governs
- Local Ollama usage does not require a credential; when operators choose an authenticated Ollama setup, it must be established through OpenClaw onboarding rather than shell env injection

The compose stack also no longer injects provider credentials into container environments.

## What is real in this repository

- `openclaw-gateway` runs from a runtime derived from the official `ghcr.io/openclaw/openclaw:latest` image.
- `openclaw-worker` invokes the real `openclaw` CLI with the dedicated `archon-worker` agent.
- `openclaw-reviewer` invokes the real `openclaw` CLI with the dedicated `codex-reviewer` agent.
- `archon` persists tasks, worker runs, reviews, approvals, and task-claim leases in SQLite.
- `services/archon-mcp/server.py` is a real stdio MCP server, and `.openclaw/openclaw.json` points OpenClaw at it through `mcp.servers.archon`.
- `POST /work/run` and `POST /reviews/run` are real forwarding endpoints.
- `GET /claims` exposes active or historical worker/reviewer claim leases.
- `scripts/run-smoke-test.sh` asserts persisted worker and review records plus post-review task state.
- `scripts/run-live-validation.sh` captures live verification artifacts for operator review, including renderer scrub checks and exact agent binding verification.

## Architecture

- **OpenClaw** = runtime / orchestration CLI and gateway
- **Ollama** = heavy-lift worker model for `archon-worker`
- **Codex** = review-only model for `codex-reviewer`
- **Archon** = tasks, reviews, approvals, and operator visibility
- **Archon MCP** = real stdio MCP server launched from OpenClaw config

The only authoritative review scheduler is the `openclaw-reviewer` service running `services/openclaw-runtime/review_loop.py`. The OpenClaw config does not schedule independent review turns.

## Canonical task flow

1. Create a task in Archon with status `queued`.
2. `openclaw-worker` claims the task and runs `/skill archon-worker` through the real `openclaw` CLI.
3. Archon persists the worker run and moves the task to `review_requested`.
4. `openclaw-reviewer` runs on `REVIEW_CRON`, claims `review_requested` tasks through Archon, and invokes `/skill codex-reviewer` through the real `openclaw` CLI.
5. Archon persists the review and moves the task to one of:
   - `pending_human_approval`
   - `approved`
   - `needs_changes`
   - `rejected`
   - `failed`
6. A human approval can be recorded through `POST /approvals`.

## Setup

### 1) Prepare local config

```bash
cp .env.example .env
bash scripts/bootstrap.sh
```

### 2) Start the stack

```bash
docker compose up -d --build
```

### 3) Complete OpenClaw onboarding and seed the worker/reviewer agents

```bash
bash scripts/onboard-openclaw.sh
```

That onboarding flow:

- runs OpenClaw onboarding in the runtime image
- provisions Codex auth through OpenClaw OAuth
- configures Ollama through OpenClaw onboarding rather than shell credentials
- verifies the dedicated worker and reviewer agents exist with the expected model bindings
- verifies rendered config is scrubbed of deprecated fallback and fake-auth keys
- fails if manual non-OAuth credentials remain for the governed reviewer provider `openai-codex`

## Exact operator commands

Create a task:

```bash
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement repo change",
    "description": "Have the Ollama worker perform routine work, then require Codex review.",
    "status": "queued",
    "source": "manual"
  }'
```

Run one worker cycle immediately:

```bash
curl -X POST http://localhost:8080/work/run -H "Content-Type: application/json" -d '{}'
```

Run one review cycle immediately:

```bash
curl -X POST http://localhost:8080/reviews/run -H "Content-Type: application/json" -d '{}'
```

List persisted state:

```bash
curl http://localhost:8080/tasks | jq
curl http://localhost:8080/claims | jq
curl http://localhost:8080/worker-runs | jq
curl http://localhost:8080/reviews | jq
curl http://localhost:8080/approvals | jq
```

Approve or reject:

```bash
curl -X POST http://localhost:8080/approvals \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": 1,
    "decision": "approved",
    "reviewer": "operator",
    "notes": "Reviewed and accepted."
  }'
```

## Smoke test

After onboarding and model pull complete:

```bash
bash scripts/run-smoke-test.sh
```

The smoke test now verifies:

- the task was created
- a worker run was persisted
- a review run was persisted
- the task advanced into a post-review state

## Live validation

To generate the operator evidence bundle for the items that were previously “not fully proven”:

```bash
bash scripts/run-live-validation.sh
```

That script collects:

- Archon, worker, reviewer, and gateway health
- exact agent inventory and model bindings
- governed auth-profile summaries for `openai-codex`
- environment leak checks for deprecated auth vars
- renderer scrub self-test plus verification of the current rendered config files
- smoke-test results
- persisted `tasks`, `claims`, `worker-runs`, `reviews`, and `approvals`

Artifacts are written under `.data/validation/latest`.

## Validation checklist

Archon health:

```bash
curl http://localhost:8080/health | jq
```

Worker health:

```bash
docker compose exec -T openclaw-worker curl -fsS http://127.0.0.1:8091/health | jq
```

Reviewer health:

```bash
docker compose exec -T openclaw-reviewer curl -fsS http://127.0.0.1:8092/health | jq
```

Gateway health through the CLI container:

```bash
docker compose run --rm openclaw-cli gateway health --url ws://127.0.0.1:18789 --json
```

Ollama model list:

```bash
curl http://localhost:11434/api/tags | jq
```

Rendered OpenClaw config:

```bash
cat .openclaw/openclaw.json | jq
cat .data/openclaw-config/openclaw.json | jq
```

Renderer scrub self-test:

```bash
python3 scripts/render-openclaw-config.py --self-test | jq
```

## Important runtime notes

- The worker/reviewer split is enforced by **dedicated OpenClaw agents** and by runtime validation of the bound model for each agent.
- The only active review scheduler is `openclaw-reviewer` using `services/openclaw-runtime/review_loop.py`; `REVIEW_CRON` configures that service cadence and the OpenClaw config intentionally does not emit independent `cronJobs`.
- The reviewer service rejects healthy status unless:
  - the configured model exactly matches `OPENCLAW_REVIEW_MODEL`
  - the model starts with `openai-codex/`
  - an OAuth auth profile exists for `openai-codex`
  - no manual `api_key` or `token` credentials remain for the governed reviewer provider `openai-codex`
- Unrelated auth profiles can coexist; the harness does not globally ban every manual profile under `.openclaw`.
- The runtime no longer shells task content into `shell=True`; all `openclaw` execution uses argv-based subprocess calls.
- The OpenClaw config renderer rebuilds the controlled sections (`agents`, `models`, `mcp`) canonically, strips any legacy `cronJobs` block, and verifies that deprecated keys such as `agents.defaults.model.fallbacks` and `models.providers.ollama.apiKey` are absent after rendering.
- Review command failures now persist a first-class `failed` review result instead of silently downgrading into `pending_human_approval`.
- The gateway runs loopback-only inside the shared runtime namespace, so worker/reviewer health checks are container-local rather than host-published.
- `openclaw-gateway`, `openclaw-worker`, `openclaw-reviewer`, and `openclaw-cli` share the same logical runtime network surface; Archon and Ollama stay on the named Compose network `harness-openclaw`.

## What still requires operator-run validation

This repository now encodes the OAuth-based runtime shape and fail-fast validation logic, but two things still require an operator to run them in a real environment:

1. pulling and starting the official OpenClaw image through Docker
2. completing the live browser-based Codex OAuth flow

Those steps are exactly what `scripts/onboard-openclaw.sh` and `scripts/run-live-validation.sh` are for.

## Files to inspect

- `docker-compose.yml`
- `.env.example`
- `.openclaw/openclaw.json`
- `services/archon-control-plane/app.py`
- `services/archon-mcp/server.py`
- `services/openclaw-runtime/Dockerfile`
- `services/openclaw-runtime/runner_common.py`
- `services/openclaw-runtime/worker_loop.py`
- `services/openclaw-runtime/review_loop.py`
- `scripts/onboard-openclaw.sh`
- `scripts/render-openclaw-config.py`
- `scripts/run-live-validation.sh`
- `skills/archon-worker/SKILL.md`
- `skills/codex-reviewer/SKILL.md`
