#!/usr/bin/env bash
# Fail if a file that is deliberately duplicated across skills has drifted.
#
# Each skill is its own directory so it can be linked into ~/.claude/skills/
# on its own. That rules out importing a shared helper across skill
# boundaries, so a handful of files are copied instead. The copying is
# intended; the drifting is not. This is the guard.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# Each entry is a space-separated group of paths that must be byte-identical.
SHARED_GROUPS=(
  "skills/universal-transcriber/scripts/version_checker.py skills/transcriber-anki/scripts/version_checker.py"
  "skills/universal-transcriber/scripts/console.py skills/transcriber-anki/scripts/console.py"
)

status=0
for group in "${SHARED_GROUPS[@]}"; do
  read -r -a paths <<< "$group"
  canonical="${paths[0]}"
  for path in "${paths[@]:1}"; do
    if ! diff -u "$canonical" "$path"; then
      cat >&2 <<MSG

ERROR: $path has drifted from $canonical.

These files are duplicated on purpose -- each skill must stand alone when it
is linked into ~/.claude/skills/ -- but they must stay identical. Copy the
canonical file over the other:

    cp $canonical $path

MSG
      status=1
    fi
  done
done

if [ "$status" -eq 0 ]; then
  echo "OK: all deliberately duplicated files are identical"
fi
exit "$status"
