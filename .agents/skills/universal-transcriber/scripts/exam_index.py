#!/usr/bin/env python3
"""Turning a module's exam papers into an index, once.

Every transcript used to re-derive provenance by matching its own sentences
against raw OCR text, which meant every run could get it wrong in a new way: a
question genuinely on the 2022 paper read as absent because the scan shredded
it, a question on no paper at all read as present because two medical sentences
share enough common words. A badge is a promise to a student revising by it, and
a promise that is re-guessed on every run is not a promise.

So the papers are read **once**, into a structured index: each question with its
options, its answer where the paper marks one, the file and section it came
from, and the year that section can honestly claim. Drafting then looks a
question up instead of hunting for it, and every later transcript in the module
inherits the same answer.

Three things the index settles that raw text cannot:

1. **Which year a question may claim.** A compiled bank holds `--- End 2022 ---`
   beside an unlabelled `--- Page 12 ---`; a question under the latter claims no
   year, however the *file* is named. A single scanned paper is marked
   `--- Page 1 ---` by the OCR alone -- that is pagination, and there the
   filename is the only statement of which paper it is.
2. **The same question across papers.** Asked in 2022 and again in 2023, it is
   one entry carrying both years, not two entries disagreeing.
3. **What the scan destroyed.** A question the OCR left unreadable is recorded
   as damaged rather than dropped, so a human resolves it once, in one place,
   instead of every writer re-deciding it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from exam_years import extract_filename_exam_years
from provenance_audit import Section, is_compiled_bank, normalize, split_sections

INDEX_NAME = "exam-index.json"
SCHEMA_VERSION = 1

# "12." / "12)" / "12 -" at the start of a line: how every paper here numbers.
QUESTION_START = re.compile(r"^\s*(\d{1,3})\s*[.)\-]\s*(.*)$")
# Not every paper numbers at all. One exports as bullets with the stem on the
# next line; dropping it would have silently indexed nothing from that year.
BULLET_START = re.compile(r"^\s*[*\u2022\u25cf]\s*(.*)$")
# An option label, with the many things OCR turns the bullet into. The leading
# marker (+, <, #, =, *) is how these papers flag the correct answer; which one
# varies by who prepared the file, so all of them are accepted.
OPTION_START = re.compile(
    r"^\s*(?P<mark>[+<#=*✓✔]?)\s*[(\[]?(?P<key>[a-dA-D])\s*[.)\]]\s*(?P<text>.*)$"
)
ANSWER_MARKS = "+<#=*✓✔"
MODEL_ANSWER = re.compile(r"^\s*model answer\s*:?\s*(.*)$", re.IGNORECASE)
# Egyptian exam papers leave the answer space as a run of dot leaders. Left on
# the stem they are invisible to a reader and decisive to a matcher: the same
# question printed with them and without reads as two questions, and the copy
# from the dated paper stops lending the copy in the bank its year.
DOT_LEADERS = re.compile(r"[.\u2026\u06d4_\-]{4,}")
# A paper's own headings -- "B-Laboratory medicine :", "SHORT ESSAY QUESTIONS"
# -- sit between questions with nothing to mark them as headings. Absorbed into
# the stem above them they change that question's identity, and the same
# question printed in two papers stops merging: one copy carries the year, the
# other carries the questions, and neither carries both.
PAPER_HEADING = re.compile(
    r"^\s*(?:[A-Z]\s*[-)]\s*[A-Za-z].{0,40}|[A-Z][A-Z \t]{6,}|Model answer)\s*:?\s*$"
)

# A stem shorter than this is a fragment the OCR tore off, not a question.
MIN_STEM_CHARS = 12
# An MCQ needs at least this many options to be one.
MIN_OPTIONS = 2
# Share of a stem's tokens that must read as words or numbers. Counting
# *characters* does not work -- `The Most Ré 7~5 ~ 2026 Q) igital ime a` is 83%
# letters and digits and still unreadable. Nor does demanding long words:
# "Pneumothorax is" is three-quarters short words and perfectly clear. What a
# wrecked scan leaves behind is tokens that are neither word nor number --
# `7~5`, `=A`, `©)`, `#` -- so those are what get counted.
MIN_LEGIBLE_RATIO = 0.70
WORDLIKE = re.compile(r"^[^\W\d_]+$|^\d+$", re.UNICODE)


class ExamIndexError(RuntimeError):
    """Raised when an index cannot be built or read."""


@dataclass
class Occurrence:
    """One appearance of a question, in one paper."""

    source: str
    section: str
    year: int | None

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "section": self.section, "year": self.year}


@dataclass
class IndexedQuestion:
    """One question, however many papers carried it."""

    number: int
    kind: str  # "mcq" | "written"
    stem: str
    options: dict[str, str] = field(default_factory=dict)
    answer: str | None = None
    model_answer: str = ""
    occurrences: list[Occurrence] = field(default_factory=list)
    legible: bool = True

    @property
    def years(self) -> list[int]:
        """Every year this question may honestly badge."""
        return sorted({o.year for o in self.occurrences if o.year})

    @property
    def sources(self) -> list[str]:
        return sorted({o.source for o in self.occurrences})

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "stem": self.stem,
            "options": self.options,
            "answer": self.answer,
            "model_answer": self.model_answer,
            "years": self.years,
            "sources": self.sources,
            "occurrences": [o.as_dict() for o in self.occurrences],
            "legible": self.legible,
        }


def clean_stem(text: str) -> str:
    """The question without the blank the student was meant to write in."""
    return re.sub(r"\s+", " ", DOT_LEADERS.sub(" ", text)).strip(" .:\u2026")


def _legible(text: str) -> bool:
    stripped = unicodedata.normalize("NFKC", text).strip()
    if len(stripped) < MIN_STEM_CHARS:
        return False
    tokens = stripped.split()
    if not tokens:
        return False
    words = sum(
        1 for token in tokens if WORDLIKE.match(token.strip(".,:;()[]?!'\"-/"))
    )
    return words / len(tokens) >= MIN_LEGIBLE_RATIO


def _section_year(
    section: Section, source_name: str, compiled: bool
) -> int | None:
    years = section.years if compiled else extract_filename_exam_years(source_name)
    return years[0] if years else None


def parse_source(source_name: str, text: str) -> list[IndexedQuestion]:
    """Every question this one paper carries, with where in it each sits."""
    sections = split_sections(text)
    compiled = is_compiled_bank(sections)
    questions: list[IndexedQuestion] = []
    for section in sections:
        year = _section_year(section, source_name, compiled)
        current: IndexedQuestion | None = None
        collecting_model_answer = False
        counter = 0
        for line in text[section.start : section.end].splitlines():
            start = QUESTION_START.match(line)
            bullet = BULLET_START.match(line)
            option = OPTION_START.match(line)
            # A numbered line that is also option-shaped is an option; papers
            # number questions, not answers.
            if (start or bullet) and not option:
                if current:
                    questions.append(current)
                counter += 1
                opener = start or bullet
                assert opener is not None  # one of the two matched
                stem = clean_stem(
                    start.group(2) if start else opener.group(1)
                )
                current = IndexedQuestion(
                    number=int(start.group(1)) if start else counter,
                    kind="mcq",
                    stem=stem,
                    occurrences=[Occurrence(source_name, section.label, year)],
                    legible=_legible(stem),
                )
                collecting_model_answer = False
                continue
            if current is None:
                continue
            answer_line = MODEL_ANSWER.match(line)
            if answer_line:
                current.kind = "written"
                current.model_answer = answer_line.group(1).strip()
                collecting_model_answer = True
                continue
            if collecting_model_answer:
                if line.strip():
                    current.model_answer = f"{current.model_answer} {line.strip()}".strip()
                continue
            if option and option.group("text").strip():
                key = option.group("key").casefold()
                current.options[key] = option.group("text").strip()
                if option.group("mark") in ANSWER_MARKS and option.group("mark"):
                    current.answer = key
                continue
            if option and not option.group("text").strip():
                # "A." alone on its line: the text is the next line. Papers
                # exported from PDF do this constantly.
                current.options.setdefault(option.group("key").casefold(), "")
                if option.group("mark") in ANSWER_MARKS and option.group("mark"):
                    current.answer = option.group("key").casefold()
                continue
            if line.strip() and current.options:
                # Continuation of the last option that was left empty.
                for key in reversed(list(current.options)):
                    if not current.options[key]:
                        current.options[key] = line.strip()
                        break
                else:
                    pass
            elif line.strip() and not current.options:
                if PAPER_HEADING.match(line):
                    questions.append(current)
                    current = None
                    continue
                current.stem = clean_stem(f"{current.stem} {line.strip()}")
                current.legible = _legible(current.stem)
        if current:
            questions.append(current)
    for question in questions:
        if question.kind == "mcq" and len(question.options) < MIN_OPTIONS:
            question.kind = "written"
    return questions


def _merge_key(question: IndexedQuestion) -> str:
    return " ".join(normalize(question.stem)[:12])


def merge(questions: list[IndexedQuestion]) -> list[IndexedQuestion]:
    """One entry per question, carrying every paper that asked it."""
    merged: dict[str, IndexedQuestion] = {}
    for question in questions:
        key = _merge_key(question)
        if not key:
            continue
        if key not in merged:
            merged[key] = question
            continue
        kept = merged[key]
        kept.occurrences.extend(question.occurrences)
        # Prefer the copy that survived the scan best.
        if not kept.answer and question.answer:
            kept.answer = question.answer
        if len(question.options) > len(kept.options):
            kept.options = question.options
        if len(question.model_answer) > len(kept.model_answer):
            kept.model_answer = question.model_answer
        if question.legible and not kept.legible:
            kept.stem, kept.legible = question.stem, True
    return list(merged.values())


def build_index(questions_dir: Path, module_id: str) -> dict[str, Any]:
    """Read every paper in Questions/ into one index."""
    files = sorted(
        path
        for path in questions_dir.glob("*")
        if path.is_file() and path.suffix.lower() in {".txt", ".md"}
    )
    if not files:
        raise ExamIndexError(
            f"No extractable question files in {questions_dir}. Convert PDFs "
            "first (--sync-sources runs OCR), then build the index."
        )
    collected: list[IndexedQuestion] = []
    sources: list[dict[str, Any]] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        sections = split_sections(text)
        compiled = is_compiled_bank(sections)
        found = parse_source(path.name, text)
        collected.extend(found)
        sources.append(
            {
                "file": path.name,
                "kind": "question_bank" if compiled else "past_exam",
                "years": sorted(
                    {y for s in sections for y in s.years}
                    if compiled
                    else extract_filename_exam_years(path.name)
                ),
                "questions": len(found),
            }
        )
    merged = merge(collected)
    merged.sort(key=lambda q: (q.kind, -len(q.occurrences), q.stem[:40]))
    return {
        "schema_version": SCHEMA_VERSION,
        "module": module_id,
        "sources": sources,
        "questions": {
            f"{module_id}-{index + 1:04d}": question.as_dict()
            for index, question in enumerate(merged)
        },
    }


REPAIR_KEY = "repaired_by_hand"


def carry_over_repairs(
    fresh: dict[str, Any], questions_dir: Path
) -> dict[str, Any]:
    """Keep entries a human fixed by hand when the index is rebuilt.

    A question the scan destroyed is repaired once, by someone reading the
    actual page. Rebuilding must not throw that away -- otherwise the repair
    has to be redone after every new paper is added, which is exactly the
    per-run re-deciding the index exists to end.
    """
    try:
        previous = load_index(questions_dir)
    except (ExamIndexError, json.JSONDecodeError):
        return fresh
    repaired = {
        key: question
        for key, question in previous.get("questions", {}).items()
        if question.get(REPAIR_KEY)
    }
    if not repaired:
        return fresh
    known = {
        " ".join(normalize(question["stem"])[:12])
        for question in fresh["questions"].values()
    }
    for key, question in repaired.items():
        if " ".join(normalize(question["stem"])[:12]) not in known:
            fresh["questions"][key] = question
    fresh["carried_repairs"] = sorted(repaired)
    return fresh


def write_index(index: dict[str, Any], questions_dir: Path) -> Path:
    path = questions_dir / INDEX_NAME
    path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_index(questions_dir: Path) -> dict[str, Any]:
    path = questions_dir / INDEX_NAME
    if not path.is_file():
        raise ExamIndexError(
            f"No {INDEX_NAME} in {questions_dir}. Build it once with "
            "--build-exam-index before drafting."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def render_summary(index: dict[str, Any]) -> str:
    questions = index["questions"]
    damaged = [k for k, q in questions.items() if not q["legible"]]
    dated = [k for k, q in questions.items() if q["years"]]
    lines = [
        f"{len(questions)} question(s) indexed from {len(index['sources'])} file(s)",
        f"  {len(dated)} carry a defensible exam year; "
        f"{len(questions) - len(dated)} are bank-only (no year claim)",
        f"  {len(damaged)} need a human eye (OCR damage)",
        "",
    ]
    for source in index["sources"]:
        years = ", ".join(str(y) for y in source["years"]) or "—"
        lines.append(
            f"  {source['file']:<45} {source['kind']:<14} "
            f"{source['questions']:>4} q  years: {years}"
        )
    return "\n".join(lines)
