#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

set -a
source .env
set +a

OUT_DIR=".data/validation/latest"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

echo "[validate] collecting health"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/health" | python3 -m json.tool > "$OUT_DIR/archon-health.json"
docker compose exec -T openclaw-worker curl -fsS http://127.0.0.1:8091/health | python3 -m json.tool > "$OUT_DIR/worker-health.json"
docker compose exec -T openclaw-reviewer curl -fsS http://127.0.0.1:8092/health | python3 -m json.tool > "$OUT_DIR/reviewer-health.json"
docker compose run --rm openclaw-cli gateway health --url ws://127.0.0.1:18789 --json > "$OUT_DIR/gateway-health.json"

echo "[validate] checking for deprecated auth env leakage"
docker compose run --rm --entrypoint /usr/bin/env openclaw-cli | grep -E '^(OLLAMA_API_KEY|OPENAI_API_KEY|OPENCLAW_GATEWAY_TOKEN)=' > "$OUT_DIR/deprecated-auth-env.txt" || true
if [[ -s "$OUT_DIR/deprecated-auth-env.txt" ]]; then
  echo "[validate] deprecated auth env vars detected"
  cat "$OUT_DIR/deprecated-auth-env.txt"
  exit 1
fi

echo "[validate] capturing auth profile summary"
docker compose run --rm --entrypoint python3 openclaw-cli - <<'PY2' > "$OUT_DIR/auth-profiles.json"
import json
from pathlib import Path

state_dir = Path("/home/node/.openclaw")
profiles = []
for path in state_dir.rglob("auth-profiles.json"):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(data, dict):
        for profile_id, credential in data.items():
            if not isinstance(credential, dict):
                continue
            profiles.append(
                {
                    "path": str(path),
                    "profile_id": str(profile_id),
                    "provider": credential.get("provider"),
                    "type": credential.get("type"),
                }
            )
manual = [item for item in profiles if item.get("type") in {"api_key", "token"}]
oauth = [item for item in profiles if item.get("type") == "oauth"]
print(json.dumps({"profiles": profiles, "manual": manual, "oauth": oauth}, indent=2))
if manual:
    raise SystemExit("manual credentials remain in auth profiles")
if not any(item.get("provider") == "openai-codex" for item in oauth):
    raise SystemExit("missing openai-codex OAuth profile")
PY2

echo "[validate] capturing agent inventory"
docker compose run --rm openclaw-cli agents list --json > "$OUT_DIR/agents.json"

echo "[validate] running smoke test"
bash scripts/run-smoke-test.sh | tee "$OUT_DIR/smoke-test.log"

echo "[validate] capturing persisted state"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/tasks" | python3 -m json.tool > "$OUT_DIR/tasks.json"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/worker-runs" | python3 -m json.tool > "$OUT_DIR/worker-runs.json"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/reviews" | python3 -m json.tool > "$OUT_DIR/reviews.json"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/approvals" | python3 -m json.tool > "$OUT_DIR/approvals.json"

echo "[validate] wrote artifacts to $OUT_DIR"
