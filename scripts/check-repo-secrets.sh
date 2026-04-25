#!/usr/bin/env bash
# Fail CI if obvious secret patterns appear in tracked content (lightweight guardrail).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "[check-repo-secrets] git not found; skip"
  exit 0
fi

patterns=(
  'gateway\.auth\.token'
  '"token"[[:space:]]*:[[:space:]]*"[a-f0-9]{32,}"'
  'ghp_[A-Za-z0-9]{20,}'
  'xox[baprs]-[A-Za-z0-9-]+'
)

failed=0
for pat in "${patterns[@]}"; do
  if git grep -nE "$pat" -- ':!*.md' ':!scripts/check-repo-secrets.sh' 2>/dev/null; then
    echo "[check-repo-secrets] matched forbidden pattern: $pat" >&2
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  echo "[check-repo-secrets] FAILED" >&2
  exit 1
fi
echo "[check-repo-secrets] OK"
exit 0
