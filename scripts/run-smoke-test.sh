#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARCHON_API_BASE_URL="${ARCHON_API_BASE_URL:-http://localhost:8080}"

echo "[smoke] checking service health"
curl -fsS "$ARCHON_API_BASE_URL/health" | python3 -m json.tool >/dev/null
curl -fsS "${OLLAMA_HTTP_BASE_URL:-http://localhost:11434}/api/tags" | python3 -m json.tool >/dev/null
docker compose exec -T openclaw-worker curl -fsS http://127.0.0.1:8091/health >/dev/null
docker compose exec -T openclaw-reviewer curl -fsS http://127.0.0.1:8092/health >/dev/null
docker compose run --rm openclaw-cli gateway health --url ws://127.0.0.1:18789 --json >/dev/null

create_json="$(curl -fsS -X POST "$ARCHON_API_BASE_URL/tasks" -H 'Content-Type: application/json' -d '{"title":"smoke-task","description":"validate end-to-end worker and reviewer flow","status":"queued","source":"smoke"}')"
task_id="$(python3 - <<'PY2' "$create_json"
import json
import sys
print(json.loads(sys.argv[1])["task_id"])
PY2
)"

echo "[smoke] created task $task_id"

curl -fsS -X POST "$ARCHON_API_BASE_URL/work/run" -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool
curl -fsS -X POST "$ARCHON_API_BASE_URL/reviews/run" -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool

python3 - <<'PY2' "$ARCHON_API_BASE_URL" "$task_id"
import json
import sys
import time
import urllib.request

base = sys.argv[1]
task_id = int(sys.argv[2])
for _ in range(30):
    with urllib.request.urlopen(base + '/worker-runs') as resp:
        worker_runs = json.loads(resp.read().decode())['items']
    with urllib.request.urlopen(base + '/reviews') as resp:
        reviews = json.loads(resp.read().decode())['items']
    with urllib.request.urlopen(base + '/tasks') as resp:
        tasks = json.loads(resp.read().decode())['items']
    worker_seen = any(item['task_id'] == task_id for item in worker_runs)
    review_seen = any(item['task_id'] == task_id for item in reviews)
    task = next((item for item in tasks if item['id'] == task_id), None)
    if worker_seen and review_seen and task and task['status'] in {'pending_human_approval', 'approved', 'needs_changes', 'rejected', 'failed'}:
        print(json.dumps({'task': task, 'worker_seen': worker_seen, 'review_seen': review_seen}, indent=2))
        sys.exit(0)
    time.sleep(2)
raise SystemExit('smoke test failed to observe persisted worker+review flow')
PY2

echo "[smoke] success"
