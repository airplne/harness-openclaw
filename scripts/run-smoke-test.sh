#!/usr/bin/env bash
set -euo pipefail

echo "[smoke] checking compose services"
docker compose ps

echo "[smoke] checking Archon health"
curl -fsS "${ARCHON_API_BASE_URL:-http://localhost:8080}/health" | python3 -m json.tool

echo "[smoke] checking OpenClaw gateway health"
curl -fsS "${OPENCLAW_GATEWAY_BASE_URL:-http://localhost:8090}/health" | python3 -m json.tool

echo "[smoke] checking Ollama tags"
curl -fsS "${OLLAMA_HTTP_BASE_URL:-http://localhost:11434}/api/tags" | python3 -m json.tool

echo "[smoke] creating a sample task"
curl -fsS -X POST "${ARCHON_API_BASE_URL:-http://localhost:8080}/tasks" \
  -H "Content-Type: application/json" \
  -d '{"title":"smoke-test","description":"validate hybrid harness","status":"working","source":"smoke"}' | python3 -m json.tool

echo "[smoke] requesting a review cycle"
curl -fsS -X POST "${ARCHON_API_BASE_URL:-http://localhost:8080}/reviews/run" \
  -H "Content-Type: application/json" \
  -d '{"reason":"manual smoke test"}' | python3 -m json.tool

echo "[smoke] done"
