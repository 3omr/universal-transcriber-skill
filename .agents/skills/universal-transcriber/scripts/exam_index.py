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
# The margin of a scanned paper OCRs as punctuation on the front of the line --
# the page border becomes "|", a speck becomes "," or "!". Left there it stops
# "| 12) MLI of cannabis" from reading as question 12, and the parser then feeds
# the next question's text into the previous one: two entries were found holding
# a *different* question's options, which is worse than holding none.
GUTTER = re.compile(r"^[\s|!;,:'\"`\u2018\u2019\u201c\u201d~^\u00b7]+")
# One option label anywhere in a line. These papers routinely print all four on
# a single line ("a. Naloxone, b) Physostigmine C. Neostigmine. d. charcoal."),
# and matching only at the start kept the first and lost the rest.
OPTION_TOKEN = re.compile(
    r"(?:^|[\s,;|/.)\]])"
    r"(?P<mark>[+<#=*✓✔])?\s*"
    r"(?P<open>[(\[])?\s*"
    r"(?P<key>[a-dA-D])\s*"
    r"(?P<dot>[.)\]])"
)
# The examiner rings the correct answer in pen. The scan renders that ring as
# brackets around the label -- "(a) Green" beside a bare "b. Black" -- or, when
# it swallows the letter, as "@ Disseminated intravascular coagulopathy".
# An option whose *label* the scan destroyed, which is most of what is left
# after the runs above: "Ce Pilutional therapy", "S, Alkaline potash", "cd.
# Nitric acid", ". Gastric lavage", "#@)Asphyxia from laryngeal edema". The
# prose is perfectly readable; only the letter in front of it is gone. Which
# letter it was is not a guess -- it is the one missing from a run of four.
# The damage itself is the guard: a line that begins with a plain word is an
# option wrapping onto a second line, and must not be taken for a new one.
LOST_LABEL = re.compile(
    r"^\s*(?:(?P<mark>[+<#=*✓✔@\u00a9\u00ae\u25cb\u25ce])\s*)?"
    r"(?:[^\w\s]{1,3}\s*|(?P<letters>[A-Za-z0-9]{1,2})(?:\s*[.,)\]]\s*|\s+(?=[A-Z])))*"
    r"(?P<text>[A-Za-z(\"].{3,})$"
)
LOST_LABEL_MARKS = "+<#=*✓✔@\u00a9\u00ae\u25cb\u25ce"
OPTION_KEYS = "abcd"
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


def _option_run(line: str, taken: str) -> list[tuple[str, str, bool]]:
    """Every option on this line, as (key, text, ringed).

    A line is only read as options if it *starts* with a label and the labels
    run in order -- "a. ... b. ... c. ..." -- which is what keeps a sentence
    ending in "vitamin B." from being mistaken for option b.

    `ringed` is the examiner's pen: a label in brackets where its neighbours
    have none. It is reported per option rather than decided here, because one
    ringed option among four is an answer key and four ringed options are just
    how that scan prints brackets.
    """
    matches = list(OPTION_TOKEN.finditer(line))
    if not matches or matches[0].start("key") > 2:
        return []
    run: list[tuple[str, str, bool]] = []
    previous = ""
    for position, match in enumerate(matches):
        key = match.group("key").casefold()
        expected = taken and key in OPTION_KEYS[OPTION_KEYS.index(taken[-1]) + 1 :]
        if key <= previous or (not run and not (key == "a" or expected)):
            break
        end = (
            matches[position + 1].start()
            if position + 1 < len(matches)
            else len(line)
        )
        text = line[match.end() : end].lstrip(")] \t").strip()
        ringed = bool(match.group("open")) or bool(match.group("mark"))
        run.append((key, text, ringed))
        previous = key
    return run


