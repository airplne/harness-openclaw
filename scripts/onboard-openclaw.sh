#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

set -a
source .env
set +a

python3 scripts/render-openclaw-config.py
python3 scripts/render-openclaw-config.py --verify-file .openclaw/openclaw.json --verify-file .data/openclaw-config/openclaw.json

echo "[onboard] running OpenClaw onboarding for Codex OAuth"
docker compose run --rm openclaw-cli onboard --mode local --auth-choice openai-codex --accept-risk

echo "[onboard] configuring Ollama through OpenClaw"
docker compose run --rm openclaw-cli onboard --non-interactive \
  --auth-choice ollama \
  --custom-base-url "${OLLAMA_BASE_URL}" \
  --custom-model-id "${OLLAMA_MODEL}" \
  --accept-risk

echo "[onboard] pulling local Ollama model"
docker compose exec ollama ollama pull "${OLLAMA_MODEL}"

echo "[onboard] rendering config after onboarding"
python3 scripts/render-openclaw-config.py
python3 scripts/render-openclaw-config.py --verify-file .openclaw/openclaw.json --verify-file .data/openclaw-config/openclaw.json

echo "[onboard] verifying dedicated agents"
docker compose run --rm openclaw-cli agents add archon-worker --workspace /workspace --model "${OPENCLAW_WORKER_MODEL}" --non-interactive --json
docker compose run --rm openclaw-cli agents add codex-reviewer --workspace /workspace --model "${OPENCLAW_REVIEW_MODEL}" --non-interactive --json

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

echo "[onboard] verifying governed auth profile types"
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

echo "[onboard] complete"
