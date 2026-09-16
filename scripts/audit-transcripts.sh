#!/usr/bin/env bash
# Audit every transcript in a module for thin extraction and duplicated prose.
#
# Answers "which transcripts should be re-run?" without opening any of them.
# Question coverage is measured against the whole Questions/ folder, which is
# an upper bound -- extraction is scoped to one lecture's topics -- so a LOW
# mark means "look at this", not "this is broken". `dup` counts ### headings
# that appear more than once, which is the signature of a transcript produced
# before the sliced-query merge learned to deduplicate narrative prose.
#
# Usage: scripts/audit-transcripts.sh [module_id]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module="${1:-toxo}"
transcripts="$root/modules/$module/Transcripts"
questions="$root/modules/$module/Questions"
coverage="$root/skills/universal-transcriber/scripts/question_coverage.py"

if [[ ! -d "$transcripts" ]]; then
  echo "No Transcripts directory for module '$module': $transcripts" >&2
  exit 1
fi

printf '%-32s %4s %4s %4s %4s %5s %6s %4s %s\n' \
  transcript secs mcq wr imp comb 'mcq%' dup ''
for file in "$transcripts"/*.md; do
  case "$file" in *Index.md|*.draft.md) continue;; esac
  report="$(python3 "$coverage" --questions-dir "$questions" --transcript "$file" --json)"
  REPORT="$report" FILE="$file" python3 - <<'PY'
import collections, json, os
report = json.loads(os.environ["REPORT"])
path = os.environ["FILE"]
text = open(path, encoding="utf-8").read()
headings = collections.Counter(
    line.rstrip() for line in text.splitlines() if line.strip().startswith("###")
)
print("%-32s %4d %4d %4d %4d %5d %5.0f%% %4d %s" % (
    os.path.basename(path),
    text.count("\n## "),
    text.count("\n### MCQ "),
    text.count("\n### Question "),
    text.count("**[IMP]**"),
    text.count("/ IMP]**"),
    report["mcq_coverage"] * 100,
    sum(1 for count in headings.values() if count > 1),
    "LOW" if report["below_floor"] else "",
))
PY
done
