#!/usr/bin/env python3
"""Aggregate every question in a module into one bank.

Until now a question existed only inside the transcript that produced it. That
is the wrong unit for revision: nobody studies one lecture's MCQs the night
before a paper, they study the module's. And nothing could answer the question
a student actually asks -- "what has been asked every year since 2022?" --
because no two lectures' questions were ever in the same place.

This walks a module's transcripts through transcript_parser, collects the MCQs,
written questions and clinical cases into one typed bank, and marks the
duplicates. Duplicates are marked rather than dropped: the same question
appearing in three lectures is a signal about what the examiners care about,
and deleting two copies throws that signal away. Exam sampling then uses it as
a weight.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from transcript_parser import (
    IMPORTANT,
    PAST_EXAMS,
    QUESTION_BANK,
    ParsedCase,
    ParsedMCQ,
    ParsedTranscript,
    ParsedWritten,
    parse_transcript_file,
)

MCQ = "mcq"
WRITTEN = "written"
CASE = "case"
KINDS = (MCQ, WRITTEN, CASE)

# Two stems counted as the same question when this much of their wording
# overlaps. Calibrated on the ophtha module: "Define: Glaucoma" and "Define
# glaucoma." land at 1.0, while distinct questions about the same topic
# ("Mention 5 signs of glaucoma") sit well below.
SIMILARITY_THRESHOLD = 0.85
# Words that say nothing about which question this is.
STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "define", "describe",
        "for", "from", "give", "in", "is", "list", "mention", "of", "on", "or",
        "outline", "state", "that", "the", "to", "what", "which", "with",
        "write", "your",
    }
)


class QuestionBankError(RuntimeError):
    """Raised when a bank cannot be built."""


@dataclass(frozen=True)
class BankQuestion:
    """One question, lifted out of the transcript that produced it."""

    question_id: str
    kind: str
    lecture: str
    module_id: str
    stem: str
    options: dict[str, str] = field(default_factory=dict)
    correct_option: str = ""
    answer: str = ""
    model_answer: tuple[str, ...] = ()
    explanation: str = ""
    scenario: str = ""
    sub_questions: tuple[str, ...] = ()
    years: tuple[int, ...] = ()
    badge_labels: tuple[str, ...] = ()
    is_past_exam: bool = False
    is_question_bank: bool = False
    is_important: bool = False
    duplicate_of: str = ""

    @property
    def is_duplicate(self) -> bool:
        return bool(self.duplicate_of)

    @property
    def sort_key(self) -> tuple[str, str, int]:
        return (self.lecture, self.kind, _leading_number(self.question_id))


@dataclass(frozen=True)
class QuestionBank:
    module_id: str
    questions: tuple[BankQuestion, ...] = ()
    lectures: tuple[str, ...] = ()

    @property
    def unique(self) -> tuple[BankQuestion, ...]:
        """One representative per distinct question."""
        return tuple(question for question in self.questions if not question.is_duplicate)

    @property
    def duplicate_count(self) -> int:
        return len(self.questions) - len(self.unique)

    @property
    def years(self) -> tuple[int, ...]:
        found: set[int] = set()
        for question in self.questions:
            found.update(question.years)
        return tuple(sorted(found))

    def by_kind(self, kind: str) -> tuple[BankQuestion, ...]:
        return tuple(question for question in self.questions if question.kind == kind)

    def repeat_count(self, question: BankQuestion) -> int:
        """How many copies of this question exist across the module."""
        return 1 + sum(
            1 for other in self.questions if other.duplicate_of == question.question_id
        )


def _leading_number(question_id: str) -> int:
    match = re.search(r"(\d+)$", question_id)
    return int(match.group(1)) if match else 0


def normalize_stem(stem: str) -> str:
    """Fold a stem to its comparable form: no markup, no punctuation, no case."""
    text = re.sub(r"\*\*|__|`", " ", stem)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def stem_tokens(stem: str) -> frozenset[str]:
    """The words that identify a question, minus the ones every stem has."""
    return frozenset(
        token
        for token in normalize_stem(stem).split()
        if len(token) > 2 and token not in STOP_WORDS
    )


def similarity(first: str, second: str) -> float:
    """Jaccard overlap of two stems' identifying words."""
    left, right = stem_tokens(first), stem_tokens(second)
    if not left or not right:
        return 1.0 if normalize_stem(first) == normalize_stem(second) else 0.0
    return len(left & right) / len(left | right)


def _badge_fields(item: ParsedMCQ | ParsedWritten | ParsedCase) -> dict[str, object]:
    years: set[int] = set()
    labels = []
    kinds = set()
    for badge in item.badges:
        years.update(badge.years)
        labels.append(badge.label)
        kinds.add(badge.kind)
    return {
        "years": tuple(sorted(years)),
        "badge_labels": tuple(labels),
        "is_past_exam": PAST_EXAMS in kinds,
        "is_question_bank": QUESTION_BANK in kinds,
        "is_important": IMPORTANT in kinds,
    }


