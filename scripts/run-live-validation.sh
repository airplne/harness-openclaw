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
docker compose exec -T openclaw-worker curl -fsS http://127.0.0.1:8091/health | python3 -m json.tool > "$OUT_DIR/worker-healt.json"
docker compose exec -T openclaw-reviewer curl -fsS http://127.0.0.1:8092/health | python3 -m json.tool > "$OUT_DIR/reviewer-health.json"
docker compose run --rm openclaw-cli gateway health --url ws://127.0.0.1:18789 --json > "$OUT_DIR/gateway-healt.json"

echo "[validate] verifying renderer scrub behavior"
python3 scripts/render-openclaw-config.py --self-test > "$OUT_DIR/render-self-test.json"
python3 scripts/render-openclaw-config.py --verify-file .openclaw/openclaw.json --verify-file .data/openclaw-config/openclaw.json > "$OUT_DIR/rendered-config-check.json"

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
provider = "openai-codex"
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

governed = [item for item in profiles if item.get("provider") == provider]
governed_manual = [item for item in governed if item.get("type") in {"api_key", "token"}]
governed_oauth = [item for item in governed if item.get("type") == "oauth"]
other_manual = [item for item in profiles if item.get("provider") != provider and item.get("type") in {"api_key", "token"}]

print(json.dumps({
    "profiles": profiles,
    "governed_provider": provider,
    "governed_manual": governed_manual,
    "governed_oauth": governed_oauth,
    "other_manual": other_manual,
}, indent=2))
if governed_manual:
    raise SystemExit("manual credentials remain for governed provider openai-codex")
if not governed_oauth:
    raise SystemExit("missing openai-codex OAuth profile")
PY2

echo "[validate] capturing agent inventory"
docker compose run --rm openclaw-cli agents list --json > "$OUT_DIR/agents.json"
python3 - <<'PY2' "$OUT_DIR/agents.json" "$OPENCLAW_WORKER_MODEL" "$OPENCLAW_REVIEW_MODEL" > "$OUT_DIR/agent-bindings.json"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
worker_model = sys.argv[2]
review_model = sys.argv[3]
items = payload.get("items") if isinstance(payload, dict) else payload
if not isinstance(items, list):
    raise SystemExit("unexpected agents list payload")

def find_agent(agent_id: str):
    for item in items:
        if item.get("id") == agent_id:
            return item
    return None

worker = find_agent("archon-worker")
reviewer = find_agent("codex-reviewer")
if worker is None or reviewer is None:
    raise SystemExit("expected worker/reviewer agents missing from inventory")
if worker.get("model") != worker_model:
    raise SystemExit(f"archon-worker model drift: {worker.get('model')} != {worker_model}")
if reviewer.get("model") != review_model:
    raise SystemExit(f"codex-reviewer model drift: {reviewer.get('model')} != {review_model}")
print(json.dumps({"worker": worker, "reviewer": reviewer}, indent=2))
PY2

echo "[validate] running smoke test"
bash scripts/run-smoke-test.sh | tee "$OUT_DIR/smoke-test.log"

echo "[validate] capturing persisted state"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/tasks" | python3 -m json.tool > "$OUT_DIR/tasks.json"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/worker-runs" | python3 -m json.tool > "$OUT_DIR/worker-runs.json"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/reviews" | python3 -m json.tool > "$OUT_DIR/reviews.json"
curl -fsS "http://localhost:${ARCHON_PORT:-8080}/approvals" | python3 -m json.tool > "$OUT_DIR/approvals.json"

echo "[validate] wrote artifacts to $OUT_DIR"
