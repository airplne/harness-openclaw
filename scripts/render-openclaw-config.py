#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

load_env(ENV_PATH)
worker_model = os.getenv("OPENCLAW_WORKER_MODEL", "ollama/qwen3-coder:latest")
review_model = os.getenv("OPENCLAW_REVIEW_MODEL", "openai-codex/gpt-5.4")
review_cron = os.getenv("REVIEW_CRON", "*/5 * * * *")
review_timezone = os.getenv("REVIEW_TIMEZONE", "UTC")
ollama_model = os.getenv("OLLAMA_MODEL", worker_model.split("/", 1)[1] if "/" in worker_model else worker_model)
ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
ollama_api_key = os.getenv("OLLAMA_API_KEY", "ollama-local")
config_dir = Path(os.getenv("OPENCLAW_CONFIG_DIR", str(ROOT / ".data" / "openclaw-config")))
workspace_dir = os.getenv("OPENCLAW_WORKSPACE_DIR", str(ROOT))
review_timeout = int(os.getenv("OPENCLAW_REVIEW_TIMEOUT_SECONDS", "60"))

def make_config() -> dict:
    return {
        "agents": {
            "defaults": {
                "workspace": "/workspace",
                "model": {"primary": worker_model, "fallbacks": [review_model]},
                "heartbeat": {"every": "5m", "target": "last", "lightContext": False},
            }
        },
        "models": {
            "providers": {
                "ollama": {
                    "baseUrl": ollama_base_url,
                    "apiKey": ollama_api_key,
                    "api": "ollama",
                    "models": [{
                        "id": ollama_model,
                        "name": ollama_model,
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 32768,
                        "maxTokens": 32768,
                    }],
                }
            }
        },
        "mcp": {
            "servers": {
                "archon": {
                    "command": "python3",
                    "args": ["/workspace/services/archon-mcp/server.py"],
                    "env": {"ARCHON_API_BASE_URL": "http://archon:8080"},
                }
            }
        },
        "cronJobs": [{
            "name": "codex-reviewer-every-5m",
            "schedule": {"kind": "cron", "expr": review_cron, "tz": review_timezone},
            "sessionTarget": "isolated",
            "payload": {"kind": "agentTurn", "message": "/skill codex-reviewer", "model": review_model, "timeoutSeconds": review_timeout},
            "delivery": {"mode": "none"},
        }],
    }

config = make_config()
repo_config_path = ROOT / ".openclaw" / "openclaw.json"
repo_config_path.parent.mkdir(parents=True, exist_ok=True)
repo_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
config_dir.mkdir(parents=True, exist_ok=True)
(config_dir / "openclaw.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
(config_dir / ".env").write_text(
    f"OPENCLAW_GATEWAY_TOKEN={os.getenv('OPENCLAW_GATEWAY_TOKEN', 'change-me')}\n"
    f"OLLAMA_API_KEY={ollama_api_key}\n",
    encoding="utf-8",
)
print(f"rendered {repo_config_path}")
print(f"rendered {config_dir / 'openclaw.json'}")
print(f"workspace_dir={workspace_dir}")