def _question_id(module_id: str, lecture: str, kind: str, number: int) -> str:
    slug = re.sub(r"[^\w]+", "-", lecture).strip("-").casefold() or "lecture"
    return f"{module_id}:{slug}:{kind}:{number:03d}"


def questions_from_transcript(parsed: ParsedTranscript) -> list[BankQuestion]:
    """Every question in one transcript, as bank entries."""
    module_id = parsed.module_id or "module"
    lecture = parsed.lecture_title
    collected: list[BankQuestion] = []

    for mcq in parsed.mcqs:
        collected.append(
            BankQuestion(
                question_id=_question_id(module_id, lecture, MCQ, mcq.number),
                kind=MCQ,
                lecture=lecture,
                module_id=module_id,
                stem=mcq.stem,
                options=dict(mcq.options),
                correct_option=mcq.correct_option,
                answer=mcq.correct_answer,
                explanation=mcq.explanation,
                **_badge_fields(mcq),  # type: ignore[arg-type]
            )
        )
    for written in parsed.written:
        collected.append(
            BankQuestion(
                question_id=_question_id(module_id, lecture, WRITTEN, written.number),
                kind=WRITTEN,
                lecture=lecture,
                module_id=module_id,
                stem=written.stem,
                model_answer=written.model_answer,
                answer="; ".join(written.model_answer),
                explanation=written.explanation,
                **_badge_fields(written),  # type: ignore[arg-type]
            )
        )
    for case in parsed.cases:
        collected.append(
            BankQuestion(
                question_id=_question_id(module_id, lecture, CASE, case.number),
                kind=CASE,
                lecture=lecture,
                module_id=module_id,
                # The scenario identifies a case; its sub-questions do not.
                # Every case asks "What is the most likely diagnosis?" and
                # friends, so keying on those collapsed thirteen distinct
                # vignettes into two.
                stem=case.scenario or " ".join(case.questions),
                scenario=case.scenario,
                model_answer=case.model_answer,
                sub_questions=case.questions,
                answer="; ".join(case.model_answer),
                explanation=case.explanation,
                **_badge_fields(case),  # type: ignore[arg-type]
            )
        )
    return collected


def mark_duplicates(
    questions: list[BankQuestion], threshold: float = SIMILARITY_THRESHOLD
) -> list[BankQuestion]:
    """Point every repeat at the first copy of itself.

    Comparison is within a kind: an MCQ and a written question that happen to
    share wording are different questions, because they are answered
    differently.
    """
    resolved: list[BankQuestion] = []
    canonical: dict[str, list[tuple[frozenset[str], str, str]]] = {kind: [] for kind in KINDS}

    for question in questions:
        tokens = stem_tokens(question.stem)
        normalized = normalize_stem(question.stem)
        match_id = ""
        for seen_tokens, seen_normalized, seen_id in canonical.get(question.kind, []):
            if normalized and normalized == seen_normalized:
                match_id = seen_id
                break
            if not tokens or not seen_tokens:
                continue
            overlap = len(tokens & seen_tokens) / len(tokens | seen_tokens)
            if overlap >= threshold:
                match_id = seen_id
                break
        if match_id:
            resolved.append(
                BankQuestion(**{**question.__dict__, "duplicate_of": match_id})
            )
        else:
            canonical.setdefault(question.kind, []).append(
                (tokens, normalized, question.question_id)
            )
            resolved.append(question)

    # A repeat often carries exam years its first copy does not -- the same
    # question badged [Past Exams - 2023, 2024] in one lecture and unbadged in
    # another. The canonical copy is the one every caller reads, so it has to
    # end up knowing every year the question was actually asked.
    merged_years: dict[str, set[int]] = {}
    for question in resolved:
        if question.duplicate_of:
            merged_years.setdefault(question.duplicate_of, set()).update(question.years)
    if not merged_years:
        return resolved
    return [
        BankQuestion(
            **{
                **question.__dict__,
                "years": tuple(sorted(set(question.years) | merged_years[question.question_id])),
                "is_past_exam": question.is_past_exam
                or bool(merged_years[question.question_id]),
            }
        )
        if question.question_id in merged_years
        else question
        for question in resolved
    ]


def transcript_paths(transcripts_dir: Path) -> list[Path]:
    """The finalized transcripts in a module, Index.md and drafts excluded."""
    if not transcripts_dir.is_dir():
        return []
    return sorted(
        path
        for path in transcripts_dir.glob("*.md")
        if path.name != "Index.md" and not path.name.endswith(".draft.md")
    )


