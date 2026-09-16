#!/usr/bin/env python3
"""Decide the next release version from a merged pull request.

The version is not something anyone should have to remember to bump. This
reads the PR the way a reader does -- its labels first, then its title -- and
says what the next version is and why.

Precedence, highest first:

  1. a `release:major` / `release:minor` / `release:patch` / `release:skip`
     label, which is the explicit override
  2. `!` after the conventional-commit type, or `BREAKING CHANGE:` in the body
     -> major
  3. the conventional-commit type in the title:
       feat                                  -> minor
       fix, perf, revert                     -> patch
       refactor                              -> patch
       docs, test, chore, ci, build, style   -> skip
  4. anything unrecognised -> patch, because a change that shipped is safer
     described as a release than as nothing

Before 1.0.0 a "major" bump is reported as minor: 0.x has no stable API to
break, and jumping to 1.0.0 is a decision a person makes, not a script.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

BUMPS = ("major", "minor", "patch", "skip")

LABEL_PREFIX = "release:"

TYPE_BUMPS = {
    "feat": "minor",
    "fix": "patch",
    "perf": "patch",
    "revert": "patch",
    "refactor": "patch",
    "docs": "skip",
    "test": "skip",
    "tests": "skip",
    "chore": "skip",
    "ci": "skip",
    "build": "skip",
    "style": "skip",
}

# "feat(engine)!: drop the legacy manifest" -> type "feat", breaking marker "!"
TITLE_PATTERN = re.compile(r"^\s*(?P<type>[a-zA-Z]+)(?:\([^)]*\))?(?P<breaking>!?)\s*:")
BREAKING_BODY = re.compile(r"(?mi)^\s*BREAKING[ -]CHANGE\s*:")


@dataclass(frozen=True)
class Decision:
    bump: str
    version: str
    reason: str

    @property
    def releases(self) -> bool:
        return self.bump != "skip"

    def as_dict(self) -> dict[str, object]:
        return {
            "bump": self.bump,
            "version": self.version,
            "reason": self.reason,
            "releases": self.releases,
        }


def parse_version(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", version or "")
    if not match:
        raise ValueError(f"'{version}' is not a MAJOR.MINOR.PATCH version")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def apply_bump(current: str, bump: str) -> str:
    major, minor, patch = parse_version(current)
    if bump == "skip":
        return f"{major}.{minor}.{patch}"
    if bump == "major":
        # 0.x has no stable API to break; reaching 1.0.0 is a human decision.
        if major == 0:
            return f"0.{minor + 1}.0"
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def bump_from_labels(labels: list[str]) -> str | None:
    """An explicit release: label always wins, strongest one first."""
    wanted = {
        label.strip().casefold()[len(LABEL_PREFIX):]
        for label in labels
        if label.strip().casefold().startswith(LABEL_PREFIX)
    }
    for bump in BUMPS:
        if bump in wanted:
            return bump
    return None


def bump_from_title(title: str, body: str = "") -> tuple[str, str]:
    match = TITLE_PATTERN.match(title or "")
    if not match:
        if BREAKING_BODY.search(body or ""):
            return "major", "the body declares BREAKING CHANGE"
        return (
            "patch",
            "the title carries no conventional-commit type, so this is "
            "treated as a patch",
        )
    commit_type = match.group("type").casefold()
    if match.group("breaking"):
        return "major", f"'{commit_type}!' marks a breaking change"
    if BREAKING_BODY.search(body or ""):
        return "major", "the body declares BREAKING CHANGE"
    bump = TYPE_BUMPS.get(commit_type)
    if bump is None:
        return "patch", f"'{commit_type}' is not a known type, so this is a patch"
    if bump == "skip":
        return "skip", f"'{commit_type}' changes nothing users install"
    return bump, f"'{commit_type}' means a {bump} release"


def decide(
    current: str, labels: list[str] | None = None, title: str = "", body: str = ""
) -> Decision:
    labelled = bump_from_labels(labels or [])
    if labelled:
        reason = f"the release:{labelled} label was applied"
        return Decision(labelled, apply_bump(current, labelled), reason)
    bump, reason = bump_from_title(title, body)
    return Decision(bump, apply_bump(current, bump), reason)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True, help="The version in VERSION")
    parser.add_argument("--title", default="", help="The pull request title")
    parser.add_argument("--body", default="", help="The pull request body")
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="labels",
        help="A pull request label; repeat for several",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    try:
        decision = decide(args.current, args.labels, args.title, args.body)
    except ValueError as error:
        print(f"[Error] {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(decision.as_dict(), ensure_ascii=False))
    else:
        print(f"bump={decision.bump}")
        print(f"version={decision.version}")
        print(f"reason={decision.reason}")
        print(f"releases={'true' if decision.releases else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
