# Harness OpenClaw

This repo now wires a **real OpenClaw runtime base image**, **Ollama** as the routine worker path, **Codex** as the isolated scheduled reviewer, and **Archon** as the task / approval / operator layer.

## What is real in this repository

- `openclaw-gateway` runs from the official `ghcr.io/openclaw/openclaw:latest` image via a derived runtime image.
- `openclaw-worker` invokes the real `openclaw` CLI with the dedicated `archon-worker` agent.
- `openclaw-reviewer` invokes the real `openclaw` CLI with the dedicated `codex-reviewer` agent.
- `archon` persists tasks, worker runs, reviews, approvals, and task-claim leases in SQLite.
- `services/archon-mcp/server.py` is a real stdio MCP server, and `.openclaw/openclaw.json` points OpenClaw at it through `mcp.servers.archon`.
- `POST /work/run` and `POST /reviews/run` are no longer stubs; they forward to the worker and reviewer services.
- `scripts/run-smoke-test.sh` now asserts persisted worker and review records, not just health checks.

## Architecture

- **OpenClaw** = runtime / orchestration CLI and gateway
- **Ollama** = heavy-lift worker model for `archon-worker`
- **Codex** = review-only model for `codex-reviewer`
- **Archon** = tasks, reviews, approvals, and operator visibility
- **Archon MCP** = real stdio MCP server that OpenClaw can launch from config

## Canonical task flow

1. Create a task in Archon with status `queued`.
2. `openclaw-worker` claims the task and runs `/skill archon-worker` through the real `openclaw` CLI.
3. Archon persists the worker run and moves the task to `review_requested`.
4. `openclaw-reviewer` runs on `REVIEW_CRON` and invokes `/skill codex-reviewer` through the real `openclaw` CLI.
5. Archon persists the review and moves the task to one of:
   - `pending_human_approval`
   - `approved`
   - `needs_changes`
   - `rejected`
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

That onboarding command runs **inside the official OpenClaw gateway image**, stores auth under `.data/openclaw-config`, creates the dedicated worker and reviewer agents, and pulls the local Ollama model.

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
ARCHON_API_BASE_URL=http://localhost:8080 \
OPENCLAW_GATEWAY_HTTP_BASE_URL=http://localhost:18789 \
OLLAMA_HTTP_BASE_URL=http://localhost:11434 \
bash scripts/run-smoke-test.sh
```

This smoke test now verifies:

- the task was created
- a worker run was persisted
- a review run was persisted
- the task advanced into a post-review state

## Validation checklist

Gateway health:

```bash
curl http://localhost:18789/healthz
```

Archon health:

```bash
curl http://localhost:8080/health | jq
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

## Important runtime notes

- The worker/reviewer split is enforced by **dedicated OpenClaw agents**:
  - `archon-worker` must use `OPENCLAW_WORKER_MODEL`
  - `codex-reviewer` must use `OPENCLAW_REVIEW_MODEL`
- The reviewer service rejects healthy status unless the configured model name starts with `openai-codex/`.
- The runtime no longer shells task content into `shell=True`; all `openclaw` execution uses argv-based subprocess calls.
- The OpenClaw config and runtime files are rendered from `.env` through `scripts/render-openclaw-config.py`.

## Files to inspect

- `docker-compose.yml`
- `.env.example`
- `.openclaw/openclaw.json`
- `services/archon-control-plane/app.py`
- `services/archon-mcp/server.py`
- `services/openclaw-runtime/Dockerfile`
- `services/openclaw-runtime/worker_loop.py`
- `services/openclaw-runtime/review_loop.py`
- `skills/archon-worker/SKILL.md`
- `skills/codex-reviewer/SKILL.md`
