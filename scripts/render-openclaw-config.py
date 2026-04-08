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


def deep_merge(base: object, patch: object) -> object:
    if isinstance(base, dict) and isinstance(patch, dict):
        result = dict(base)
        for key, value in patch.items():
            if key in result:
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    return patch


def load_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


load_env(ENV_PATH)

worker_model = os.getenv("OPENCLAW_WORKER_MODEL", "ollama/qwen3-coder:latest")
review_model = os.getenv("OPENCLAW_REVIEW_MODEL", "openai-codex/gpt-5.4")
review_cron = os.getenv("REVIEW_CRON", "*/5 * * * *")
review_timezone = os.getenv("REVIEW_TIMEZONE", "UTC")
ollama_model = os.getenv("OLLAMA_MODEL", worker_model.split("/", 1)[1] if "/" in worker_model else worker_model)
ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
config_dir = Path(os.getenv("OPENCLAW_CONFIG_DIR", str(ROOT / ".data" / "openclaw-config")))
review_timeout = int(os.getenv("OPENCLAW_REVIEW_TIMEOUT_SECONDS", "60"))

base = {
    "agents": {
        "defaults": {
            "workspace": "/workspace",
            "model": {"primary": worker_model},
            "heartbeat": {"every": "5m", "target": "last", "lightContext": False},
        }
    },
    "models": {
        "providers": {
            "ollama": {
                "baseUrl": ollama_base_url,
                "api": "ollama",
                "models": [
                    {
                        "id": ollama_model,
                        "name": ollama_model,
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 32768,
                        "maxTokens": 32768,
                    }
                ],
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
    "cronJobs": [
        {
            "name": "codex-reviewer-every-5m",
            "schedule": {"kind": "cron", "expr": review_cron, "tz": review_timezone},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": "/skill codex-reviewer",
                "model": review_model,
                "timeoutSeconds": review_timeout,
            },
            "delivery": {"mode": "none"},
        }
    ],
}

repo_config_path = ROOT / ".openclaw" / "openclaw.json"
repo_existing = load_json_if_exists(repo_config_path)
runtime_existing = load_json_if_exists(config_dir / "openclaw.json")
existing = deep_merge(runtime_existing, repo_existing)
config = deep_merge(existing, base)

repo_config_path.parent.mkdir(parents=True, exist_ok=True)
repo_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
config_dir.mkdir(parents=True, exist_ok=True)
(config_dir / "openclaw.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

print(f"rendered {repo_config_path}")
print(f"rendered {config_dir / 'openclaw.json'}")
