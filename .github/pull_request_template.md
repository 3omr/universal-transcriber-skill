<!--
  The PR TITLE decides the next version. Write it as a conventional commit:

    feat: ...      -> minor release   (1.4.0 -> 1.5.0)
    fix: ...       -> patch release   (1.4.0 -> 1.4.1)
    perf|refactor|revert: ...         -> patch release
    feat!: ...     -> major release   (1.4.0 -> 2.0.0)
    chore|docs|ci|test|build|style    -> no release

  To override, add a release:major / release:minor / release:patch /
  release:skip label. A label always beats the title.

  A bot comments the planned version on this PR. Check it before merging --
  users only see an update when a GitHub release is published.
-->

## What changed

<!-- What this does, and why it is worth doing. -->

## How it was verified

<!--
  Measured results beat descriptions. If a transcription ran, say which
  lecture and what came out. If only the suite ran, say so.
-->

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `bash scripts/sync-agents-mirror.sh` run if anything under `skills/` changed

## Anything left out

<!-- Scope you deliberately did not cover, and why. Write "nothing" if none. -->
