# Harness OpenClaw: Ollama worker + Codex reviewer + Archon approval plane

This repository implements a hybrid OpenClaw harness with:

- **Ollama** as the default heavy-lift worker
- **Codex** reserved for the scheduled `/skill codex-reviewer` quality gate
- **Archon** as the task, approval, and operator visibility layer
- a 5-minute automated review loop, matching the recommended hybrid pattern from the research brief

## What is in the stack

- `ollama` — local model runtime for the primary worker model
- `openclaw` — gateway container that hosts the OpenClaw workspace, config, skills, and review execution hook
- `archon` — central control plane for task intake, task state, review history, and human approval decisions
- `review-scheduler` — idempotent sidecar that asks Codex to run `/skill codex-reviewer` every 5 minutes

The OpenClaw config in `.openclaw/openclaw.json` follows the required pattern:

- primary model: `ollama/qwen3-coder:latest`
- Codex fallback/reviewer: `openai-codex/gpt-5.4`
- heartbeat: `5m`
- isolated cron payload calling `/skill codex-reviewer` every 5 minutes

## One-command local setup

```bash
cp .env.example .env
docker compose up -d --build
```

Optional bootstrap step:

```bash
bash scripts/bootstrap.sh
```

Pull the local worker model into Ollama:

```bash
docker compose exec ollama ollama pull qwen3-coder:latest
```

## Important live-wire setting

The harness ships with a **safe mock OpenClaw runner** so the stack can boot and the review loop can be smoke-tested immediately.

To switch into a real OpenClaw runtime, edit `.env` and replace `OPENCLAW_SKILL_RUN_CMD` with your actual OpenClaw CLI invocation. The command template receives three substitutions:

- `{message}`
- `{model}`
- `{config}`

Example shape:

```bash
OPENCLAW_SKILL_RUN_CMD=openclaw agent turn --config "{config}" --model "{model}" --message "{message}"
```

Use the exact command form supported by your installed OpenClaw build.

## Architecture and task flow

1. A user, automation, or agent creates a task in Archon.
2. OpenClaw routes routine work to the local Ollama primary model.
3. When work needs review, the task remains in `working` or `review_requested`.
4. Every 5 minutes, the isolated Codex review loop triggers `/skill codex-reviewer`.
5. The review result is recorded back into Archon as `approved`, `needs_changes`, `rejected`, or `pending_human_approval`.
6. A human operator can approve or reject the work through the Archon approval layer.

## Exact commands

Bring the stack up:

```bash
docker compose up -d --build
```

Inspect service status:

```bash
docker compose ps
docker compose logs -f archon
docker compose logs -f openclaw
docker compose logs -f review-scheduler
```

Create a task:

```bash
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement repo change",
    "description": "Have the Ollama worker make the change, then wait for Codex review.",
    "status": "working",
    "source": "manual"
  }'
```

List tasks and reviews:

```bash
curl http://localhost:8080/tasks | jq
curl http://localhost:8080/reviews | jq
curl http://localhost:8080/approvals | jq
```

Record a human approval:

```bash
curl -X POST http://localhost:8080/approvals \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": 1,
    "decision": "approved",
    "reviewer": "operator",
    "notes": "Looks good."
  }'
```

Run the smoke test:

```bash
ARCHON_API_BASE_URL=http://localhost:8080 \
OPENCLAW_GATEWAY_BASE_URL=http://localhost:8090 \
OLLAMA_HTTP_BASE_URL=http://localhost:11434 \
bash scripts/run-smoke-test.sh
```

## Verification checklist

Confirm Ollama is serving:

```bash
curl http://localhost:11434/api/tags | jq
```

Confirm OpenClaw is configured for a local primary model:

```bash
cat .openclaw/openclaw.json | jq '.agents.defaults.model'
```

Confirm the review scheduler is alive:

```bash
curl http://localhost:8079/health | jq
```

Confirm Archon is receiving task and approval state:

```bash
curl http://localhost:8080/tasks | jq
curl http://localhost:8080/approvals | jq
curl http://localhost:8080/reviews | jq
```

## Failure modes and recovery

- **Ollama has no model pulled**  
  Pull the configured model with `docker compose exec ollama ollama pull qwen3-coder:latest`.

- **Codex review loop runs but only returns mock results**  
  Replace `OPENCLAW_SKILL_RUN_CMD` in `.env` with your real OpenClaw CLI command.

- **Review loop is not moving tasks**  
  Check `docker compose logs -f review-scheduler` and verify tasks are still in `working` or `review_requested`.

- **Need a different review cadence**  
  Edit `REVIEW_INTERVAL_SECONDS` and `REVIEW_CRON` in `.env`, then restart `review-scheduler` and `openclaw`.

## Files of interest

- `docker-compose.yml`
- `.env.example`
- `.openclaw/openclaw.json`
- `skills/codex-reviewer/SKILL.md`
- `services/archon-control-plane/`
- `services/openclaw-gateway/`
- `services/review-scheduler/`
- `scripts/bootstrap.sh`
- `scripts/render-openclaw-config.py`
- `scripts/run-smoke-test.sh`
