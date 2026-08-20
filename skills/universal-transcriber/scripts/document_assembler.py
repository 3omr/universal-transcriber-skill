#!/usr/bin/env python3
"""Document assembly, final validation, and index file updater."""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from models import (
    OutputTarget,
    QuestionEvidence,
    TranscriptIdentity,
    TranscriberError,
    ValidationError,
)
from text_processors import (
    clean_notebooklm_phrases,
    deduplicate_question_section,
    format_markdown_tables,
    remove_evidence_fields,
    renumber_question_section,
)
from validators import (
    _question_provenance_errors,
    validate_editorial_quality,
    validate_final_document,
)


def _prepare_temp(dest_path: str, content: bytes) -> str:
    parent_dir = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(parent_dir, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=parent_dir, prefix=".tmp_transcriber_")
    try:
        with os.fdopen(temp_fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        return temp_path
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def student_document_from_draft(draft: str) -> str:
    cleaned = clean_notebooklm_phrases(remove_evidence_fields(draft))
    return format_markdown_tables(cleaned) + "\n"


def finalize_student_document(
    draft: str,
    verified_years: set[int],
    exam_style_profile: dict[str, Any] | None = None,
    evidence_catalog: list[dict[str, Any]] | None = None,
) -> str:
    """Agent editorial review & final student document preparation."""
    reviewed = draft
    for heading_prefix in ("MCQ", "Question", "Clinical Case"):
        reviewed = deduplicate_question_section(
            reviewed, heading_prefix, {year: [] for year in verified_years}, evidence_catalog
        )
        reviewed = renumber_question_section(reviewed, heading_prefix)
    editorial_errors = validate_editorial_quality(reviewed, exam_style_profile)
    if evidence_catalog:
        catalog_year_map = {
            int(y): [entry.get("canonical_name", "")]
            for entry in evidence_catalog
            for y in entry.get("verified_years", [])
            if str(y).isdigit()
        }
        catalog_names = {str(entry.get("canonical_name", "")) for entry in evidence_catalog}
        provenance_evidence = QuestionEvidence(
            catalog_year_map=catalog_year_map,
            catalog_names=catalog_names,
            evidence_catalog=evidence_catalog,
        )
        editorial_errors += _question_provenance_errors(
            reviewed, "MCQ", provenance_evidence
        )
        editorial_errors += _question_provenance_errors(
            reviewed, "Question", provenance_evidence
        )
    if editorial_errors:
        raise ValidationError(
            "Editorial review required: " + "; ".join(editorial_errors)
        )
    finalized = student_document_from_draft(reviewed)
    validate_final_document(finalized, verified_years)
    return finalized


def save_draft(draft: str, target: OutputTarget, verified_years: set[int]) -> None:
    validate_final_document(student_document_from_draft(draft), verified_years)
    draft_path = target.output_path + ".draft.md"
    temporary_path = _prepare_temp(draft_path, draft.encode("utf-8"))
    try:
        os.replace(temporary_path, draft_path)
    except OSError as error:
        raise TranscriberError(f"Atomic draft write failed: {error}") from error
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    print(f"[+] Saved evidence-rich draft for Agent review: {draft_path}")


def index_row(identity: TranscriptIdentity, target: OutputTarget) -> str:
    encoded_name = urllib.parse.quote(target.file_name, safe="/")
    return (
        f"| {identity.emoji} {identity.title} | [فتح التفريغ](./{encoded_name}) | "
        "شاملة الدليل الزمني وIMP Points وMCQs والأسئلة التحريرية "
        "والحالات السريرية |\n"
    )


def update_index_file(identity: TranscriptIdentity, target: OutputTarget) -> None:
    index_path = target.index_path
    row = index_row(identity, target)
    if not os.path.exists(index_path):
        header = (
            f"# تفريغات {identity.subject} 🩺\n\n"
            "| المحاضرة | رابط التفريغ | الملاحظات |\n"
            "| :--- | :--- | :--- |\n"
        )
        content = header + row
    else:
        with open(index_path, "r", encoding="utf-8") as f:
            existing = f.read()
        target_token = f"فتح التفريغ](./{urllib.parse.quote(target.file_name, safe='/')})"
        if target_token in existing:
            return
        content = existing.rstrip() + "\n" + row
    temp_path = _prepare_temp(index_path, content.encode("utf-8"))
    try:
        os.replace(temp_path, index_path)
    except OSError as error:
        raise TranscriberError(f"Failed to update index file: {error}") from error
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
