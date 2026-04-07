#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

primary = os.getenv("OPENCLAW_PRIMARY_MODEL", "ollama/qwen3-coder:latest")
review = os.getenv("OPENCLAW_CODEX_REVIEW_MODEL", "openai-codex/gpt-5.4")
cron = os.getenv("REVIEW_CRON", "*/5 * * * *")
tz = os.getenv("REVIEW_TIMEZONE", "UTC")
timeout = int(os.getenv("OPENCLAW_REVIEW_TIMEOUT_SECONDS", "300"))

config = {
    "agents": {
        "defaults": {
            "model": {
                "primary": primary,
                "fallbacks": [review],
            },
            "models": {
                primary: {"alias": "local-heavy"},
                review: {"alias": "codex-reviewer"},
            },
            "heartbeat": {
                "every": "5m",
                "target": "last",
                "lightContext": False,
            },
        }
    },
    "cronJobs": [
        {
            "name": "codex-reviewer-every-5m",
            "schedule": {"kind": "cron", "expr": cron, "tz": tz},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": "/skill codex-reviewer",
                "model": review,
                "timeoutSeconds": timeout,
            },
            "delivery": {"mode": "none"},
        }
    ],
    "mcpServers": {
        "archon": {
            "transport": "http",
            "url": os.getenv("ARCHON_API_BASE_URL", "http://archon:8080") + "/mcp",
        }
    },
}

path = Path(os.getenv("OPENCLAW_CONFIG_PATH", "/workspace/.openclaw/openclaw.json"))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
print(f"[render-openclaw-config] wrote {path}")
