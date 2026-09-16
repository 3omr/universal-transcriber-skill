#!/usr/bin/env bash
# Emit the release decision as GitHub Actions `key=value` output lines.
#
# Reads PR_TITLE, PR_BODY and PR_LABELS (a JSON array of label names) from the
# environment so nothing untrusted is interpolated into a shell command.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
current="$(tr -d '[:space:]' < "$root/VERSION")"

CURRENT="$current" python3 - "$root/scripts/next_version.py" <<'PY'
import json, os, runpy, sys

module = runpy.run_path(sys.argv[1])
labels = json.loads(os.environ.get("PR_LABELS") or "[]")
decision = module["decide"](
    os.environ["CURRENT"],
    labels if isinstance(labels, list) else [],
    os.environ.get("PR_TITLE", ""),
    os.environ.get("PR_BODY", ""),
)
# Reasons are a single line by construction, so plain key=value is safe.
print(f"current={os.environ['CURRENT']}")
print(f"bump={decision.bump}")
print(f"version={decision.version}")
print(f"reason={decision.reason}")
print(f"releases={'true' if decision.releases else 'false'}")
PY
