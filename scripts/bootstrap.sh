#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OPENCLAW_CONFIG_DIR="${OPENCLAW_CONFIG_DIR:-./.data/openclaw-config}"

mkdir -p .data/openclaw-config .data/archon .data/ollama .data/validation

ARCHON_AUTH_CONFIG_PATH="${ARCHON_AUTH_CONFIG_PATH:-$OPENCLAW_CONFIG_DIR/archon-auth.json}"
python3 - <<'PY' "$OPENCLAW_CONFIG_DIR" "$ARCHON_AUTH_CONFIG_PATH"
import hashlib
import json
import secrets
import stat
import sys
from pathlib import Path

config_dir = Path(sys.argv[1])
config_path = Path(sys.argv[2])
config_dir.mkdir(parents=True, exist_ok=True)

identities = {
    "operator": ["tasks:create", "tasks:read", "tasks:patch", "claims:read", "worker-runs:read", "reviews:read", "approvals:read", "approvals:create", "work:run", "reviews:run", "audit:read"],
    "worker": ["tasks:read", "tasks:claim:worker", "tasks:transition:working", "worker-runs:create", "worker-runs:read"],
    "reviewer": ["tasks:read", "tasks:claim:review", "tasks:transition:reviewing", "reviews:create", "reviews:read"],
    "mcp": [
        "tasks:read",
        "tasks:create",
        "tasks:transition:mcp",
        "reviews:create:mcp",
        "approvals:create:mcp",
        "mcp:archon_create_task",
        "mcp:archon_list_tasks",
        "mcp:archon_transition_task",
        "mcp:archon_record_review",
        "mcp:archon_request_approval",
    ],
    "readonly": ["tasks:read", "claims:read", "worker-runs:read", "reviews:read", "approvals:read", "audit:read"],
    "archon": ["runner:invoke"],
}

token_files = {}
for identity in identities:
    path = config_dir / f"{identity}.token"
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    token_files[identity] = path.read_text(encoding="utf-8").strip()

credentials = []
for identity, scopes in identities.items():
    token = token_files[identity]
    credentials.append(
        {
            "key_id": f"{identity}-v1",
            "identity": identity,
            "state": "active",
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "scopes": scopes,
        }
    )

config = {"version": 1, "credentials": credentials}
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
print(json.dumps({"ok": True, "config": str(config_path), "identities": sorted(identities)}))
PY

python3 scripts/render-openclaw-config.py
python3 scripts/render-openclaw-config.py \
  --verify-file .openclaw/openclaw.json \
  --verify-file .data/openclaw-config/openclaw.json

echo "[bootstrap] rendered OpenClaw config and local runtime directories"
echo "[bootstrap] next steps:"
echo "  docker compose up -d --build"
echo "  bash scripts/onboard-openclaw.sh"