def _next_key(taken: str) -> str | None:
    """The label a ringed option must have carried, when the scan ate it."""
    if not taken:
        return OPTION_KEYS[0]
    position = OPTION_KEYS.index(taken[-1]) + 1
    return OPTION_KEYS[position] if position < len(OPTION_KEYS) else None


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
        for raw_line in text[section.start : section.end].splitlines():
            line = GUTTER.sub("", raw_line).rstrip()
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
            lost = LOST_LABEL.match(line)
            # Something must actually be damaged for this to be a lost label:
            # a pen mark, a misread letter, or punctuation where the label was.
            # A line opening on a plain word is an option wrapping, not a new one.
            if lost and not (
                lost.group("mark") or lost.group("letters") or not line.lstrip()[:1].isalnum()
            ):
                lost = None
            if lost and current.options and len(current.options) < len(OPTION_KEYS):
                key = _next_key("".join(sorted(current.options)))
                if key:
                    current.options[key] = lost.group("text").strip()
                    # A pen-ring or a "+" survived the label it was drawn on:
                    # that is still the paper telling us the answer.
                    if any(c in LOST_LABEL_MARKS for c in lost.group("mark") or ""):
                        current.answer = key
                    continue
            run = _option_run(line, "".join(sorted(current.options)))
            if run:
                ringed = [key for key, _, is_ringed in run if is_ringed]
                # A single ringed label reading as one the question already has
                # is the scan mangling a later letter, not the paper repeating
                # itself. Overwriting here is how option (a) once ended up
                # holding "All of the above".
                if len(run) == 1 and run[0][0] in current.options:
                    run = []
                for key, text_part, _ in run:
                    if text_part or key not in current.options:
                        current.options[key] = text_part
                # One ringed option among several is the answer key. All of them
                # ringed is just how that scan draws brackets, and so tells us
                # nothing. An option alone on its line only counts when it
                # carries a real answer mark rather than brackets.
                several = len(ringed) == 1 and len(run) > 1
                alone = len(run) == 1 and ringed and option and option.group("mark")
                if several or alone:
                    current.answer = ringed[0]
                continue
            if line.strip() and current.options:
                # Continuation of the last option that was left empty.
                for key in reversed(list(current.options)):
                    if not current.options[key]:
                        current.options[key] = line.strip()
                        break
                else:
                    # Nothing empty to fill, so this is the last option running
                    # onto a second line. Dropping it truncated the option at
                    # the line break, which reads as a different answer.
                    last = list(current.options)[-1]
                    current.options[last] = (
                        f"{current.options[last]} {line.strip()}".strip()
                    )
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
    by_stem = {
        " ".join(normalize(question["stem"])[:12]): key
        for key, question in fresh["questions"].items()
    }
    for key, question in repaired.items():
        # The repair replaces what the parser produced. Keeping the fresh entry
        # instead was the old behaviour and it silently discarded every repair:
        # repairing a question rewrites its stem, so the rebuilt copy no longer
        # looks like the repaired one and the "only if missing" rule never
        # fired. Every copy is absorbed, not just the first -- a question asked
        # in two years reaches the rebuild as two entries, and the repair must
        # come away carrying both years.
        for match in _rebuilt_counterparts(question, fresh["questions"], by_stem):
            _absorb(question, fresh["questions"].pop(match))
        # Remembering the stem it was repaired from lets the next rebuild find
        # it by name instead of by resemblance.
        question.setdefault(
            "repaired_from",
            " ".join(normalize(question["stem"])[:12]),
        )
        fresh["questions"][key] = question
    fresh["carried_repairs"] = sorted(repaired)
    _collapse_duplicates(fresh["questions"])
    return fresh


def _collapse_duplicates(questions: dict[str, dict[str, Any]]) -> None:
    """Fold entries that a repair has just revealed to be the same question.

    Two copies of one question can reach the index looking different -- one
    stem carrying its mark allocation, the other its own options -- and so miss
    the merge at build time. Repairing them makes them identical, and they must
    then carry *both* years: that is the difference between a badge reading
    2023 and one reading 2023, 2025.
    """
    seen: dict[str, str] = {}
    for key in list(questions):
        question = questions[key]
        stem_key = " ".join(normalize(question["stem"])[:12])
        if not stem_key:
            continue
        first = seen.get(stem_key)
        if first is None:
            seen[stem_key] = key
            continue
        kept = questions[first]
        occurrences = kept.get("occurrences", []) + question.get("occurrences", [])
        unique = {
            (o.get("source"), o.get("section"), o.get("year")): o for o in occurrences
        }
        kept["occurrences"] = list(unique.values())
        kept["years"] = sorted({o["year"] for o in kept["occurrences"] if o.get("year")})
        kept["sources"] = sorted({o["source"] for o in kept["occurrences"]})
        kept["answer"] = kept.get("answer") or question.get("answer")
        del questions[key]


def _option_fingerprint(question: dict[str, Any]) -> set[str]:
    """What a question's options say, ignoring how the scan spelled it."""
    return {
        " ".join(normalize(text)[:6])
        for text in (question.get("options") or {}).values()
        if text and len(normalize(text)) >= 2
    }


def _absorb(repaired: dict[str, Any], superseded: dict[str, Any]) -> None:
    """Give the repaired entry the papers the copy it replaces was found in."""
    occurrences = repaired.get("occurrences", []) + superseded.get("occurrences", [])
    unique = {
        (o.get("source"), o.get("section"), o.get("year")): o for o in occurrences
    }
    repaired["occurrences"] = list(unique.values())
    repaired["years"] = sorted({o["year"] for o in unique.values() if o.get("year")})
    repaired["sources"] = sorted({o["source"] for o in unique.values()})


def _rebuilt_counterparts(
    repaired: dict[str, Any],
    fresh: dict[str, dict[str, Any]],
    by_stem: dict[str, str],
) -> list[str]:
    """The freshly parsed entry a hand-repaired one supersedes, if any.

    Identity has to survive the repair itself. The stem is the natural key and
    is tried first -- including the stem the entry was repaired *from*, which
    later rebuilds carry -- but a repair that rewrote the stem is exactly the
    case where that fails, so the options are the fallback: OCR damages the
    letter in front of an option far more often than the words in it.
    """
    matches: list[str] = []
    for candidate in (
        repaired.get("repaired_from"),
        " ".join(normalize(repaired["stem"])[:12]),
    ):
        if candidate and by_stem.get(candidate) not in (None, *matches):
            matches.append(by_stem[candidate])
    wanted = _option_fingerprint(repaired)
    if len(wanted) >= 2:
        for key, question in fresh.items():
            if key in matches:
                continue
            found = _option_fingerprint(question)
            if not found:
                continue
            overlap = len(wanted & found) / max(len(wanted), len(found))
            if overlap >= 0.6:
                matches.append(key)
    return matches


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
