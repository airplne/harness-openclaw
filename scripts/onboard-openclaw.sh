#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "[onboard] missing .env; run 'bash scripts/bootstrap.sh' first" >&2
  exit 1
fi

set -a
source .env
set +a

verify_rendered_config() {
  python3 scripts/render-openclaw-config.py
  python3 scripts/render-openclaw-config.py \
    --verify-file .openclaw/openclaw.json \
    --verify-file .data/openclaw-config/openclaw.json
}

ensure_service_running() {
  local service="$1"
  if ! docker compose ps --services --filter status=running | grep -Fx "$service" >/dev/null; then
    echo "[onboard] required service '$service' is not running; start the stack with 'docker compose up -d --build'" >&2
    exit 1
  fi
}

ensure_agent_bindings() {
  local agents_json
  agents_json="$(docker compose run --rm openclaw-cli agents list --json)"
  python3 - <<'PY2' "$agents_json" "$OPENCLAW_WORKER_MODEL" "$OPENCLAW_REVIEW_MODEL"
import json
import sys

payload = json.loads(sys.argv[1])
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
missing = []
if worker is None:
    missing.append("archon-worker")
if reviewer is None:
    missing.append("codex-reviewer")

print(json.dumps({"worker": worker, "reviewer": reviewer, "missing": missing}, indent=2))
PY2
}

seed_missing_agents() {
  local agent_snapshot
  agent_snapshot="$(ensure_agent_bindings)"

  while IFS= read -r agent_id; do
    [[ -n "$agent_id" ]] || continue
    case "$agent_id" in
      archon-worker)
        echo "[onboard] creating archon-worker agent"
        docker compose run --rm openclaw-cli agents add archon-worker --workspace /workspace --model "$OPENCLAW_WORKER_MODEL" --non-interactive --json
        ;;
      codex-reviewer)
        echo "[onboard] creating codex-reviewer agent"
        docker compose run --rm openclaw-cli agents add codex-reviewer --workspace /workspace --model "$OPENCLAW_REVIEW_MODEL" --non-interactive --json
        ;;
      *)
        echo "[onboard] unexpected missing agent '$agent_id'" >&2
        exit 1
        ;;
    esac
  done < <(
    python3 - <<'PY2' "$agent_snapshot"
import json
import sys

payload = json.loads(sys.argv[1])
for agent in payload.get("missing", []):
    print(agent)
PY2
  )

  local final_agents_json
  final_agents_json="$(docker compose run --rm openclaw-cli agents list --json)"
  python3 - <<'PY2' "$final_agents_json" "$OPENCLAW_WORKER_MODEL" "$OPENCLAW_REVIEW_MODEL"
import json
import sys

payload = json.loads(sys.argv[1])
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
if worker is None:
    raise SystemExit("archon-worker agent missing after onboarding")
if reviewer is None:
    raise SystemExit("codex-reviewer agent missing after onboarding")
if worker.get("model") != worker_model:
    raise SystemExit(f"archon-worker model drift: {worker.get('model')} != {worker_model}")
if reviewer.get("model") != review_model:
    raise SystemExit(f"codex-reviewer model drift: {reviewer.get('model')} != {review_model}")
print(json.dumps({"worker": worker, "reviewer": reviewer}, indent=2))
PY2
}

verify_governed_auth_policy() {
  docker compose run --rm --entrypoint python3 openclaw-cli - <<'PY2'
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

if governed_manual:
    raise SystemExit("manual credentials remain for governed provider openai-codex: " + json.dumps(governed_manual))
if not governed_oauth:
    raise SystemExit("missing openai-codex OAuth auth profile after onboarding")

print(json.dumps({
    "governed_provider": provider,
    "governed_oauth_profiles": governed_oauth,
    "other_manual_profiles": other_manual,
}, indent=2))
PY2
}

echo "[onboard] verifying local prerequisites"
ensure_service_running ollama
ensure_service_running archon
ensure_service_running openclaw-gateway

echo "[onboard] rendering canonical OpenClaw config"
verify_rendered_config

echo "[onboard] running OpenClaw onboarding for Codex OAuth"
docker compose run --rm openclaw-cli onboard --mode local --auth-choice openai-codex --accept-risk

echo "[onboard] configuring Ollama through OpenClaw"
docker compose run --rm openclaw-cli onboard --non-interactive \
  --auth-choice ollama \
  --custom-base-url "${OLLAMA_BASE_URL}" \
  --custom-model-id "${OLLAMA_MODEL}" \
  --accept-risk

echo "[onboard] pulling local Ollama model"
docker compose exec -T ollama ollama pull "${OLLAMA_MODEL}"

echo "[onboard] re-rendering config after onboarding"
verify_rendered_config

echo "[onboard] seeding and verifying dedicated agents"
seed_missing_agents

echo "[onboard] verifying governed reviewer auth policy"
verify_governed_auth_policy

echo "[onboard] complete"
