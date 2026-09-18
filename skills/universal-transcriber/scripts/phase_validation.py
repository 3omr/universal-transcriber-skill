#!/usr/bin/env python3
"""Everything that decides whether a generated section is usable.

The five-phase pipeline is only as good as its refusal to accept a bad answer,
and that refusal is a surprising amount of code: heading shapes, callout
whitelists, badge grammar, MCQ option keys, exam-year claims, question
provenance against the evidence catalog, duplicate detection and renumbering,
and the OCR-damage heuristics that catch a source that was read badly rather
than a model that answered badly.

Roughly a fifth of the engine, and none of it knows or cares which backend
produced the text it is checking -- which is exactly why it comes out cleanly.
The engine re-exports every name here, so `universal_transcribe.validate_mcqs`
still resolves.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from engine_utils import _catalog_entry_is_available, _unique_strings, is_empty_sentinel
from exam_years import ARABIC_DIGITS, is_reasonable_exam_year
from output_assembly import format_markdown_tables
from question_prompts import IMP_HEADINGS, NO_MCQS, NO_WRITTEN
from provenance_audit import index_years
from source_naming import normalize_source_key, normalize_source_stem
from transcriber_models import (
    CaseEvidence,
    QueryResult,
    QuestionEvidence,
    QuestionProvenanceContext,
)

ALLOWED_CALLOUTS = {"NOTE", "IMPORTANT", "WARNING", "CAUTION", "TIP"}
SECTION_HEADINGS = (
    "## 📖 Chronological Guide",
    "## 🌟 IMP Points",
    "## ❓ MCQs",
    "## ✍️ Written Questions",
    "## 🩺 Clinical Cases",
)
QUESTION_OPTION_KEYS = ("a", "b", "c", "d")
EDITORIAL_REVIEW_MARKERS = (
    "NEEDS_SOURCE_REVIEW",
    "NEEDS_OCR_REVIEW",
    "UNRESOLVED_CONFLICT",
)
BROKEN_OCR_TOKEN_PATTERN = re.compile(r"\b(?:[A-Za-z]{1,3}\s+){4,}[A-Za-z]{1,3}\b")
# Words that lost their spaces in OCR ("resultsofthe", "signsandsymptoms").
#
# The obvious pattern -- a function word with letters either side -- is wrong,
# and wrong in a way that actively corrupts transcripts. It fires on any word
# that merely CONTAINS one of these as a substring, which in medical English is
# most of the vocabulary: radiotherapy, chemotherapy, physiotherapy,
# brachytherapy, immunotherapy, anesthesiology, mesothelioma, synthesis,
# hypothesis, prosthesis, Tomosynthesis. Fifteen of nineteen real terms tested
# were flagged. A transcript of a radiology lecture could not say
# "radiotherapy" without failing validation, and the Agent's only way out was
# to reword correct terminology -- which is exactly what the editorial rules
# forbid. It happened: a past-exam MCQ had "Tomosynthesis" rewritten to "DBT"
# purely to get past this check, changing the verbatim wording of an exam
# question.
#
# MEDICAL_OCR_ALLOWLIST was the previous attempt to contain this. An allowlist
# cannot work here: no finite list covers every English or medical word
# containing "the" or "and".
#
# So the test is now the signature of genuinely lost spaces rather than the
# presence of a substring. Either:
#   (a) two function words run together in one token ("causesofthedisease"), or
#   (b) one function word with a word-length run of letters on BOTH sides
#       ("diagnosisandtreatment") -- six, which every -therapy compound clears
#       on its short side.
# No real word satisfies either. This catches fewer run-togethers than the old
# pattern did, and that is the right trade: a missed OCR artifact is visible to
# the reviewing Agent, while a false positive silently rewrites medicine.
_OCR_FUNCTION_WORDS = r"(?:of|the|and|are|from|with|except)"
JOINED_COMMON_WORD_PATTERN = re.compile(
    rf"\b[A-Za-z]{{3,}}{_OCR_FUNCTION_WORDS}[A-Za-z]*{_OCR_FUNCTION_WORDS}[A-Za-z]{{2,}}\b"
    rf"|\b[A-Za-z]{{6,}}{_OCR_FUNCTION_WORDS}[A-Za-z]{{6,}}\b",
    flags=re.IGNORECASE,
)
MEDICAL_OCR_ALLOWLIST = frozenset({
    # Words the joined-word heuristic still flags and should not.
    #
    # This list used to hold 28 entries -- catheter, erythema, quadrant,
    # radiotherapy, chemotherapy, anesthesia, hypothalamus, noradrenaline and
    # twenty more. None of them were exceptions. They were all symptomatic
    # patches for a pattern that flagged any word merely containing "the" or
    # "and", added one at a time as each was hit in a real run. Fixing the
    # pattern made 27 of the 28 unnecessary.
    #
    # Keep this list tiny. If a whole family of words needs adding, the pattern
    # has regressed -- fix that instead. tests/test_ocr_heuristics.py asserts
    # the -therapy family never comes back here.
    "notwithstanding",
})
NOTEBOOK_CITATION_PATTERN = re.compile(
    r"\[\s*\d+(?:\s*[,،、;\-–—]\s*\d+)*\s*\]"
)


def _body_heading_errors(text: str) -> list[str]:
    if re.search(r"^#{1,2}\s", text, flags=re.MULTILINE):
        return ["section body contains a forbidden # or ## heading"]
    return []


def _callout_errors(text: str, phase_allowed: set[str] | None = None) -> list[str]:
    allowed = phase_allowed or ALLOWED_CALLOUTS
    found = re.findall(r"^> \[!([^\]]+)\]", text, flags=re.MULTILINE)
    invalid = sorted({callout for callout in found if callout not in allowed})
    return [f"unsupported callout(s): {', '.join(invalid)}"] if invalid else []


BADGE_LIKE_PATTERN = re.compile(
    r"\*{0,2}\[(?:IMP|Question Bank|Past Exams[^\]]*|Past year from doctor[^\]]*)\]\*{0,2}",
    flags=re.IGNORECASE,
)


def _has_combined_imp_badge(text: str) -> bool:
    return any("/ IMP]" in badge for badge in BADGE_LIKE_PATTERN.findall(text))


def _badge_is_valid(badge: str, verified_years: set[int]) -> bool:
    if badge in {"**[IMP]**", "**[Question Bank]**"}:
        return True
    year_badge = re.fullmatch(
        r"\*\*\[Past Exams - ((?:20\d{2})(?:, 20\d{2})*)\]\*\*", badge
    )
    if year_badge:
        years = {int(year) for year in year_badge.group(1).split(", ")}
        ordered = [int(year) for year in year_badge.group(1).split(", ")]
        return (
            bool(years)
            and ordered == sorted(set(ordered))
            and all(is_reasonable_exam_year(year) for year in years)
            and years.issubset(verified_years)
        )
    combined = re.fullmatch(
        r"\*\*\[Past Exams \((20\d{2}(?:, 20\d{2})*)\) / IMP\]\*\*", badge
    )
    if not combined:
        return False
    ordered = [int(year) for year in combined.group(1).split(", ")]
    years = set(ordered)
    return (
        ordered == sorted(set(ordered))
        and all(is_reasonable_exam_year(year) for year in years)
        and years.issubset(verified_years)
    )


def _index_verified_years(evidence: QuestionEvidence | None) -> set[int]:
    """Every year the module's exam index records, across all its questions.

    The file-level year map is built from the manifest and describes files. A
    badge is about a question, and for a compiled bank the two disagree: the
    index is what actually read the papers.
    """
    index = getattr(evidence, "exam_index", None) if evidence else None
    if not index:
        return set()
    return {
        year
        for question in index.get("questions", {}).values()
        for year in question.get("years", ())
    }


def _badge_errors(
    text: str,
    verified_years: set[int],
    evidence: QuestionEvidence | None = None,
) -> list[str]:
    verified_years = verified_years | _index_verified_years(evidence)
    invalid = [
        match.group(0)
        for match in BADGE_LIKE_PATTERN.finditer(text)
        if not _badge_is_valid(match.group(0), verified_years)
    ]
    if not invalid:
        return []
    return [f"invalid or unverified badge(s): {', '.join(sorted(set(invalid)))}"]


def _source_name_matches(left: str, right: str) -> bool:
    left_key = normalize_source_key(left)
    right_key = normalize_source_key(right)
    return (
        normalize_source_stem(left) == normalize_source_stem(right)
        or right_key in left_key
        or left_key in right_key
    )


def _citations_include(query_result: QueryResult, expected_names: list[str]) -> bool:
    return any(
        _source_name_matches(actual, expected)
        for actual in query_result.source_names
        for expected in expected_names
    )


def validate_guide(
    query_result: QueryResult, recording_sources: tuple[str, ...]
) -> list[str]:
    errors = _body_heading_errors(query_result.answer)
    errors += _callout_errors(
        query_result.answer, {"NOTE", "IMPORTANT", "WARNING", "CAUTION"}
    )
    if len(query_result.answer) < 300:
        errors.append("chronological guide is not substantive")
    if not _citations_include(query_result, list(recording_sources)):
        errors.append("citations do not include the recording authority")
    return errors


def validate_imp(query_result: QueryResult) -> list[str]:
    errors = _body_heading_errors(query_result.answer)
    errors += _callout_errors(query_result.answer)
    headings = re.findall(r"^#{3,6} .+$", query_result.answer, flags=re.MULTILINE)
    if tuple(headings) != IMP_HEADINGS:
        errors.append("IMP section does not contain exactly the five required headings")
    if "> [!WARNING]" not in query_result.answer:
        errors.append("Diagnostic Traps lacks a WARNING callout")
    if "> [!CAUTION]" not in query_result.answer:
        errors.append("Lethal Mistakes lacks a CAUTION callout")
    return errors


def _section_blocks(answer: str, heading_prefix: str) -> list[str]:
    if heading_prefix in ("Clinical Case", "Case"):
        pattern = r"(?ms)^(?:[ \t]*>[ \t]*)?(?:### (?:Clinical )?Case\s+\d+|>\s*\*\*🩺 Clinical Case \d+:?\*\*).*?(?=(?:^(?:[ \t]*>[ \t]*)?### (?:Clinical )?Case\s+\d+|^>\s*\*\*🩺 Clinical Case \d+:?\*\*|\Z))"
        blocks = re.findall(pattern, answer)
        if blocks:
            return blocks
        if "> [!TIP]" in answer:
            return [b.strip() for b in answer.split("> [!TIP]") if b.strip()]
        pattern = rf"(?ms)^(?:[ \t]*>[ \t]*)?### {re.escape(heading_prefix)}\s+\d+.*?(?=(?:^(?:[ \t]*>[ \t]*)?### |\Z))"
        return re.findall(pattern, answer)
    pattern = rf"(?ms)^(?:[ \t]*>[ \t]*)?### {re.escape(heading_prefix)}\s+\d+.*?(?=(?:^(?:[ \t]*>[ \t]*)?### |\Z))"
    return re.findall(pattern, answer)


def _source_field_matches(block: str, evidence_sources: list[str]) -> bool:
    source_fields = _source_fields(block)
    return bool(source_fields) and any(
        _source_name_matches(source_field, evidence_source)
        for source_field in source_fields
        for evidence_source in evidence_sources
    )


def _clean_source_field_item(item: str) -> str:
    cleaned = item.strip().strip("'\"`")
    cleaned = re.sub(r"\s*\(\d{4}\)$", "", cleaned).strip().strip("'\"`")
    return cleaned


def _source_fields(block: str) -> list[str]:
    raw_lines = re.findall(r"^(?:[ \t]*>[ \t]*)?\*\*Source:\*\*\s*(.+)$", block, re.MULTILINE)
    items: list[str] = []
    for line in raw_lines:
        parts = re.split(r"\s+and\s+|\s*,\s*", line)
        for part in parts:
            cleaned = _clean_source_field_item(part)
            if cleaned:
                items.append(cleaned)
    return items


def _catalog_matches(
    source_name: str, evidence_catalog: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    if not evidence_catalog:
        return []
    normalized = normalize_source_key(source_name)
    exact = [
        entry
        for entry in evidence_catalog
        if normalized
        and (
            normalized == str(entry.get("normalized_name", ""))
            or any(
                normalized == normalize_source_key(str(alias))
                for alias in entry.get("aliases", [])
            )
        )
    ]
    if exact:
        return exact
    stem = normalize_source_stem(source_name)
    return [
        entry
        for entry in evidence_catalog
        if stem
        and any(
            stem == normalize_source_stem(str(alias))
            for alias in entry.get("aliases", [])
        )
    ]


def _source_evidence(
    source_name: str,
    year_map: dict[int, list[str]],
    evidence_sources: list[str],
    evidence_catalog: list[dict[str, Any]] | None = None,
) -> tuple[set[int], set[str], list[dict[str, Any]]]:
    matches = [
        entry
        for entry in _catalog_matches(source_name, evidence_catalog)
        if _catalog_entry_is_available(entry)
    ]
    if matches:
        years = {
            int(year)
            for entry in matches
            for year in entry.get("verified_years", [])
            if isinstance(year, int) or str(year).isdigit()
        }
        roles = {str(entry.get("role", "")) for entry in matches if entry.get("role")}
        return years, roles, matches
    matched_names = [
        expected
        for expected in evidence_sources
        if _source_name_matches(source_name, expected)
    ]
    years = {
        year
        for year, source_names in year_map.items()
        if any(
            _source_name_matches(source_name, source_name_entry)
            for source_name_entry in source_names
        )
    }
    return years, set(), [{"canonical_name": name} for name in matched_names]


def _question_number(block: str, heading_prefix: str) -> str:
    match = re.search(
        rf"^### {re.escape(heading_prefix)}\s+(\d+)", block, re.MULTILINE
    )
    return match.group(1) if match else "?"


def _ungrounded_block_errors(
    answer: str,
    heading_prefix: str,
    evidence: QuestionEvidence,
    expected_count: int,
) -> list[str]:
    blocks = _section_blocks(answer, heading_prefix)
    if len(blocks) == expected_count and all(
        "**[IMP]**" in block
        or _indexed(block, evidence)
        or _source_field_matches(block, evidence.evidence_sources)
        or any(
            _catalog_entry_is_available(entry)
            for source_field in _source_fields(block)
            for entry in _catalog_matches(source_field, evidence.evidence_catalog)
        )
        for block in blocks
    ):
        return []
    return [
        f"{heading_prefix} [missing_source]: one or more blocks lacks a verified source field"
    ]


def _badge_years(block: str) -> set[int]:
    years: set[int] = set()
    for badge in BADGE_LIKE_PATTERN.findall(block):
        if badge.startswith("**[Past Exams"):
            years.update(int(year) for year in re.findall(r"20\d{2}", badge))
    return years


def _source_field_years(block: str, year_map: dict[int, list[str]]) -> set[int]:
    source_fields = _source_fields(block)
    return {
        year
        for year, source_names in year_map.items()
        if any(
            _source_name_matches(source_field, source_name)
            for source_field in source_fields
            for source_name in source_names
        )
    }


def _block_year_errors(
    answer: str,
    heading_prefix: str,
    year_map: dict[int, list[str]],
    evidence_catalog: list[dict[str, Any]] | None = None,
    evidence: QuestionEvidence | None = None,
) -> list[str]:
    errors: list[str] = []
    index = getattr(evidence, "exam_index", None) if evidence else None
    for block in _section_blocks(answer, heading_prefix):
        number = _question_number(block, heading_prefix)
        claimed_years = _badge_years(block)
        if index:
            stem = _question_content(block)
            known = index_years(stem, index) if stem else None
            if known is not None:
                # The index read the papers question by question; a file-level
                # year map cannot second-guess it.
                unbacked = sorted(claimed_years - set(known))
                if unbacked:
                    errors.append(
                        f"{heading_prefix} {number} [source_year_mismatch]: claimed "
                        f"years {sorted(claimed_years)}; the exam index records "
                        f"{sorted(known)}"
                    )
                continue
        source_years: set[int] = set()
        for source_field in _source_fields(block):
            years, _roles, _matches = _source_evidence(
                source_field, year_map, [], evidence_catalog
            )
            source_years.update(years)
        if claimed_years - source_years:
            errors.append(
                f"{heading_prefix} {number} [source_year_mismatch]: claimed years "
                f"{sorted(claimed_years)}; evidenced years {sorted(source_years)}"
            )
        if source_years - claimed_years and claimed_years:
            errors.append(
                f"{heading_prefix} {number} [missing_supported_year]: source evidence "
                f"contains years {sorted(source_years)} not present in the badge"
            )
    return errors


def _source_field_errors(
    source_fields: list[str],
    heading_prefix: str,
    number: str,
    evidence: QuestionEvidence,
) -> tuple[list[str], set[int], set[str]]:
    errors: list[str] = []
    evidenced_years: set[int] = set()
    roles: set[str] = set()
    for source_field in source_fields:
        matches = [
            entry
            for entry in _catalog_matches(source_field, evidence.evidence_catalog)
            if _catalog_entry_is_available(entry)
        ]
        fallback_matches = [
            expected
            for expected in evidence.evidence_sources
            if _source_name_matches(source_field, expected)
        ]
        if not matches and not fallback_matches:
            errors.append(
                f"{heading_prefix} {number} [unknown_source]: supplied source "
                f"{source_field}"
            )
        if len(matches) > 1:
            errors.append(
                f"{heading_prefix} {number} [ambiguous_source]: supplied source "
                f"{source_field} matches {len(matches)} catalog entries"
            )
        source_years, source_roles, _ = _source_evidence(
            source_field,
            evidence.year_map,
            evidence.evidence_sources,
            evidence.evidence_catalog,
        )
        evidenced_years.update(source_years)
        roles.update(source_roles)
    return errors, evidenced_years, roles


def _question_year_provenance_errors(
    context: QuestionProvenanceContext,
    evidenced_years: set[int],
) -> list[str]:
    errors: list[str] = []
    claimed_years = _badge_years(context.block)
    verified_years = set(context.evidence.year_map)
    if claimed_years - verified_years:
        errors.append(
            f"{context.heading_prefix} {context.number} [unverified_badge_year]: claimed years "
            f"{sorted(claimed_years)} are not in the verified manifest"
        )
    if claimed_years - evidenced_years:
        errors.append(
            f"{context.heading_prefix} {context.number} [source_year_mismatch]: claimed years "
            f"{sorted(claimed_years)}; evidenced years {sorted(evidenced_years)}"
        )
    if evidenced_years - claimed_years and claimed_years:
        errors.append(
            f"{context.heading_prefix} {context.number} [missing_supported_year]: source evidence "
            f"contains years {sorted(evidenced_years)} not present in the badge"
        )
    return errors


def _question_role_provenance_errors(
    context: QuestionProvenanceContext,
    roles: set[str],
) -> list[str]:
    errors: list[str] = []
    claimed_years = _badge_years(context.block)
    heading_prefix = context.heading_prefix
    number = context.number
    if "past_exam" in roles and not claimed_years:
        errors.append(
            f"{heading_prefix} {number} [missing_badge]: past-exam source "
            "requires a Past Exams year badge"
        )
    has_question_bank_badge = any("Question Bank" in badge for badge in context.badges)
    if "question_bank" in roles and not has_question_bank_badge:
        errors.append(
            f"{heading_prefix} {number} [source_role_mismatch]: question-bank "
            "source requires a Question Bank badge"
        )
    if has_question_bank_badge and roles and "question_bank" not in roles:
        errors.append(
            f"{heading_prefix} {number} [source_role_mismatch]: Question Bank badge "
            "does not point to a question-bank source"
        )
    if claimed_years and roles and "past_exam" not in roles:
        errors.append(
            f"{heading_prefix} {number} [source_role_mismatch]: Past Exams badge "
            "does not point to a past-exam source"
        )
    return errors


def _indexed_year_errors(
    context: QuestionProvenanceContext,
) -> list[str] | None:
    """Year check against the index, when the index knows this question.

    Returns None when it does not, so the caller falls back to the file-level
    year map. The index is preferred because it answers per question: a file
    map can only say "this file contains 2021 through 2025", which for a
    compiled bank makes every question in it claim every year the bank holds.
    """
    index = getattr(context.evidence, "exam_index", None)
    if not index:
        return None
    stem = _question_content(context.block)
    known = index_years(stem, index) if stem else None
    if known is None:
        return None
    claimed: set[int] = set()
    for badge in context.badges:
        claimed.update(
            int(year) for year in re.findall(r"20\d{2}", badge)
        )
    unbacked = sorted(claimed - set(known))
    if unbacked:
        return [
            f"{context.heading_prefix} {context.number} [source_year_mismatch]: "
            f"claims {sorted(claimed)}; the exam index records {sorted(known)}"
        ]
    return []


def _question_badge_provenance_errors(
    context: QuestionProvenanceContext,
) -> list[str]:
    indexed = _indexed_year_errors(context)
    if indexed is not None:
        # The index settles this question's provenance; the file-level year map
        # can only over- or under-claim it.
        return indexed + _question_role_provenance_errors(context, ())
    field_errors, evidenced_years, roles = _source_field_errors(
        _source_fields(context.block),
        context.heading_prefix,
        context.number,
        context.evidence,
    )
    errors = field_errors + _question_year_provenance_errors(context, evidenced_years)
    errors.extend(
        _question_role_provenance_errors(context, roles)
    )
    return errors


def _question_provenance_errors(
    answer: str,
    heading_prefix: str,
    evidence: QuestionEvidence,
) -> list[str]:
    errors: list[str] = []
    for block in _section_blocks(answer, heading_prefix):
        number = _question_number(block, heading_prefix)
        badges = BADGE_LIKE_PATTERN.findall(block)
        if not badges:
            errors.append(f"{heading_prefix} {number} [missing_badge]: no provenance badge")
        source_fields = _source_fields(block)
        is_imp = "**[IMP]**" in block or any("/ IMP]" in badge for badge in badges)
        sourced_badge = any(
            "Past Exams" in badge or "Question Bank" in badge for badge in badges
        )
        if (not is_imp or sourced_badge) and not source_fields and not _indexed(
            block, evidence
        ):
            errors.append(
                f"{heading_prefix} {number} [missing_source]: sourced question has no Source field"
            )
        if is_imp and not sourced_badge and source_fields:
            errors.append(
                f"{heading_prefix} {number} [source_role_mismatch]: IMP-only question "
                "must not carry exam provenance"
            )
        errors.extend(
            _question_badge_provenance_errors(
                QuestionProvenanceContext(
                    block, heading_prefix, number, evidence, tuple(badges)
                )
            )
        )
    return errors


def _field_content(block: str, field_name: str) -> str:
    pattern = (
        rf"(?ms)^\s*(?:>\s*)?\*\*{re.escape(field_name)}:\*\*\s*"
        rf"(.*?)(?=^\s*(?:>\s*)?\*\*[^*\n]+:\*\*|\Z)"
    )
    match = re.search(pattern, block)
    return match.group(1).strip() if match else ""


def _question_content(block: str) -> str:
    for field_name in ("Question", "Question (verbatim)"):
        content = _field_content(block, field_name)
        if content:
            return content
    return ""


def _options_content(block: str) -> str:
    for field_name in ("Options", "Options (verbatim)"):
        content = _field_content(block, field_name)
        if content:
            return content
    return ""


def _model_answer_content(block: str) -> str:
    for field_name in ("Model Answer", "Model Answer (Short)"):
        content = _field_content(block, field_name)
        if content:
            return content
    return ""


def _explanation_content(block: str) -> str:
    for field_name in (
        "Clinical Explanation",
        "Clinical Explanation (Egyptian Arabic)",
        "Explanation",
    ):
        content = _field_content(block, field_name)
        if content:
            return content
    return ""



def _question_fingerprint_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").translate(ARABIC_DIGITS)
    normalized = re.sub(
        r"^\s*(?:question\s*)?\d+\s*[\).:;-]\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", " ", normalized.casefold()).strip()
    return re.sub(r"[^\w\u0600-\u06ff]+", "", normalized)


def _question_fingerprint(block: str, heading_prefix: str) -> str:
    question = _question_content(block)
    options = ""
    if heading_prefix == "MCQ":
        options = _field_content(block, "Options (verbatim)") or _field_content(
            block, "Options"
        )
        options += _field_content(block, "Correct Answer")
    return _question_fingerprint_text(f"{question}\n{options}")


def _question_stem_fingerprint(block: str) -> str:
    return _question_fingerprint_text(_question_content(block))


def _question_group_key(block: str, heading_prefix: str) -> tuple[str, bool]:
    if heading_prefix in {"Clinical Case", "Case"}:
        case_identity = _question_fingerprint_text(
            "\n".join(
                (
                    _field_content(block, "Scenario"),
                    _field_content(block, "Questions"),
                )
            )
        )
        return case_identity, "**[IMP]**" in block
    return _question_fingerprint(block, heading_prefix), "**[IMP]**" in block


def _badge_line_without_provenance(line: str) -> str:
    return re.sub(
        r"\s+\*{0,2}\[(?:IMP|Question Bank|Past Exams[^\]]*|Past year from doctor[^\]]*)\]\*{0,2}",
        "",
        line,
        flags=re.IGNORECASE,
    ).rstrip()


def _canonical_source_name(
    source_name: str, evidence_catalog: list[dict[str, Any]] | None
) -> str:
    matches = _catalog_matches(source_name, evidence_catalog)
    if len(matches) == 1:
        return str(matches[0].get("canonical_name") or source_name)
    return source_name.strip()


def _merged_badges(
    blocks: list[str],
    year_map: dict[int, list[str]],
    evidence_catalog: list[dict[str, Any]] | None,
) -> list[str]:
    years: set[int] = set()
    roles: set[str] = set()
    for block in blocks:
        years.update(_badge_years(block))
        for source_field in _source_fields(block):
            source_years, source_roles, _ = _source_evidence(
                source_field, year_map, [], evidence_catalog
            )
            years.update(source_years)
            roles.update(source_roles)
    question_bank = any(
        "Question Bank" in badge
        for block in blocks
        for badge in BADGE_LIKE_PATTERN.findall(block)
    ) or "question_bank" in roles
    imp = any(
        "**[IMP]**" in block
        or any("/ IMP]" in badge for badge in BADGE_LIKE_PATTERN.findall(block))
        for block in blocks
    )
    past = bool(years) or "past_exam" in roles
    badges: list[str] = []
    if past and years:
        year_text = ", ".join(str(year) for year in sorted(years))
        badges.append(
            f"**[Past Exams ({year_text}) / IMP]**" if imp else f"**[Past Exams - {year_text}]**"
        )
    if question_bank:
        badges.append("**[Question Bank]**")
    if imp and not past:
        badges.append("**[IMP]**")
    return badges


def _normalize_mcq_block(block: str) -> str:
    lines = block.splitlines()
    cleaned_lines = [
        line[2:] if line.startswith("> ") else line[1:] if line.startswith(">") else line
        for line in lines
    ]
    text = "\n".join(cleaned_lines).strip()
    text = NOTEBOOK_CITATION_PATTERN.sub("", text)
    text = re.sub(r"\*\*Question\s*(?:\(verbatim\))?:\*\*", "**Question:**", text)
    text = re.sub(r"\*\*Options\s*(?:\(verbatim\))?:\*\*", "**Options:**", text)
    text = re.sub(
        r"\*\*Clinical Explanation\s*(?:\(Egyptian Arabic\))?:\*\*",
        "**Clinical Explanation:**",
        text,
    )
    options_match = re.search(
        r"(?ms)^\*\*Options:\*\*[ \t]*(.*?)(?=^\*\*[^*\n]+:\*\*|\Z)", text
    )
    if options_match:
        raw_options = options_match.group(1).strip()
        entries = _option_entries(raw_options)
        if entries:
            formatted_options = "\n".join(f"- **{k}.** {v}" for k, v in entries.items())
            start, end = options_match.span(0)
            text = text[:start] + "**Options:**\n" + formatted_options + "\n\n" + text[end:].lstrip()
    return text.strip()


def _normalize_written_block(block: str) -> str:
    lines = block.splitlines()
    cleaned_lines = [
        line[2:] if line.startswith("> ") else line[1:] if line.startswith(">") else line
        for line in lines
    ]
    text = "\n".join(cleaned_lines).strip()
    text = NOTEBOOK_CITATION_PATTERN.sub("", text)
    text = re.sub(r"\*\*Question\s*(?:\(verbatim\))?:\*\*", "**Question:**", text)
    text = re.sub(r"\*\*Model Answer\s*(?:\(Short\))?:\*\*", "**Model Answer:**", text)
    text = re.sub(
        r"\*\*Clinical Explanation\s*(?:\(Egyptian Arabic\))?:\*\*",
        "**Clinical Explanation:**",
        text,
    )
    return text.strip()


def _normalize_case_block(block: str) -> str:
    lines = block.splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in ("> [!TIP]", "[!TIP]"):
            continue
        if stripped.startswith("> "):
            stripped = stripped[2:]
        elif stripped.startswith(">"):
            stripped = stripped[1:]
        cleaned_lines.append(stripped)
    text = "\n".join(cleaned_lines).strip()
    text = NOTEBOOK_CITATION_PATTERN.sub("", text)
    text = re.sub(
        r"^\*\*🩺 Clinical Case\s+(\d+):\*\*(.*)$",
        r"### Clinical Case \1\2",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^### Case\s+(\d+)(.*)$",
        r"### Clinical Case \1\2",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"\*\*Model Answer\s*(?:\(Short\))?:\*\*", "**Model Answer:**", text)
    text = re.sub(
        r"\*\*Clinical Explanation\s*(?:\(Egyptian Arabic\))?:\*\*",
        "**Clinical Explanation:**",
        text,
    )
    return text.strip()


def _merge_question_blocks(
    blocks: list[str],
    heading_prefix: str,
    year_map: dict[int, list[str]],
    evidence_catalog: list[dict[str, Any]] | None,
) -> str:
    base = blocks[0].strip()
    heading_match = re.search(r"^### .*?$", base, flags=re.MULTILINE)
    if not heading_match:
        return base
    original = base
    heading = _badge_line_without_provenance(heading_match.group(0))
    badges = _merged_badges(blocks, year_map, evidence_catalog)
    base = original[heading_match.end() :]
    source_names = _unique_strings(
        [
            _canonical_source_name(source, evidence_catalog)
            for block in blocks
            for source in _source_fields(block)
        ]
    )
    base = re.sub(
        r"(?m)^[ \t]*(?:> )?\*\*Source:\*\*.*(?:\n|$)",
        "",
        base,
    ).strip()
    source_lines = "\n".join(f"**Source:** {source}" for source in source_names)
    anchors = ("**Correct Answer:**", "**Model Answer:**", "**Model Answer (Short):**")
    if source_lines:
        found_anchor = None
        for a in anchors:
            if a in base:
                found_anchor = a
                break
        if found_anchor:
            base = base.replace(found_anchor, f"{source_lines}\n{found_anchor}", 1)
        else:
            base = f"{base}\n{source_lines}"
    merged_block = f"{heading}{''.join(f' {badge}' for badge in badges)}\n\n{base.strip()}"
    if heading_prefix == "MCQ":
        return _normalize_mcq_block(merged_block)
    elif heading_prefix == "Question":
        return _normalize_written_block(merged_block)
    elif heading_prefix in ("Clinical Case", "Case"):
        return _normalize_case_block(merged_block)
    return merged_block


def deduplicate_question_section(
    answer: str,
    heading_prefix: str,
    year_map: dict[int, list[str]] | None = None,
    evidence_catalog: list[dict[str, Any]] | None = None,
) -> str:
    """Merge exact, semantic, and OCR duplicate question blocks automatically."""
    blocks = _section_blocks(answer, heading_prefix)
    if not blocks:
        return answer
    normalized_blocks = [
        _normalize_mcq_block(block)
        if heading_prefix == "MCQ"
        else _normalize_written_block(block)
        if heading_prefix == "Question"
        else _normalize_case_block(block)
        for block in blocks
    ]
    groups: dict[tuple[str, bool], list[str]] = {}
    order: list[tuple[str, bool]] = []
    for block in normalized_blocks:
        group_key = _question_group_key(block, heading_prefix)
        if group_key not in groups:
            order.append(group_key)
            groups[group_key] = []
        groups[group_key].append(block)

    merged = [
        _merge_question_blocks(
            groups[group_key], heading_prefix, year_map or {}, evidence_catalog
        )
        for group_key in order
    ]
    if heading_prefix in ("Clinical Case", "Case"):
        matches = list(
            re.finditer(
                r"(?ms)^(?:### (?:Clinical )?Case\s+\d+|> \[!TIP\]).*?(?=(?:^### (?:Clinical )?Case\s+\d+|^> \[!TIP\]|^## |\Z))",
                answer,
            )
        )
    else:
        matches = list(
            re.finditer(
                rf"(?ms)^### {re.escape(heading_prefix)}\s+\d+.*?(?=^### |\Z)",
                answer,
            )
        )
    if not matches:
        return answer
    prefix = answer[: matches[0].start()]
    suffix = answer[matches[-1].end() :]
    if prefix and not prefix.endswith("\n\n"):
        prefix = prefix.rstrip() + "\n\n"
    if suffix and not suffix.startswith("\n\n"):
        suffix = "\n\n" + suffix.lstrip()
    return prefix + "\n\n".join(merged) + suffix


def renumber_question_section(answer: str, heading_prefix: str) -> str:
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        line = match.group(0)
        return re.sub(
            rf"^### {re.escape(heading_prefix)}\s+\d+",
            f"### {heading_prefix} {counter}",
            line,
        )

    return re.sub(
        rf"^### {re.escape(heading_prefix)}\s+\d+[^\n]*$",
        replace,
        answer,
        flags=re.MULTILINE,
    )


def normalize_question_result(
    query_result: QueryResult,
    heading_prefix: str,
    year_map: dict[int, list[str]],
    evidence_catalog: list[dict[str, Any]] | None = None,
) -> QueryResult:
    answer = deduplicate_question_section(
        query_result.answer, heading_prefix, year_map, evidence_catalog
    )
    answer = renumber_question_section(answer, heading_prefix)
    return QueryResult(
        answer,
        query_result.source_names,
        query_result.session_id,
        query_result.source_quarantine,
    )


def _duplicate_question_errors(answer: str) -> list[str]:
    errors: list[str] = []
    for heading_prefix in ("MCQ", "Question"):
        seen: dict[tuple[str, bool], str] = {}
        stems: dict[str, list[tuple[str, str, bool]]] = {}
        for block in _section_blocks(answer, heading_prefix):
            fingerprint = _question_group_key(block, heading_prefix)
            number = _question_number(block, heading_prefix)
            stem = _question_stem_fingerprint(block)
            stems.setdefault(stem, []).append(
                (number, fingerprint[0], fingerprint[1])
            )
            if fingerprint in seen:
                errors.append(
                    f"{heading_prefix} {number} [duplicate_question]: matches "
                    f"{heading_prefix} {seen[fingerprint]}"
                )
            else:
                seen[fingerprint] = number
        for stem, occurrences in stems.items():
            if not stem or len(occurrences) < 2:
                continue
            unique_signatures = {(fingerprint, is_imp) for _, fingerprint, is_imp in occurrences}
            if len(unique_signatures) > 1:
                numbers = ", ".join(
                    f"{heading_prefix} {number}" for number, _, _ in occurrences
                )
                errors.append(
                    f"{heading_prefix} {numbers} [unsafe_duplicate_merge]: "
                    "same normalized question stem has different options, answers, "
                    "or provenance; Agent review is required before merging"
                )
    return errors


def _option_keys(options: str) -> list[str]:
    return list(_option_entries(options))


def _clean_option_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^(?:[ \t*>-]|\*\*)+", "", cleaned)
    cleaned = re.sub(r"(?:\s*\*\*)+$", "", cleaned)
    return cleaned.strip()


def _option_entries(options: str) -> dict[str, str]:
    cleaned_options = re.sub(r"(?m)^[ \t]*>[ \t]?", "", options)
    markers = list(
        re.finditer(
            r"(?<![A-Za-z0-9])(?:[-*]\s*)?(?:\*\*)?([a-dA-D])(?:\*\*)?\s*[\.)]\s*(?:\*\*)?",
            cleaned_options,
        )
    )
    return {
        marker.group(1).casefold(): _clean_option_text(
            cleaned_options[marker.end() : next_start]
        )
        for marker, next_start in zip(
            markers, [*(_match.start() for _match in markers[1:]), len(cleaned_options)]
        )
    }


def _expected_option_keys(profile: dict[str, Any]) -> tuple[str, ...]:
    options = profile.get("mcq", {}).get("options", {})
    count = options.get("count", len(QUESTION_OPTION_KEYS))
    if not isinstance(count, int) or not 2 <= count <= 8:
        count = len(QUESTION_OPTION_KEYS)
    return tuple(chr(ord("a") + index) for index in range(count))


def _ocr_quality_errors(text: str, field_name: str) -> list[str]:
    errors: list[str] = []
    if NOTEBOOK_CITATION_PATTERN.search(text):
        errors.append(f"{field_name} contains NotebookLM citation residue")
    if BROKEN_OCR_TOKEN_PATTERN.search(text):
        errors.append(f"{field_name} contains broken OCR word spacing")
    for match in JOINED_COMMON_WORD_PATTERN.finditer(text):
        token = match.group(0).casefold()
        if token not in MEDICAL_OCR_ALLOWLIST:
            errors.append(f"{field_name} contains joined OCR words")
            break
    return errors


def _option_shape_errors(
    block: str, block_number: int, profile: dict[str, Any]
) -> list[str]:
    is_imp = "**[IMP]**" in block
    errors: list[str] = []
    if is_imp and "**Options (verbatim):**" in block:
        errors.append(f"MCQ {block_number} uses the wrong options field for its badge")
    options = _options_content(block)
    if not options:
        return [f"MCQ {block_number} [missing_field]: missing **Options:**"]
    keys = _option_keys(options)
    expected_keys = _expected_option_keys(profile)
    if keys != list(expected_keys):
        errors.append(
            f"MCQ {block_number} options must be separate {', '.join(expected_keys)} entries"
        )
    errors += _ocr_quality_errors(options, f"MCQ {block_number} options")
    return errors


def _correct_answer_errors(
    block: str, block_number: int, options: str
) -> list[str]:
    answer = _field_content(block, "Correct Answer")
    answer = re.sub(r"(?m)^[ \t]*>[ \t]?", "", answer).strip()
    # The trailing (?:\*\*)? matters: "**d.** text" closes its bold *after* the
    # period, so without it the "**" stays glued to the answer text and every
    # correctly-written block reads as disagreeing with its own option.
    # _option_entries has always consumed it; this is the same marker.
    match = re.match(r"(?:[-*]\s*)?(?:\*\*)?([a-dA-D])(?:\*\*)?\s*[\.)]\s*(?:\*\*)?", answer)
    if not match:
        return [f"MCQ {block_number} Correct Answer must start with an option label"]
    option_entries = _option_entries(options)
    answer_key = match.group(1).casefold()
    if answer_key not in option_entries:
        return [f"MCQ {block_number} Correct Answer points to a missing option"]
    answer_text = re.sub(r"\s+", " ", answer[match.end() :].strip()).casefold()
    expected_text = re.sub(r"\s+", " ", option_entries[answer_key]).casefold()
    if answer_text and expected_text and not (
        answer_text == expected_text
        or answer_text.startswith(expected_text)
        or expected_text.startswith(answer_text)
    ):
        return [f"MCQ {block_number} Correct Answer text differs from its option"]
    return []


def _imp_style_errors(
    question: str, block_number: int, profile: dict[str, Any]
) -> list[str]:
    mcq_profile = profile.get("mcq", {})
    register = str(mcq_profile.get("register", "")).casefold()
    max_words = mcq_profile.get("max_stem_words")
    word_count = len(re.findall(r"[A-Za-z0-9]+", question))
    errors: list[str] = []
    if isinstance(max_words, int) and word_count > max_words:
        errors.append(f"MCQ {block_number} IMP stem exceeds the observed exam length")
    if "short direct" in register and word_count > 20:
        errors.append(f"MCQ {block_number} IMP stem is too long for the observed exam style")
    clinical_markers = (
        "patient",
        "brought to",
        "emergency department",
        "on examination",
        "scenario",
    )
    if "short direct" in register and any(marker in question.casefold() for marker in clinical_markers):
        errors.append(f"MCQ {block_number} IMP stem uses an unobserved clinical-vignette style")
    return errors


def _mcq_editorial_errors(
    answer: str, profile: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    for block_number, block in enumerate(_section_blocks(answer, "MCQ"), start=1):
        question = _question_content(block)
        if not question:
            errors.append(f"MCQ {block_number} is missing its question field")
            continue
        errors += _ocr_quality_errors(question, f"MCQ {block_number} question")
        errors += _option_shape_errors(block, block_number, profile)
        options = _options_content(block)
        errors += _correct_answer_errors(block, block_number, options)
        errors += _ocr_quality_errors(
            _field_content(block, "Correct Answer"),
            f"MCQ {block_number} correct answer",
        )
        if "**[IMP]**" in block:
            errors += _imp_style_errors(question, block_number, profile)
    return errors


def _written_editorial_errors(answer: str) -> list[str]:
    errors: list[str] = []
    for block_number, block in enumerate(_section_blocks(answer, "Question"), start=1):
        question = _question_content(block)
        if question:
            errors += _ocr_quality_errors(question, f"Written Question {block_number}")
    return errors


def validate_editorial_quality(
    draft: str, exam_style_profile: dict[str, Any] | None = None
) -> list[str]:
    errors: list[str] = []
    for marker in EDITORIAL_REVIEW_MARKERS:
        if marker in draft:
            errors.append(f"draft contains unresolved editorial marker: {marker}")
    errors += _mcq_editorial_errors(draft, exam_style_profile or {})
    errors += _written_editorial_errors(draft)
    errors += _duplicate_question_errors(draft)
    return errors


def _indexed(block: str, evidence: QuestionEvidence) -> bool:
    """True when the exam index already records this question's provenance.

    A `**Source:**` line exists to make a badge checkable. Once the module has
    an index, the index is where that check happens -- per question, naming the
    paper *and* the section -- and repeating a filename in every block only
    puts a machine's bookkeeping in front of the student reading it.
    """
    index = getattr(evidence, "exam_index", None)
    if not index:
        return False
    stem = _question_content(block)
    return bool(stem) and index_years(stem, index) is not None


def _mcq_field_errors(answer: str, evidence: QuestionEvidence) -> list[str]:
    errors: list[str] = []
    for block in _section_blocks(answer, "MCQ"):
        number = _question_number(block, "MCQ")
        has_question = bool(_question_content(block))
        has_options = bool(_options_content(block))
        has_correct = "**Correct Answer:**" in block
        has_explanation = bool(_explanation_content(block))
        if not has_question:
            errors.append(f"MCQ {number} [missing_field]: missing **Question:**")
        if not has_options:
            errors.append(f"MCQ {number} [missing_field]: missing **Options:**")
        if not has_correct:
            errors.append(f"MCQ {number} [missing_field]: missing **Correct Answer:**")
        if not has_explanation:
            errors.append(f"MCQ {number} [missing_field]: missing **Clinical Explanation:**")
        if "**[IMP]**" not in block and "**Source:**" not in block:
            if not _indexed(block, evidence):
                errors.append(f"MCQ {number} [missing_source]: missing **Source:**")
    return errors


def _written_field_errors(answer: str, evidence: QuestionEvidence) -> list[str]:
    errors: list[str] = []
    for block in _section_blocks(answer, "Question"):
        number = _question_number(block, "Question")
        has_question = bool(_question_content(block))
        has_model_answer = bool(_model_answer_content(block))
        if not has_question:
            errors.append(
                f"Question {number} [missing_field]: missing **Question:**"
            )
        if not has_model_answer:
            errors.append(
                f"Question {number} [missing_field]: missing **Model Answer:**"
            )
        if "**[IMP]**" not in block and "**Source:**" not in block:
            if not _indexed(block, evidence):
                errors.append(
                    f"Question {number} [missing_source]: missing **Source:**"
                )
    return errors


def _question_badge_errors(
    answer: str, heading_prefix: str, verified_years: set[int]
) -> list[str]:
    errors: list[str] = []
    for block in _section_blocks(answer, heading_prefix):
        number = _question_number(block, heading_prefix)
        for badge in BADGE_LIKE_PATTERN.findall(block):
            if not _badge_is_valid(badge, verified_years):
                errors.append(
                    f"{heading_prefix} {number} [invalid_badge_format]: {badge}"
                )
    return errors


def validate_mcqs(
    query_result: QueryResult,
    evidence: QuestionEvidence,
) -> list[str]:
    if is_empty_sentinel(query_result.answer, NO_MCQS):
        return []
    answer = query_result.answer
    errors = _body_heading_errors(answer)
    errors += _callout_errors(answer)
    errors += _badge_errors(answer, set(evidence.year_map), evidence)
    errors += _question_badge_errors(
        answer, "MCQ", set(evidence.year_map) | _index_verified_years(evidence)
    )
    question_count = len(_section_blocks(answer, "MCQ"))
    if question_count < 1:
        errors.append("MCQ response has no question blocks")
    errors += _mcq_field_errors(answer, evidence)
    if len(re.findall(r"[\u0600-\u06ff]", answer)) < 20:
        errors.append("MCQ clinical explanations are not in Egyptian Arabic")
    if len(BADGE_LIKE_PATTERN.findall(answer)) < question_count:
        errors.append("one or more MCQs lacks a canonical badge")
    errors += _ungrounded_block_errors(
        answer, "MCQ", evidence, question_count
    )
    errors += _block_year_errors(
        answer, "MCQ", evidence.year_map, evidence.evidence_catalog, evidence
    )
    errors += _question_provenance_errors(
        answer,
        "MCQ",
        evidence,
    )
    errors += validate_editorial_quality(answer, evidence.exam_style_profile)
    if "**[IMP]**" not in answer and not _citations_include(
        query_result, evidence.evidence_sources
    ):
        errors.append("MCQ citations do not include an exam/question-bank source")
    errors += _combined_badge_recording_errors(
        answer, "MCQ", query_result, evidence
    )
    return errors


def _block_names_the_recording(
    block: str, evidence: QuestionEvidence
) -> bool:
    """True when the block cites the lecture recording in a **Source:** field.

    The field is not taken on trust: it has to resolve to an available catalog
    entry whose role is ``recording``, or to one of this run's recording
    sources.  Before the catalog was rebuilt after authority resolution the
    recording was never available, which made the combined
    ``[Past Exams (YYYY) / IMP]`` badge impossible to satisfy either way.
    """
    recording_names = [name for name in evidence.recording_sources if name]
    for source_field in _source_fields(block):
        if any(
            _source_name_matches(source_field, expected)
            for expected in recording_names
        ):
            return True
        if any(
            entry.get("role") == "recording"
            for entry in _catalog_matches(source_field, evidence.evidence_catalog)
            if _catalog_entry_is_available(entry)
        ):
            return True
    return False


def _combined_badge_recording_errors(
    answer: str,
    heading_prefix: str,
    query_result: QueryResult,
    evidence: QuestionEvidence,
) -> list[str]:
    """A combined Past Exams/IMP badge claims the doctor stressed the item.

    That claim needs recording evidence: either NotebookLM cited the recording
    for the whole answer, or the individual block names it as a source.
    """
    if not _has_combined_imp_badge(answer):
        return []
    if _citations_include(query_result, list(evidence.recording_sources)):
        return []
    errors: list[str] = []
    for block in _section_blocks(answer, heading_prefix):
        if not _has_combined_imp_badge(block):
            continue
        if _block_names_the_recording(block, evidence):
            continue
        number = _question_number(block, heading_prefix)
        errors.append(
            f"{heading_prefix} {number} [missing_recording_evidence]: the combined "
            "Past Exams/IMP badge needs the recording cited or named in **Source:**"
        )
    return errors


def _long_model_answer_errors(answer: str, base_characters: int = 2_000) -> list[str]:
    errors: list[str] = []
    for block in _section_blocks(answer, "Question"):
        number = _question_number(block, "Question")
        question_text = _question_content(block)
        sub_parts = len(re.findall(r"(?:^|\n)\s*(?:\d+[\.)]|[a-e][\.)])", question_text))
        max_chars = base_characters + (max(0, sub_parts - 1) * 1_000)
        model_answer = _model_answer_content(block)
        if len(model_answer.strip()) > max_chars:
            errors.append(
                f"Question {number} [model_answer_too_long]: model answer is not concise"
            )
    return errors


def validate_written(
    query_result: QueryResult,
    evidence: QuestionEvidence,
) -> list[str]:
    if is_empty_sentinel(query_result.answer, NO_WRITTEN):
        return []
    answer = query_result.answer
    errors = _body_heading_errors(answer)
    errors += _callout_errors(answer)
    errors += _badge_errors(answer, set(evidence.year_map), evidence)
    errors += _question_badge_errors(
        answer, "Question", set(evidence.year_map) | _index_verified_years(evidence)
    )
    question_count = len(_section_blocks(answer, "Question"))
    if question_count < 1:
        errors.append("written response has no question blocks")
    errors += _written_field_errors(answer, evidence)
    if len(BADGE_LIKE_PATTERN.findall(answer)) < question_count:
        errors.append("one or more written questions lacks a canonical badge")
    errors += _ungrounded_block_errors(
        answer, "Question", evidence, question_count
    )
    errors += _block_year_errors(
        answer, "Question", evidence.year_map, evidence.evidence_catalog, evidence
    )
    errors += _question_provenance_errors(
        answer,
        "Question",
        evidence,
    )
    errors += _long_model_answer_errors(answer, 2_000)
    errors += _written_editorial_errors(answer)
    errors += _combined_badge_recording_errors(
        answer, "Question", query_result, evidence
    )
    if "**[IMP]**" not in answer and not _citations_include(
        query_result, evidence.evidence_sources
    ):
        errors.append("written citations do not include an exam/question-bank source")
    return errors


def _unquoted_case_line(answer: str) -> bool:
    in_case = False
    for line in answer.splitlines():
        if line == "> [!TIP]":
            in_case = True
        elif in_case and line.strip() == "---":
            in_case = False
        elif in_case and line.strip() and not line.startswith(">"):
            return True
    return False


def _has_sourced_badge(case_block: str) -> bool:
    return "[Past Exams" in case_block or "[Question Bank]" in case_block


def _has_imp_badge(case_block: str) -> bool:
    return "**[IMP]**" in case_block or bool(
        re.search(r"\*\*\[Past Exams \(20\d{2}(?:, 20\d{2})*\) / IMP\]\*\*", case_block)
    )


def _case_blocks(answer: str) -> list[str]:
    standard_blocks = _section_blocks(answer, "Clinical Case")
    if not standard_blocks:
        standard_blocks = _section_blocks(answer, "Case")
    if standard_blocks:
        return standard_blocks
    if "> [!TIP]" in answer:
        return [b.strip() for b in answer.split("> [!TIP]") if b.strip()]
    return []


def _case_block_evidence_errors(
    case_block: str, query_result: QueryResult, evidence: CaseEvidence
) -> list[str]:
    errors: list[str] = []
    if _has_sourced_badge(case_block) and not _source_field_matches(
        case_block, evidence.evidence_sources
    ):
        errors.append("a sourced clinical case lacks a verified source field")
    if not _badge_years(case_block).issubset(
        _source_field_years(case_block, evidence.year_map)
    ):
        errors.append("a clinical-case year badge lacks matching source-year evidence")
    if _has_imp_badge(case_block) and not _citations_include(
        query_result, list(evidence.recording_sources)
    ):
        errors.append("an IMP clinical case does not cite the recording authority")
    return errors


def _case_source_errors(
    query_result: QueryResult, evidence: CaseEvidence
) -> list[str]:
    case_blocks = _case_blocks(query_result.answer)
    errors = [
        error
        for case_block in case_blocks
        for error in _case_block_evidence_errors(case_block, query_result, evidence)
    ]
    if any(map(_has_sourced_badge, case_blocks)) and not _citations_include(
        query_result, evidence.evidence_sources
    ):
        errors.append("sourced clinical cases lack exam/question-bank citations")
    return errors


def _case_field_errors(answer: str, case_count: int) -> list[str]:
    case_blocks = _case_blocks(answer)
    errors: list[str] = []
    for idx, block in enumerate(case_blocks, start=1):
        has_scenario = "**Scenario:**" in block or "> **Scenario:**" in block
        has_questions = "**Questions:**" in block or "> **Questions:**" in block
        has_model_answer = (
            "**Model Answer:**" in block
            or "**Model Answer (Short):**" in block
            or "> **Model Answer:**" in block
            or "> **Model Answer (Short):**" in block
        )
        if not has_scenario:
            errors.append(f"clinical-case response is missing **Scenario:** in Case {idx}")
        if not has_questions:
            errors.append(f"clinical-case response is missing **Questions:** in Case {idx}")
        if not has_model_answer:
            errors.append(f"clinical-case response is missing **Model Answer:** in Case {idx}")
    return errors


def _long_case_answer_errors(answer: str, base_characters: int = 2_500) -> list[str]:
    for case_block in _case_blocks(answer):
        questions_text = _field_content(case_block, "Questions")
        sub_parts = len(re.findall(r"(?:^|\n)\s*(?:\d+[\.)]|[a-e][\.)])", questions_text))
        max_chars = base_characters + (max(0, sub_parts - 1) * 1_000)
        model_answer = _model_answer_content(case_block)
        if not model_answer:
            if "**Model Answer:**" in case_block:
                model_answer = case_block.partition("**Model Answer:**")[2]
            elif "**Model Answer (Short):**" in case_block:
                model_answer = case_block.partition("**Model Answer (Short):**")[2]
            elif "> **Model Answer:**" in case_block:
                model_answer = case_block.partition("> **Model Answer:**")[2]
            elif "> **Model Answer (Short):**" in case_block:
                model_answer = case_block.partition("> **Model Answer (Short):**")[2]
        if len(model_answer.strip()) > max_chars:
            return ["one or more clinical-case answers is not concise"]
    return []


def validate_cases(
    query_result: QueryResult, evidence: CaseEvidence
) -> list[str]:
    answer = query_result.answer
    errors = _body_heading_errors(answer)
    errors += _callout_errors(answer, {"TIP", "NOTE", "IMPORTANT", "WARNING", "CAUTION"})
    errors += _badge_errors(answer, set(evidence.year_map))
    errors += _case_source_errors(query_result, evidence)
    case_blocks = _case_blocks(answer)
    case_count = len(case_blocks)
    if case_count < 2:
        errors.append("clinical-case response must contain at least two clinical cases")
    errors += _case_field_errors(answer, case_count)
    if "> [!TIP]" in answer and _unquoted_case_line(answer):
        errors.append("a clinical-case line is outside its TIP quote block")
    return errors + _long_case_answer_errors(answer)




def clean_notebooklm_phrases(text: str) -> str:
    patterns = (
        r"^\[AI-GENERATED[^\n]*\]\s*",
        r"^Thoughts\s*$",
        r"Internal request marker: USTE-[0-9a-f]+[^\n]*",
        r"Studio Panel",
        r"Audio Overview",
        r"(?m)^\s*(?:[👁️📊🎧🔍💡📝]+|\*+)?\s*(?:أنا جاهز|تحب نعمل|حابب نجهز|تحب أعمل|Would you like|Do you want|Let me know if|Feel free to ask)[^\n]*$",
        r"(?m)^---\s*\n+\s*(?:[👁️📊🎧🔍💡📝]+|\*+)?\s*(?:أنا جاهز|تحب نعمل|حابب نجهز|تحب أعمل|Would you like|Do you want|Let me know if|Feel free to ask)[^\n]*$",
    )
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    cleaned = NOTEBOOK_CITATION_PATTERN.sub("", cleaned)
    cleaned = (
        cleaned.replace(" .", ".")
        .replace(" :", ":")
        .replace(" ,", ",")
        .replace(" ;", ";")
    )
    return format_markdown_tables(cleaned)
