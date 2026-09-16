#!/usr/bin/env bash
# Bump VERSION on main, tag it, and publish the GitHub release.
#
# The in-app update notifier reads the latest GitHub *release* tag, so a merge
# that does not reach this point is invisible to everyone who installed the
# skill. Run only from the Release workflow, after a merge.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

: "${NEXT_VERSION:?NEXT_VERSION is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
tag="v${NEXT_VERSION}"

if gh release view "$tag" >/dev/null 2>&1; then
  echo "Release $tag already exists; nothing to do." >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
  exit 0
fi

current="$(tr -d '[:space:]' < VERSION)"
if [[ "$current" == "$NEXT_VERSION" ]]; then
  # The pull request bumped VERSION itself. Respect it and only tag.
  echo "VERSION already reads $NEXT_VERSION; tagging without a bump commit."
else
  bash scripts/bump-version.sh "$NEXT_VERSION"
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git add VERSION skills .agents
  if git diff --cached --quiet; then
    echo "bump-version.sh changed nothing to commit; tagging as-is."
  else
    git commit -m "chore: release ${NEXT_VERSION}

Released by the Release workflow for #${PR_NUMBER}.
${BUMP_REASON:-}"
    git push origin HEAD:main
  fi
fi

notes_file="$(mktemp)"
{
  printf '%s\n\n' "${PR_BODY:-}"
  printf -- '---\n\n'
  printf 'Released automatically on merge of #%s (%s).\n' "$PR_NUMBER" "${BUMP_REASON:-}"
  printf '\n```bash\nnpx skills update 3omr/universal-transcriber-skill\n```\n'
} > "$notes_file"

gh release create "$tag" \
  --target "$(git rev-parse HEAD)" \
  --latest \
  --title "${tag}: ${PR_TITLE:-Release ${NEXT_VERSION}}" \
  --notes-file "$notes_file"

echo "Published $tag from #${PR_NUMBER}." >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
