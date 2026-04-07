#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "[bootstrap] copied .env.example to .env"
fi

python3 "${ROOT_DIR}/scripts/render-openclaw-config.py"

if [[ "${OLLAMA_PULL_MODEL:-true}" == "true" ]]; then
  echo "[bootstrap] compose stack must be running before pulling the Ollama model"
  echo "[bootstrap] after 'docker compose up -d', run:"
  echo "  docker compose exec ollama ollama pull ${OLLAMA_MODEL:-qwen3-coder:latest}"
fi
