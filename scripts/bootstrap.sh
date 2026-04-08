#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[bootstrap] copied .env.example to .env"
fi

set -a
source .env
set +a

mkdir -p .data/openclaw-config .data/archon .data/ollama .data/validation
python3 scripts/render-openclaw-config.py
python3 scripts/render-openclaw-config.py \
  --verify-file .openclaw/openclaw.json \
  --verify-file .data/openclaw-config/openclaw.json

echo "[bootstrap] rendered OpenClaw config and local runtime directories"
echo "[bootstrap] next steps:"
echo "  docker compose up -d --build"
echo "  bash scripts/onboard-openclaw.sh"
