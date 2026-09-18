#!/usr/bin/env python3
"""Checking that a badge's claim is true of the paper it names.

A badge is a promise to the student: `**[Past Exams - 2022]**` says *this exact
question was on the 2022 paper*, and a student who trusts it revises by it. The
phase validators check that the cited file exists and that the year is one the
evidence catalog knows -- not that the question is in it. So a block can claim a
year, name a real file, and pass, while the question was never on that paper.

That gap is not theoretical. A transcript written by hand from a verbatim
recording carried six clinical cases badged `[Past Exams - 2022, 2023]` whose
scenarios existed in no paper at all; the year badges passed validation because
the files were real and the years were known.

This module closes it by asking the only question that settles the matter: does
the question's own wording appear in the paper it cites, and under which
heading? Two things make that harder than a substring search:

1. **Scanned papers are OCR-damaged.** `Tomosynthesis` arrives as `Tomosynthes'`
   and a whole stem can read `The Most Ré 7~5 ~ 2026 Q) igital ime a`. An exact
   match would reject questions that are genuinely there, which is the worse
   error -- it teaches the writer to drop honest badges.
2. **A compiled bank is not one paper.** `Radiology_Exams_2026.txt` holds
   `--- End 2022 ---`, `--- Revision 2025 ---` and unlabelled `--- Page 12 ---`
   sections side by side. The year a question can claim comes from the *section*
   it sits in, never from the filename -- a bank compiled in 2026 does not make
   its contents 2026 questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from exam_years import extract_exam_years, extract_filename_exam_years

SECTION_PATTERN = re.compile(r"^---\s*(.+?)\s*---$", re.MULTILINE)
# The primary test is a run of consecutive words, not a similarity score. A
# ratio rewards two texts for sharing "of the in a" and will happily place a
# question in a paper that never carried it; an unbroken run of real words is
# what actually distinguishes "this question is here" from "these are both
# English medical sentences".
MIN_RUN = 5
# A shorter run still counts when the rest of the stem agrees too -- this is
# what carries a question through OCR damage without inviting coincidence.
SHORT_RUN = 3
SHORT_RUN_RATIO = 0.70
# Between "clearly present" and "clearly absent" sits the scanned page the OCR
# shredded. Those are reported for a human to read, never silently dropped: a
# badge wrongly removed is as dishonest as one wrongly kept.
REVIEW_RUN = 2
REVIEW_RATIO = 0.35
MIN_STEM_TOKENS = 4


def normalize(text: str) -> list[str]:
    """Letters and digits only, lowercased. OCR noise is mostly punctuation."""
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


@dataclass(frozen=True)
class Section:
    """One `--- Label ---` run of a compiled question file."""

    label: str
    start: int
    end: int

    @property
    def years(self) -> tuple[int, ...]:
        """Years the heading itself claims. `Page 12` claims none."""
        return extract_exam_years(self.label)


@dataclass(frozen=True)
class Location:
    """Where a question was found, and how well."""

    source: str
    section: str
    years: tuple[int, ...]
    ratio: float
    run: int = 0

    @property
    def found(self) -> bool:
        """Present beyond reasonable doubt."""
        return self.run >= MIN_RUN or (
            self.run >= SHORT_RUN and self.ratio >= SHORT_RUN_RATIO
        )

    @property
    def needs_review(self) -> bool:
        """Probably present, but the scan is too damaged to say so mechanically."""
        return (
            not self.found
            and self.run >= REVIEW_RUN
            and self.ratio >= REVIEW_RATIO
        )


def split_sections(text: str) -> list[Section]:
    """The file's `--- Label ---` runs, in order.

    A file with no headings is one anonymous section, so a plain exam paper is
    handled by the same code as a compiled bank.
    """
    marks = list(SECTION_PATTERN.finditer(text))
    if not marks:
        return [Section("", 0, len(text))]
    sections = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        sections.append(Section(mark.group(1), mark.end(), end))
    return sections


def is_compiled_bank(sections: list[Section]) -> bool:
    """True when the file collects several papers rather than being one.

    The test is whether any heading names a year, not whether headings exist:
    an OCR pass marks a single scanned paper `--- Page 1 ---`, `--- Page 2 ---`
    and those are pagination, not provenance. `Radiology_Exams_2026.txt` says
    `--- End 2022 ---` and `--- Revision 2025 ---`, and that is a bank.
    """
    return any(section.years for section in sections)


def _section_years(
    section: Section, source_name: str, compiled: bool
) -> tuple[int, ...]:
    """The years a question in this section may claim.

    A compiled bank carries its provenance in its headings, so the heading wins
    and an unlabelled `Page 12` yields nothing -- `Radiology_Exams_2026.txt`
    being compiled in 2026 does not make a question inside it a 2026 question.
    A single exam paper has no such headings, and there its filename is the only
    statement of which paper it is.
    """
    if compiled:
        return section.years
    return extract_filename_exam_years(source_name)


def locate(
    stem: str, source_name: str, source_text: str, context: str = ""
) -> Location:
    """Best place in one source where this question's wording appears.

    Matched on the longest unbroken run of the question's own words, with a
    similarity ratio as the tie-breaker, so a question is placed where its
    wording actually is rather than wherever the prose happens to rhyme.
    """
    stem_tokens = normalize(stem)
    if len(stem_tokens) < MIN_STEM_TOKENS:
        # An image question's stem is "The arrows indicate" and carries no
        # signal at all. Its options do, so they stand in for it -- otherwise
        # every picture question in the paper reads as absent from it.
        stem_tokens = normalize(f"{stem} {context}")
        if len(stem_tokens) < MIN_STEM_TOKENS:
            return Location(source_name, "", (), 0.0, 0)
    sections = split_sections(source_text)
    compiled = is_compiled_bank(sections)
    best = Location(source_name, "", (), 0.0, 0)
    for section in sections:
        body = normalize(source_text[section.start : section.end])
        if not body:
            continue
        matcher = SequenceMatcher(None, stem_tokens, body, autojunk=False)
        match = matcher.find_longest_match(0, len(stem_tokens), 0, len(body))
        if not match.size:
            continue
        window = body[
            max(0, match.b - match.a) : max(0, match.b - match.a) + len(stem_tokens)
        ]
        ratio = SequenceMatcher(None, stem_tokens, window, autojunk=False).ratio()
        if (match.size, ratio) > (best.run, best.ratio):
            best = Location(
                source_name,
                section.label,
                _section_years(section, source_name, compiled),
                ratio,
                match.size,
            )
    return best


def index_years(stem: str, index: dict | None) -> tuple[int, ...] | None:
    """Years the exam index records for this question, if it knows it.

    The index wins over raw text, because part of it is what a human read off a
    page the OCR had destroyed. Re-deriving those from the wreckage would throw
    away the one judgement in the whole pipeline that was made with the paper
    in hand.
    """
    if not index:
        return None
    wanted = " ".join(normalize(stem)[:12])
    if not wanted:
        return None
    for question in index.get("questions", {}).values():
        if " ".join(normalize(question.get("stem", ""))[:12]) == wanted:
            return tuple(question.get("years", ()))
    return None


def audit(
    stem: str, sources: dict[str, str], context: str = ""
) -> list[Location]:
    """Where this question sits in every source, best match per source first."""
    hits = [locate(stem, name, text, context) for name, text in sources.items()]
    return sorted(
        (hit for hit in hits if hit.found or hit.needs_review),
        key=lambda hit: (-hit.run, -hit.ratio),
    )


def supported_years(
    stem: str,
    sources: dict[str, str],
    context: str = "",
    index: dict | None = None,
) -> tuple[int, ...]:
    """Every year a badge for this question may honestly claim."""
    from_index = index_years(stem, index)
    if from_index is not None:
        return tuple(sorted(from_index))
    years: set[int] = set()
    for hit in audit(stem, sources, context):
        if hit.found:
            years.update(hit.years)
    return tuple(sorted(years))
