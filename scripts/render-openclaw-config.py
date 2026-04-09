#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
CONTROLLED_TOP_LEVEL_KEYS = {"agents", "models", "mcp", "cronJobs"}
FORBIDDEN_PATHS: tuple[tuple[str, ...], ...] = (
    ("agents", "defaults", "model", "fallbacks"),
    ("models", "providers", "ollama", "apiKey"),
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key.strip(), value)


def read_json(path: Path, *, strict: bool) -> dict[str, Any]:
    if not path.exists():
        if strict:
            raise SystemExit(f"verify-file failed: missing file: {path}")
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        if strict:
            raise SystemExit(f"verify-file failed: unreadable file: {path}: {exc}") from exc
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise SystemExit(f"verify-file failed: invalid JSON in {path}: {exc}") from exc
        return {}
    if not isinstance(data, dict):
        if strict:
            raise SystemExit(f"verify-file failed: top-level JSON must be an object: {path}")
        return {}
    return data


def has_path(payload: dict[str, Any], path: tuple[str, ...]) -> bool:
    cursor: Any = payload
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return False
        cursor = cursor[key]
    return True


def forbidden_paths_present(payload: dict[str, Any]) -> list[str]:
    return [".".join(path) for path in FORBIDDEN_PATHS if has_path(payload, path)]


def assert_forbidden_paths_absent(payload: dict[str, Any]) -> None:
    found = forbidden_paths_present(payload)
    if found:
        raise SystemExit("forbidden config keys present after render: " + ", ".join(found))


def collect_passthrough(existing_configs: list[dict[str, Any]]) -> dict[str, Any]:
    passthrough: dict[str, Any] = {}
    for item in existing_configs:
        for key, value in item.items():
            if key not in CONTROLLED_TOP_LEVEL_KEYS:
                passthrough[key] = value
    return passthrough


def build_base_config(
    *,
    worker_model: str,
    ollama_model: str,
    ollama_base_url: str,
) -> dict[str, Any]:
    return {
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
    }


def build_config(repo_existing: dict[str, Any], runtime_existing: dict[str, Any]) -> dict[str, Any]:
    worker_model = os.getenv("OPENCLAW_WORKER_MODEL", "ollama/llama3.1:8b")
    ollama_model = os.getenv("OLLAMA_MODEL", worker_model.split("/", 1)[1] if "/" in worker_model else worker_model)
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

    config = collect_passthrough([runtime_existing, repo_existing])
    config.update(
        build_base_config(
            worker_model=worker_model,
            ollama_model=ollama_model,
            ollama_base_url=ollama_base_url,
        )
    )
    assert_forbidden_paths_absent(config)
    return config


def verify_files(paths: list[Path]) -> list[dict[str, Any]]:
    results = []
    for path in paths:
        payload = read_json(path, strict=True)
        forbidden = forbidden_paths_present(payload)
        if forbidden:
            raise SystemExit(f"verify-file failed: forbidden keys present in {path}: {', '.join(forbidden)}")
        results.append(
            {
                "path": str(path),
                "ok": True,
                "forbidden_paths_checked": [".".join(item) for item in FORBIDDEN_PATHS],
            }
        )
    return results


def run_self_test() -> dict[str, Any]:
    stale = {
        "agents": {"defaults": {"model": {"primary": "ollama/old-model", "fallbacks": ["openai-codex/gpt-5.4"]}}},
        "models": {"providers": {"ollama": {"baseUrl": "http://old-ollama:11434", "apiKey": "ollama-local"}}},
        "mcp": {"servers": {"archon": {"command": "python3", "args": ["/tmp/old.py"]}}},
        "cronJobs": [{"name": "old-job"}],
        "ui": {"theme": "dark"},
    }
    config = build_config(stale, stale)
    if config.get("ui") != {"theme": "dark"}:
        raise SystemExit("self-test failed: non-controlled top-level keys were not preserved")
    if config["agents"]["defaults"]["model"]["primary"] != os.getenv("OPENCLAW_WORKER_MODEL", "ollama/llama3.1:8b"):
        raise SystemExit("self-test failed: worker model was not rebuilt")
    if "cronJobs" in config:
        raise SystemExit("self-test failed: legacy cronJobs block was not removed")
    assert_forbidden_paths_absent(config)
    return {
        "ok": True,
        "preserved_top_level_keys": sorted(key for key in config if key not in CONTROLLED_TOP_LEVEL_KEYS),
        "removed_controlled_keys": ["cronJobs"],
        "forbidden_paths_checked": [".".join(item) for item in FORBIDDEN_PATHS],
    }


def render_config() -> dict[str, Any]:
    config_dir = Path(os.getenv("OPENCLAW_CONFIG_DIR", str(ROOT / ".data" / "openclaw-config")))
    repo_config_path = ROOT / ".openclaw" / "openclaw.json"
    runtime_config_path = config_dir / "openclaw.json"
    repo_existing = read_json(repo_config_path, strict=False)
    runtime_existing = read_json(runtime_config_path, strict=False)
    config = build_config(repo_existing, runtime_existing)

    repo_config_path.parent.mkdir(parents=True, exist_ok=True)
    repo_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    config_dir.mkdir(parents=True, exist_ok=True)
    runtime_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "rendered": [str(repo_config_path), str(runtime_config_path)],
        "removed_controlled_keys": ["cronJobs"],
        "forbidden_paths_checked": [".".join(item) for item in FORBIDDEN_PATHS],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render canonical OpenClaw harness config.")
    parser.add_argument("--self-test", action="store_true", help="Run a synthetic stale-config scrub test and exit.")
    parser.add_argument(
        "--verify-file",
        action="append",
        default=[],
        help="Verify that an existing config file is scrubbed of deprecated keys.",
    )
    args = parser.parse_args()

    load_env(ENV_PATH)

    if args.self_test:
        print(json.dumps(run_self_test(), indent=2))
        return 0

    if args.verify_file:
        paths = [Path(item) for item in args.verify_file]
        print(json.dumps({"ok": True, "results": verify_files(paths)}, indent=2))
        return 0

    print(json.dumps(render_config(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
