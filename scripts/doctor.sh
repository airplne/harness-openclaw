#!/usr/bin/env bash
# Local diagnostics: prerequisites, config render checks, optional live health (Docker).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OPENCLAW_CONFIG_DIR="${OPENCLAW_CONFIG_DIR:-./.data/openclaw-config}"
ARCHON_API_TOKEN_PATH="${ARCHON_API_TOKEN_PATH:-$OPENCLAW_CONFIG_DIR/operator.token}"
ARCHON_PORT="${ARCHON_PORT:-8080}"

failures=0
note() { echo "[doctor] $*"; }
warn() { echo "[doctor] WARN: $*" >&2; }
err() { echo "[doctor] FAIL: $*" >&2; failures=$((failures + 1)); }

command -v python3 >/dev/null 2>&1 || err "python3 not found"
command -v docker >/dev/null 2>&1 || warn "docker not installed (skip compose checks)"

note "renderer self-test"
python3 scripts/render-openclaw-config.py --self-test >/dev/null || err "render self-test failed"

if [[ -f .openclaw/openclaw.json ]]; then
  note "verify tracked + runtime configs (if present)"
  python3 scripts/render-openclaw-config.py \
    --verify-file .openclaw/openclaw.json \
    --verify-file .data/openclaw-config/openclaw.json 2>/dev/null || warn "verify-file skipped (run bootstrap first)"
else
  warn ".openclaw/openclaw.json missing"
fi

if [[ -f "$ARCHON_API_TOKEN_PATH" ]]; then
  note "Archon API token file: $ARCHON_API_TOKEN_PATH"
else
  warn "no Archon token yet — run: bash scripts/bootstrap.sh"
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  note "docker compose services"
  docker compose ps 2>/dev/null || warn "docker compose ps failed"
  if docker compose ps --services --filter status=running 2>/dev/null | grep -q archon; then
    note "Archon health (unauthenticated)"
    curl -fsS "http://127.0.0.1:${ARCHON_PORT}/healthz" | python3 -m json.tool >/dev/null || err "Archon /healthz failed"
    curl -fsS "http://127.0.0.1:${ARCHON_PORT}/readyz" | python3 -m json.tool >/dev/null || err "Archon /readyz failed"
    if [[ -f "$ARCHON_API_TOKEN_PATH" ]]; then
      tok="$(tr -d '\n' <"$ARCHON_API_TOKEN_PATH")"
      note "Archon authenticated probe"
      curl -fsS -H "Authorization: Bearer ${tok}" "http://127.0.0.1:${ARCHON_PORT}/tasks" >/dev/null || err "Archon GET /tasks with token failed"
    fi
  else
    warn "archon container not running — start with: docker compose up -d --build"
  fi
else
  warn "Docker not running or not available; skipped live checks"
fi

if [[ "$failures" -eq 0 ]]; then
  note "all checks passed"
  exit 0
fi
err "$failures check(s) failed"
exit 1
