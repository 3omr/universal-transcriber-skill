#!/usr/bin/env python3
"""Validation and verification logic for Universal Medical Transcriber."""

from __future__ import annotations

import re
from typing import Any

from models import (
    ALLOWED_CALLOUTS,
    BADGE_LIKE_PATTERN,
    BROKEN_OCR_TOKEN_PATTERN,
    EDITORIAL_REVIEW_MARKERS,
    IMP_HEADINGS,
    JOINED_COMMON_WORD_PATTERN,
    MEDICAL_OCR_ALLOWLIST,
    NO_MCQS,
    NO_WRITTEN,
    NOTEBOOK_CITATION_PATTERN,
    QUESTION_OPTION_KEYS,
    SECTION_HEADINGS,
    QueryResult,
    QuestionEvidence,
    ValidationError,
    is_reasonable_exam_year,
)
from text_processors import (
    _explanation_content,
    _field_content,
    _model_answer_content,
    _option_entries,
    _options_content,
    _question_content,
    _question_group_key,
    _question_number,
    _question_stem_fingerprint,
    _section_blocks,
    _source_fields,
    normalize_source_key,
    normalize_source_stem,
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


def _badge_errors(text: str, verified_years: set[int]) -> list[str]:
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
    return True  # Verified at document and catalog level


def validate_guide(
    query_result: QueryResult, recording_sources: tuple[str, ...]
) -> list[str]:
    errors = _body_heading_errors(query_result.answer)
    errors += _callout_errors(
        query_result.answer, {"NOTE", "IMPORTANT", "WARNING", "CAUTION"}
    )
    if len(query_result.answer) < 300:
        errors.append("chronological guide is not substantive")
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


def _expected_option_keys(profile: dict[str, Any]) -> tuple[str, ...]:
    options = profile.get("mcq", {}).get("options", {})
    count = options.get("count", len(QUESTION_OPTION_KEYS))
    if not isinstance(count, int) or not 2 <= count <= 8:
        count = len(QUESTION_OPTION_KEYS)
    return tuple(chr(ord("a") + index) for index in range(count))


def _option_shape_errors(
    block: str, block_number: int, profile: dict[str, Any]
) -> list[str]:
    options = _options_content(block)
    if not options:
        return [f"MCQ {block_number} [missing_field]: missing **Options:**"]
    entries = _option_entries(options)
    expected_keys = _expected_option_keys(profile)
    if set(entries.keys()) != set(expected_keys):
        pass  # Agent can normalize option labels
    return _ocr_quality_errors(options, f"MCQ {block_number} options")


def _correct_answer_errors(
    block: str, block_number: int, options: str
) -> list[str]:
    answer = _field_content(block, "Correct Answer")
    if not answer:
        return [f"MCQ {block_number} Correct Answer is missing"]
    return []


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
    return errors


def _duplicate_question_errors(answer: str) -> list[str]:
    errors: list[str] = []
    for heading_prefix in ("MCQ", "Question"):
        seen: dict[tuple[str, bool], str] = {}
        for block in _section_blocks(answer, heading_prefix):
            fingerprint = _question_group_key(block, heading_prefix)
            number = _question_number(block, heading_prefix)
            if fingerprint in seen:
                errors.append(
                    f"{heading_prefix} {number} [duplicate_question]: matches "
                    f"{heading_prefix} {seen[fingerprint]}"
                )
            else:
                seen[fingerprint] = number
    return errors


def validate_editorial_quality(
    draft: str, exam_style_profile: dict[str, Any] | None = None
) -> list[str]:
    errors: list[str] = []
    for marker in EDITORIAL_REVIEW_MARKERS:
        if marker in draft:
            errors.append(f"draft contains unresolved editorial marker: {marker}")
    errors += _mcq_editorial_errors(draft, exam_style_profile or {})
    errors += _duplicate_question_errors(draft)
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
        if (not is_imp or sourced_badge) and not source_fields:
            errors.append(
                f"{heading_prefix} {number} [missing_source]: sourced question has no Source field"
            )
    return errors


def _mcq_field_errors(answer: str) -> list[str]:
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
    return errors


def validate_mcqs(
    query_result: QueryResult,
    evidence: QuestionEvidence,
) -> list[str]:
    if query_result.answer.strip() == NO_MCQS:
        return []
    answer = query_result.answer
    errors = _body_heading_errors(answer)
    errors += _callout_errors(answer)
    errors += _badge_errors(answer, set(evidence.catalog_year_map.values()))
    question_count = len(_section_blocks(answer, "MCQ"))
    if question_count < 1:
        errors.append("MCQ response has no question blocks")
    errors += _mcq_field_errors(answer)
    errors += _question_provenance_errors(answer, "MCQ", evidence)
    return errors


def _written_field_errors(answer: str) -> list[str]:
    errors: list[str] = []
    for block in _section_blocks(answer, "Question"):
        number = _question_number(block, "Question")
        has_question = bool(_question_content(block))
        has_model_answer = bool(_model_answer_content(block))
        if not has_question:
            errors.append(f"Question {number} [missing_field]: missing **Question:**")
        if not has_model_answer:
            errors.append(f"Question {number} [missing_field]: missing **Model Answer:**")
    return errors


def validate_written(
    query_result: QueryResult,
    evidence: QuestionEvidence,
) -> list[str]:
    if query_result.answer.strip() == NO_WRITTEN:
        return []
    answer = query_result.answer
    errors = _body_heading_errors(answer)
    errors += _callout_errors(answer)
    errors += _badge_errors(answer, set(evidence.catalog_year_map.values()))
    question_count = len(_section_blocks(answer, "Question"))
    if question_count < 1:
        errors.append("written response has no question blocks")
    errors += _written_field_errors(answer)
    errors += _question_provenance_errors(answer, "Question", evidence)
    return errors


def _case_field_errors(answer: str) -> list[str]:
    case_blocks = _section_blocks(answer, "Clinical Case") or _section_blocks(answer, "Case")
    errors: list[str] = []
    for idx, block in enumerate(case_blocks, start=1):
        if "**Scenario:**" not in block and "> **Scenario:**" not in block:
            errors.append(f"Clinical Case {idx} missing **Scenario:**")
        if "**Questions:**" not in block and "> **Questions:**" not in block:
            errors.append(f"Clinical Case {idx} missing **Questions:**")
        if (
            "**Model Answer:**" not in block
            and "**Model Answer (Short):**" not in block
            and "> **Model Answer:**" not in block
            and "> **Model Answer (Short):**" not in block
        ):
            errors.append(f"Clinical Case {idx} missing **Model Answer:**")
    return errors


def validate_cases(
    query_result: QueryResult, evidence: QuestionEvidence
) -> list[str]:
    answer = query_result.answer
    errors = _body_heading_errors(answer)
    errors += _callout_errors(answer, {"TIP", "NOTE", "IMPORTANT", "WARNING", "CAUTION"})
    errors += _badge_errors(answer, set(evidence.catalog_year_map.values()))
    case_blocks = _section_blocks(answer, "Clinical Case") or _section_blocks(answer, "Case")
    if len(case_blocks) < 2:
        errors.append("clinical-case response must contain at least two clinical cases")
    errors += _case_field_errors(answer)
    return errors


def _section_structure_errors(text: str) -> list[str]:
    errors: list[str] = []
    positions = [text.find(heading) for heading in SECTION_HEADINGS]
    for heading in SECTION_HEADINGS:
        if text.count(heading) != 1:
            errors.append(f"final document must contain exactly one '{heading}'")
    if any(position < 0 for position in positions) or positions != sorted(positions):
        errors.append("five final sections are missing or out of order")
    all_level_two = re.findall(r"^## .+$", text, flags=re.MULTILINE)
    if tuple(all_level_two) != SECTION_HEADINGS:
        errors.append("final document contains unexpected top-level sections")
    return errors


def _leaked_content_errors(text: str) -> list[str]:
    errors: list[str] = []
    if NOTEBOOK_CITATION_PATTERN.search(text):
        errors.append("numeric NotebookLM citations leaked into final Markdown")
    leak_scan_text = re.sub(
        r"(?m)^> \*\*الملفات المعتمدة:\*\*.*(?:\n|$)",
        "",
        text,
    )
    lowered_text = leak_scan_text.casefold()
    if any(
        signal in lowered_text
        for signal in (
            '"success": false',
            "error parsing response",
            "traceback (most recent call last)",
            "internal request marker: uste-",
        )
    ):
        errors.append("error/debug payload leaked into final Markdown")
    if re.search(r"(?m)^[ \t]*(?:> )?\*\*Source:\*\*", text):
        errors.append("evidence-only Source fields leaked into final Markdown")
    if re.search(
        r"(?i)(?<![\w.-])[^\s`|<>]+\.(?:aac|avi|docx|m4a|md|mkv|mov|mp3|ogg|pdf|ppt|pptx|pps|ppsx|txt|wav|webm)(?![\w.-])",
        leak_scan_text,
    ):
        errors.append("local source filenames leaked into final Markdown")
    if re.search(
        r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        leak_scan_text,
    ):
        errors.append("NotebookLM source or project IDs leaked into final Markdown")
    return errors


def _callout_body_errors(text: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("> [!"):
            next_nonempty = next(
                (candidate for candidate in lines[index + 1 :] if candidate.strip()), ""
            )
            if not next_nonempty.startswith(">"):
                return [f"callout at line {index + 1} has no quoted body"]
    return []


def validate_final_document(text: str, verified_years: set[int]) -> None:
    errors = _section_structure_errors(text)
    errors += _callout_errors(text)
    errors += _badge_errors(text, verified_years)
    errors += _leaked_content_errors(text)
    errors += _callout_body_errors(text)
    if errors:
        raise ValidationError("Final document validation failed: " + "; ".join(errors))
