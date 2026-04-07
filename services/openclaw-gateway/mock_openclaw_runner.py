#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--message", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--config", required=True)
args = parser.parse_args()

result = {
    "status": "pending_human_approval",
    "summary": "Mock reviewer executed. Replace OPENCLAW_SKILL_RUN_CMD with the real OpenClaw CLI invocation.",
    "findings": [
        f"message={args.message}",
        f"model={args.model}",
        f"config={args.config}",
    ],
    "follow_up": [
        "Set OPENCLAW_SKILL_RUN_CMD to your real OpenClaw command.",
        "Re-run the smoke test to verify Codex review routing.",
    ],
    "requires_human_approval": True,
    "ran_at": datetime.now(timezone.utc).isoformat(),
}
print(json.dumps(result))
print("\nMock OpenClaw runner completed.")
