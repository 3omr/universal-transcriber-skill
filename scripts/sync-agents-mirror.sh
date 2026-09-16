#!/usr/bin/env bash
# Regenerate .agents/skills/ from skills/.
#
# Google Antigravity and Codex load skills from .agents/skills/, so the repo
# carries a copy of skills/ there. This script is the only supported way to
# update it -- hand-editing one tree and forgetting the other is how the two
# drifted apart before.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

rm -rf .agents/skills
mkdir -p .agents
cp -R skills .agents/skills
find .agents/skills -name '__pycache__' -type d -prune -exec rm -rf {} +
find .agents/skills -name '*.py[cod]' -delete

echo "Regenerated .agents/skills from skills/"
