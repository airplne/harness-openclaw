#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

set -a
source .env
set +a

python3 scripts/render-openclaw-config.py

echo "[onboard] running official OpenClaw onboarding in the gateway image"
docker compose run --rm --no-deps --entrypoint node openclaw-gateway dist/index.js onboard --mode local --no-install-daemon

echo "[onboard] ensuring worker and reviewer agents exist"
docker compose run --rm openclaw-cli agents add archon-worker --workspace /workspace --model "$OPENCLAW_WORKER_MODEL" --non-interactive --json || true
docker compose run --rm openclaw-cli agents add codex-reviewer --workspace /workspace --model "$OPENCLAW_REVIEW_MODEL" --non-interactive --json || true

echo "[onboard] pulling local Ollama model"
docker compose exec ollama ollama pull "$OLLAMA_MODEL"

echo "[onboard] complete"
