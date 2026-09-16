#!/usr/bin/env bash
# Set the release version everywhere it appears.
#
# The repository VERSION file is the source of truth. Each skill's
# version_checker.py also carries a FALLBACK_VERSION for standalone installs
# that have no VERSION file above them; this script keeps the two in step, and
# tests/test_version_checker.py fails if they ever drift.
#
# Usage: scripts/bump-version.sh 1.4.0
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $(basename "$0") <version>   e.g. $(basename "$0") 1.4.0" >&2
  exit 2
fi

version="$1"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: '$version' is not a MAJOR.MINOR.PATCH version" >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

echo "$version" > VERSION

for checker in skills/*/scripts/version_checker.py; do
  python3 - "$checker" "$version" <<'PY'
import pathlib, re, sys

path, version = pathlib.Path(sys.argv[1]), sys.argv[2]
source = path.read_text(encoding="utf-8")
updated, count = re.subn(
    r'^FALLBACK_VERSION = "[^"]*"$',
    f'FALLBACK_VERSION = "{version}"',
    source,
    count=1,
    flags=re.MULTILINE,
)
if count != 1:
    raise SystemExit(f"ERROR: no FALLBACK_VERSION assignment in {path}")
path.write_text(updated, encoding="utf-8")
PY
  echo "  updated $checker"
done

bash "$root/scripts/sync-agents-mirror.sh"
echo "Version set to $version"
