#!/usr/bin/env python3
"""Small predicates the engine and its validators both need.

Nothing here is interesting on its own. It exists because each of these is
called from both sides of a boundary the engine was split along -- the
validators ask whether a section is an empty-sentinel refusal, and so does the
code that assembles sections; the evidence catalog is read by both the query
builders and the provenance checks. Leaving them on either side would have
meant a circular import.
"""

from __future__ import annotations

from typing import Any

from question_prompts import NO_MCQS, NO_WRITTEN


def _unique_strings(values: list[str]) -> list[str]:
    """De-duplicate while keeping first-seen order."""
    return list(dict.fromkeys(value for value in values if value))


def is_empty_sentinel(answer: str, sentinel: str) -> bool:
    """True when an answer is the no-content sentinel, with or without a reason.

    The IMP prompts require a written reason on the line after the sentinel, so
    an exact string comparison would treat a justified refusal as a malformed
    section.
    """
    stripped = (answer or "").strip()
    return stripped == sentinel or stripped.startswith(f"{sentinel}\n")


def empty_sentinel_reason(answer: str, sentinel: str) -> str:
    if not is_empty_sentinel(answer, sentinel):
        return ""
    return (answer or "").strip()[len(sentinel) :].strip()


def _is_any_empty_sentinel(answer: str) -> bool:
    return any(
        is_empty_sentinel(answer, sentinel) for sentinel in (NO_MCQS, NO_WRITTEN)
    )


def _catalog_entry_is_available(entry: dict[str, Any]) -> bool:
    """Whether an evidence-catalog entry may be cited by a generated question."""
    return (
        entry.get("content_status") in {"available", "remote_only"}
        and entry.get("selected_for_run", True) is not False
    )


__all__ = [
    "_catalog_entry_is_available",
    "_is_any_empty_sentinel",
    "_unique_strings",
    "empty_sentinel_reason",
    "is_empty_sentinel",
]