def build_bank(
    transcripts_dir: Path | str,
    module_id: str = "",
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> QuestionBank:
    """Collect one module's transcripts into a single bank."""
    directory = Path(transcripts_dir)
    paths = transcript_paths(directory)
    if not paths:
        raise QuestionBankError(f"No finalized transcripts in {directory}")

    collected: list[BankQuestion] = []
    lectures: list[str] = []
    for path in paths:
        parsed = parse_transcript_file(path, module_id=module_id)
        lectures.append(parsed.lecture_title)
        collected.extend(questions_from_transcript(parsed))

    return QuestionBank(
        module_id=module_id or (collected[0].module_id if collected else "module"),
        questions=tuple(mark_duplicates(collected, threshold)),
        lectures=tuple(lectures),
    )


def filter_bank(
    bank: QuestionBank,
    *,
    kinds: tuple[str, ...] = KINDS,
    years: tuple[int, ...] = (),
    past_exams_only: bool = False,
    important_only: bool = False,
    include_duplicates: bool = False,
) -> tuple[BankQuestion, ...]:
    """Narrow a bank to the questions a caller asked for."""
    pool = bank.questions if include_duplicates else bank.unique
    selected = []
    for question in pool:
        if question.kind not in kinds:
            continue
        if years and not set(question.years) & set(years):
            continue
        if past_exams_only and not question.is_past_exam:
            continue
        if important_only and not question.is_important:
            continue
        selected.append(question)
    return tuple(selected)


def sample_exam(
    bank: QuestionBank,
    count: int,
    *,
    kinds: tuple[str, ...] = (MCQ,),
    years: tuple[int, ...] = (),
    seed: int | None = None,
) -> tuple[BankQuestion, ...]:
    """Draw an exam from the bank, spread across lectures.

    Two rules, both from how a real paper behaves. Questions are taken a
    lecture at a time in rotation, so a 50-question paper cannot come mostly
    from whichever lecture had the most questions extracted. Within a lecture
    the ones that recur across years come first, because a question asked in
    four papers is likelier to be asked again than one asked once.
    """
    pool = filter_bank(bank, kinds=kinds, years=years)
    if not pool:
        return ()

    by_lecture: dict[str, list[BankQuestion]] = {}
    for question in pool:
        by_lecture.setdefault(question.lecture, []).append(question)

    shuffler = random.Random(seed)
    for lecture_questions in by_lecture.values():
        shuffler.shuffle(lecture_questions)
        lecture_questions.sort(
            key=lambda question: (
                -bank.repeat_count(question),
                -len(question.years),
                0 if question.is_past_exam else 1,
            )
        )

    drawn: list[BankQuestion] = []
    lecture_names = sorted(by_lecture)
    shuffler.shuffle(lecture_names)
    while len(drawn) < count:
        progressed = False
        for name in lecture_names:
            if len(drawn) >= count:
                break
            if by_lecture[name]:
                drawn.append(by_lecture[name].pop(0))
                progressed = True
        if not progressed:
            break
    return tuple(drawn)


def year_histogram(bank: QuestionBank) -> Counter[int]:
    """How many questions claim each past-exam year."""
    counts: Counter[int] = Counter()
    for question in bank.questions:
        for year in question.years:
            counts[year] += 1
    return counts


def render_summary(bank: QuestionBank) -> str:
    """The bank's shape, for the CLI."""
    lines = [
        f"Question bank for {bank.module_id}: "
        f"{len(bank.unique)} unique of {len(bank.questions)} across "
        f"{len(bank.lectures)} lecture(s)",
    ]
    for kind in KINDS:
        of_kind = bank.by_kind(kind)
        unique = [question for question in of_kind if not question.is_duplicate]
        lines.append(f"  {kind:<8} {len(unique):>4} unique / {len(of_kind):>4} total")
    if bank.duplicate_count:
        lines.append(
            f"  {bank.duplicate_count} repeat(s) kept and marked -- a question asked "
            "in several lectures is a signal, not noise"
        )
    histogram = year_histogram(bank)
    if histogram:
        spread = ", ".join(f"{year}: {count}" for year, count in sorted(histogram.items()))
        lines.append(f"  past exam years -- {spread}")
    return "\n".join(lines)


__all__ = [
    "CASE",
    "KINDS",
    "MCQ",
    "SIMILARITY_THRESHOLD",
    "WRITTEN",
    "BankQuestion",
    "QuestionBank",
    "QuestionBankError",
    "build_bank",
    "filter_bank",
    "mark_duplicates",
    "normalize_stem",
    "questions_from_transcript",
    "render_summary",
    "sample_exam",
    "similarity",
    "stem_tokens",
    "transcript_paths",
    "year_histogram",
]
