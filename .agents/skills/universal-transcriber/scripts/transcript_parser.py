#!/usr/bin/env python3
"""One reader for the 5-section transcript format.

Two parsers grew independently against the same documents. question_coverage.py
counted `### MCQ N` headings with a regex and knew nothing else; the anki
skill's transcript_concept_extractor.py pulled stems, options, answers, badges
and model answers out with a dozen ad-hoc regexes scattered through a class,
and returned untyped dicts. They disagreed about what a transcript even
contains, and every new feature that needed to read one was on course to add a
third.

This module is the single answer. It parses the format the engine actually
emits -- verified against the finalized ophtha transcripts -- into frozen
records, and both existing readers now consume it.

The shape it reads:

    ## 📖 Chronological Guide / ## 🌟 IMP Points / ## ❓ MCQs
    ## ✍️ Written Questions   / ## 🩺 Clinical Cases

    ### MCQ 1 **[IMP]**
    **Question:** ...
    **Options:**
    - **a.** ...
    **Correct Answer:** b. ...
    **Clinical Explanation:** ...

    ### Question 1 **[Past Exams - 2022, 2023]** **[Question Bank]**
    **Question:** ... / **Model Answer:** ... / **Clinical Explanation:** ...

    ### Clinical Case 1 **[IMP]**
    **Scenario:** ... / **Questions:** ... / **Model Answer:** ... /
    **Clinical Explanation:** ...

Blocks are kept even when a field is missing. Callers that need a complete
record filter for it; a caller that is counting what the engine produced must
not silently lose a malformed one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Every `**Name:**` that is a *field* of a question block. Nothing else is,
# however much it looks like one.
#
# Both readers used to end a field at the next line starting `**...**`, which
# meant a heading the author wrote *inside* an answer closed it: a Model Answer
# opening on `**Small bowel:**` parsed as empty, and one containing
# `**ملحوظة:**` halfway down was truncated there -- silently, because the field
# still held text, so validation passed on an answer missing its last three
# points. `**كلمة:**` is ordinary writing in a medical answer (`**Early:**` /
# `**Late:**`, `**استثناء:**`), so the boundary has to be this list rather than
# the shape of the line.
FIELD_NAMES: tuple[str, ...] = (
    "Question",
    "Question (verbatim)",
    "Options",
    "Options (verbatim)",
    "Correct Answer",
    "Model Answer",
    "Model Answer (Short)",
    "Clinical Explanation",
    "Clinical Explanation (Egyptian Arabic)",
    "Explanation",
    "Scenario",
    "Questions",
    "Source",
    "Answer",
)
# Longest first: "Model Answer (Short)" must win over "Model Answer", or the
# alternation stops at the shorter name and leaves " (Short):**" in the body.
FIELD_NAME_PATTERN = "|".join(
    re.escape(name) for name in sorted(FIELD_NAMES, key=len, reverse=True)
)

# Section keys, in document order.
GUIDE = "guide"
IMP = "imp"
MCQS = "mcqs"
WRITTEN = "written"
CASES = "cases"
SECTION_ORDER = (GUIDE, IMP, MCQS, WRITTEN, CASES)

# Matched against the text of an H2 line, lowercased. The engine writes each
# heading with an emoji and a fixed English name, but transcripts predating the
# current wording are still on disk, so each key keeps its historical spellings.
SECTION_MARKERS: dict[str, tuple[str, ...]] = {
    GUIDE: ("chronological guide",),
    IMP: ("imp points", "high-yield", "summary", "key takeaways"),
    MCQS: ("mcq", "multiple choice"),
    WRITTEN: ("written question",),
    CASES: ("clinical case",),
}

H2 = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)
H3 = re.compile(r"^###\s+(?P<title>.+?)\s*$", re.MULTILINE)
BADGE = re.compile(r"\*\*\[(?P<label>[^\]]+)\]\*\*")
YEAR = re.compile(r"\b(19|20)\d{2}\b")
# "### MCQ 4", "### Question 12", "### Clinical Case 3"
BLOCK_NUMBER = re.compile(r"^(?P<kind>.+?)\s+(?P<number>\d+)\s*(?:\*\*|$)")
# "- **a.** text" / "a) text" / "**b.** text"
OPTION_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?\(?(?P<letter>[a-eA-E])[).\].]?(?:\*\*)?[).\s]\s*(?P<text>.+?)\s*$"
)
LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.\-)])\s*")
BLOCKQUOTE_MARKER = re.compile(r"^>\s*")

PAST_EXAMS = "past_exams"
QUESTION_BANK = "question_bank"
IMPORTANT = "imp"
OTHER = "other"


@dataclass(frozen=True)
class Badge:
    """One `**[...]**` marker on a question heading.

    A heading carries several -- `**[Past Exams - 2023]** **[IMP]**` is normal
    -- which is why every reader that grabbed only the first one was wrong.
    """

    kind: str
    label: str
    years: tuple[int, ...] = ()


@dataclass(frozen=True)
class ParsedMCQ:
    number: int
    stem: str
    options: dict[str, str]
    correct_answer: str
    correct_option: str
    explanation: str
    badges: tuple[Badge, ...] = ()
    raw: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.stem and self.correct_answer)


@dataclass(frozen=True)
class ParsedWritten:
    number: int
    stem: str
    model_answer: tuple[str, ...]
    explanation: str
    badges: tuple[Badge, ...] = ()
    raw: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.stem and self.model_answer)


@dataclass(frozen=True)
class ParsedCase:
    number: int
    scenario: str
    questions: tuple[str, ...]
    model_answer: tuple[str, ...]
    explanation: str
    badges: tuple[Badge, ...] = ()
    raw: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.scenario and self.model_answer)


@dataclass(frozen=True)
class ParsedTranscript:
    lecture_title: str
    sections: dict[str, str] = field(default_factory=dict)
    mcqs: tuple[ParsedMCQ, ...] = ()
    written: tuple[ParsedWritten, ...] = ()
    cases: tuple[ParsedCase, ...] = ()
    path: Path | None = None
    module_id: str = ""

    @property
    def questions(self) -> tuple[ParsedMCQ | ParsedWritten | ParsedCase, ...]:
        """Every question in the transcript, whatever its kind."""
        return (*self.mcqs, *self.written, *self.cases)

    @property
    def years(self) -> tuple[int, ...]:
        """Every past-exam year claimed anywhere in the transcript, sorted."""
        found: set[int] = set()
        for item in self.questions:
            for badge in item.badges:
                found.update(badge.years)
        return tuple(sorted(found))


def parse_badges(text: str) -> tuple[Badge, ...]:
    """Every badge on a heading, not just the first."""
    badges = []
    for match in BADGE.finditer(text):
        label = match.group("label").strip()
        lowered = label.casefold()
        years = tuple(int(year.group(0)) for year in YEAR.finditer(label))
        if "past exam" in lowered:
            kind = PAST_EXAMS
        elif "question bank" in lowered:
            kind = QUESTION_BANK
        elif lowered == "imp":
            kind = IMPORTANT
        else:
            kind = OTHER
        badges.append(Badge(kind=kind, label=label, years=years))
    return tuple(badges)


def split_sections(content: str) -> dict[str, str]:
    """Split a transcript into its five sections, keyed by SECTION_ORDER."""
    sections: dict[str, str] = {}
    matches = list(H2.finditer(content))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        title = match.group("title").casefold()
        for key, markers in SECTION_MARKERS.items():
            if key in sections:
                continue
            if any(marker in title for marker in markers):
                sections[key] = content[match.start() : end].strip()
                break
    return sections


def split_blocks(section_text: str) -> list[str]:
    """The `### ...` blocks inside one section, heading line included."""
    if not section_text:
        return []
    blocks = []
    matches = list(H3.finditer(section_text))
    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(section_text)
        )
        blocks.append(section_text[match.start() : end].strip())
    return blocks


def block_number(block: str) -> int:
    """The N in `### MCQ N`. 0 when the heading is not numbered."""
    heading = block.split("\n", 1)[0].removeprefix("###").strip()
    match = BLOCK_NUMBER.match(heading)
    return int(match.group("number")) if match else 0


def field_value(block: str, name: str) -> str:
    """The text of a `**Name:**` field, up to the next field or separator.

    ``(verbatim)`` is accepted after the name: the exam-style prompts ask for
    verbatim past-exam wording and the engine labels those fields accordingly.
    """
    pattern = re.compile(
        rf"\*\*{re.escape(name)}\s*(?:\(verbatim\))?\s*:\*\*\s*(.+?)"
        rf"(?=\n[ \t]*(?:>[ \t]*)?\*\*(?:{FIELD_NAME_PATTERN})"
        rf"[ \t]*(?:\(verbatim\))?[ \t]*:\*\*|\n---|\Z)",
        re.DOTALL,
    )
    match = pattern.search(block)
    return match.group(1).strip() if match else ""


def _bullets(text: str, *, strip_markers: bool = True) -> tuple[str, ...]:
    """Split a model answer into its lines.

    ``strip_markers`` is False for clinical cases, where the numbering is not
    decoration but structure -- "1. **Diagnosis:**" heads a group that the
    bullets under it belong to, and a case answer flattened to bare text loses
    which finding answers which sub-question.
    """
    lines = []
    for raw_line in text.split("\n"):
        line = BLOCKQUOTE_MARKER.sub("", raw_line).strip()
        if not line:
            continue
        if strip_markers:
            line = LIST_MARKER.sub("", line).strip()
        if line:
            lines.append(line)
    return tuple(lines)


def _options(text: str) -> tuple[dict[str, str], str]:
    """Parse an options block into {letter: text}, preserving document order."""
    options: dict[str, str] = {}
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = OPTION_LINE.match(line)
        if not match:
            continue
        letter = match.group("letter").casefold()
        if letter in options:
            continue
        options[letter] = match.group("text").strip().strip("*").strip()
    return options, text.strip()


def _correct_option(correct_answer: str, options: dict[str, str]) -> str:
    """Which letter the `Correct Answer` field names, if it names one."""
    match = re.match(r"\s*\(?([a-eA-E])[).\].]", correct_answer)
    if match:
        return match.group(1).casefold()
    answer = correct_answer.strip().casefold()
    for letter, text in options.items():
        if text.casefold() and text.casefold() in answer:
            return letter
    return ""


def _mcq(block: str) -> ParsedMCQ:
    stem = field_value(block, "Question")
    options, _ = _options(field_value(block, "Options"))
    correct = field_value(block, "Correct Answer")
    return ParsedMCQ(
        number=block_number(block),
        stem=re.sub(r"^\d+[.)]\s*", "", stem).strip(),
        options=options,
        correct_answer=correct,
        correct_option=_correct_option(correct, options),
        explanation=field_value(block, "Clinical Explanation"),
        badges=parse_badges(block.split("\n", 1)[0]),
        raw=block,
    )


def parse_mcqs(section_text: str) -> tuple[ParsedMCQ, ...]:
    return tuple(_mcq(block) for block in split_blocks(section_text))


def _written(block: str) -> ParsedWritten:
    stem = field_value(block, "Question")
    return ParsedWritten(
        number=block_number(block),
        stem=re.sub(r"^\d+[.)]\s*", "", stem).strip(),
        model_answer=_bullets(field_value(block, "Model Answer")),
        explanation=field_value(block, "Clinical Explanation"),
        badges=parse_badges(block.split("\n", 1)[0]),
        raw=block,
    )


def parse_written(section_text: str) -> tuple[ParsedWritten, ...]:
    return tuple(_written(block) for block in split_blocks(section_text))


def _case(block: str) -> ParsedCase:
    return ParsedCase(
        number=block_number(block),
        scenario=field_value(block, "Scenario"),
        questions=_bullets(field_value(block, "Questions"), strip_markers=False),
        model_answer=_bullets(
            field_value(block, "Model Answer"), strip_markers=False
        ),
        explanation=field_value(block, "Clinical Explanation"),
        badges=parse_badges(block.split("\n", 1)[0]),
        raw=block,
    )


def parse_cases(section_text: str) -> tuple[ParsedCase, ...]:
    return tuple(_case(block) for block in split_blocks(section_text))


# A question block is identified by its own heading, not by which section it
# sits under. Transcripts are written with the H2 headings, but fragments are
# not -- a coverage count handed a bare list of `### MCQ N` blocks has to see
# them, and scoping to a `## ❓ MCQs` heading that is not there would silently
# report zero.
BLOCK_KINDS: tuple[tuple[str, str], ...] = (
    (CASES, "clinical case"),
    (MCQS, "mcq"),
    (WRITTEN, "question"),
)


def classify_block(block: str) -> str:
    """Which section a `### ...` block belongs to, from its heading alone."""
    heading = block.split("\n", 1)[0].removeprefix("###").strip().casefold()
    for key, prefix in BLOCK_KINDS:
        if heading.startswith(prefix):
            return key
    return ""


def group_blocks(content: str) -> dict[str, list[str]]:
    """Every question block in the document, grouped by kind."""
    grouped: dict[str, list[str]] = {MCQS: [], WRITTEN: [], CASES: []}
    for block in split_blocks(content):
        kind = classify_block(block)
        if kind:
            grouped[kind].append(block)
    return grouped


def parse_transcript(
    content: str, *, lecture_title: str = "", path: Path | None = None, module_id: str = ""
) -> ParsedTranscript:
    """Parse transcript text into a ParsedTranscript."""
    sections = split_sections(content)
    grouped = group_blocks(content)
    return ParsedTranscript(
        lecture_title=lecture_title,
        sections=sections,
        mcqs=tuple(_mcq(block) for block in grouped[MCQS]),
        written=tuple(_written(block) for block in grouped[WRITTEN]),
        cases=tuple(_case(block) for block in grouped[CASES]),
        path=path,
        module_id=module_id,
    )


def parse_transcript_file(path: Path | str, module_id: str = "") -> ParsedTranscript:
    """Parse a transcript from disk. The emoji is stripped from the title."""
    path = Path(path)
    title = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", path.stem).strip()
    return parse_transcript(
        path.read_text(encoding="utf-8"),
        lecture_title=title,
        path=path,
        module_id=module_id or path.parent.parent.name,
    )


__all__ = [
    "Badge",
    "CASES",
    "GUIDE",
    "IMP",
    "IMPORTANT",
    "MCQS",
    "OTHER",
    "PAST_EXAMS",
    "QUESTION_BANK",
    "SECTION_ORDER",
    "WRITTEN",
    "ParsedCase",
    "ParsedMCQ",
    "ParsedTranscript",
    "ParsedWritten",
    "block_number",
    "classify_block",
    "group_blocks",
    "field_value",
    "parse_badges",
    "parse_cases",
    "parse_mcqs",
    "parse_transcript",
    "parse_transcript_file",
    "parse_written",
    "split_blocks",
    "split_sections",
]
