#!/usr/bin/env bash
# Fail if .agents/skills/ has drifted from skills/.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if diff -r \
    --exclude='__pycache__' --exclude='*.pyc' \
    skills .agents/skills; then
  echo "OK: .agents/skills matches skills/"
  exit 0
fi

cat >&2 <<'MSG'

ERROR: .agents/skills/ has drifted from skills/.

Edit skills/ only, then regenerate the mirror:

    bash scripts/sync-agents-mirror.sh

MSG
exit 1
