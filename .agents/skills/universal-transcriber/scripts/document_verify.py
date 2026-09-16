#!/usr/bin/env python3
"""Deciding whether a document's text layer is usable before it is uploaded.

A scanned exam paper with no text layer produces a transcript with no
evidence behind its badges, so every PDF and DOCX is checked here first and
routed to OCR when it fails. Extracted from universal_transcribe.py; the
engine re-exports verify_document_text.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree

from exam_years import extract_exam_years
from transcriber_models import LocalSource, OCRReport, PDFMetrics


def _garbage_ratio(text: str) -> float:
    if not text:
        return 1.0
    garbage = text.count("\ufffd") + sum(
        1 for character in text if ord(character) < 32 and character not in "\n\r\t\f"
    )
    return garbage / max(len(text), 1)


def _pdf_tool_failure(source: LocalSource, reason: str) -> tuple[OCRReport, tuple[int, ...]]:
    return OCRReport(source.path, "fail", reason[:500]), ()


def _run_pdf_tools(
    source: LocalSource,
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    page_metadata = subprocess.run(
        ["pdfinfo", source.path], capture_output=True, text=True, timeout=60
    )
    extracted_text = subprocess.run(
        ["pdftotext", "-layout", source.path, "-"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    return page_metadata, extracted_text


def _pdf_pages(page_metadata: str, extracted_text: str) -> tuple[int, list[str]]:
    page_match = re.search(r"^Pages:\s+(\d+)", page_metadata, flags=re.MULTILINE)
    declared_pages = int(page_match.group(1)) if page_match else 0
    pages = extracted_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if declared_pages and len(pages) < declared_pages:
        pages.extend([""] * (declared_pages - len(pages)))
    return declared_pages or max(len(pages), 1), pages


def _pdf_metrics(page_metadata: str, extracted_text: str) -> PDFMetrics:
    page_count, pages = _pdf_pages(page_metadata, extracted_text)
    character_counts = [sum(character.isalnum() for character in page) for page in pages]
    sparse_pages = sum(count < 20 for count in character_counts)
    return PDFMetrics(
        page_count=page_count,
        text_pages=sum(count >= 20 for count in character_counts),
        total_characters=sum(character_counts),
        sparse_page_ratio=sparse_pages / max(page_count, 1),
        garbage_ratio=_garbage_ratio(extracted_text),
    )


def _pdf_quality(metrics: PDFMetrics, source_role: str) -> tuple[str, str]:
    if metrics.total_characters < 50:
        return "fail", "No usable OCR/text layer was extracted"
    if metrics.garbage_ratio > 0.02:
        return "fail", "Extracted text contains excessive corrupt characters"
    if metrics.sparse_page_ratio > 0.80 and source_role in {
        "past_exam",
        "question_bank",
    }:
        return "fail", "Most exam/question-bank pages have no usable text"
    characters_per_page = metrics.total_characters / max(metrics.page_count, 1)
    if metrics.sparse_page_ratio > 0.60 or characters_per_page < 80:
        return "warning", "Text is sparse; review OCR quality manually"
    return "pass", "Extractable text is available"


def _pdf_report(source: LocalSource, metrics: PDFMetrics) -> OCRReport:
    status, reason = _pdf_quality(metrics, source.role)
    return OCRReport(
        path=source.path,
        status=status,
        reason=reason,
        page_count=metrics.page_count,
        text_pages=metrics.text_pages,
        total_characters=metrics.total_characters,
        sparse_page_ratio=metrics.sparse_page_ratio,
        garbage_ratio=metrics.garbage_ratio,
    )


def _verify_pdf(source: LocalSource) -> tuple[OCRReport, tuple[int, ...]]:
    if not shutil.which("pdfinfo") or not shutil.which("pdftotext"):
        return _pdf_tool_failure(
            source, "pdfinfo and pdftotext are required for PDF text verification"
        )
    try:
        page_metadata, extracted_text = _run_pdf_tools(source)
    except subprocess.TimeoutExpired:
        return _pdf_tool_failure(source, "PDF text extraction timed out")
    if page_metadata.returncode != 0 or extracted_text.returncode != 0:
        reason = (
            extracted_text.stderr.strip()
            or page_metadata.stderr.strip()
            or "PDF extraction failed"
        )
        return _pdf_tool_failure(source, reason)
    metrics = _pdf_metrics(page_metadata.stdout, extracted_text.stdout)
    return _pdf_report(source, metrics), extract_exam_years(extracted_text.stdout)


def _docx_text(source: LocalSource) -> str:
    with zipfile.ZipFile(source.path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)
    return " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))


def _docx_report(source: LocalSource, text: str) -> OCRReport:
    total_characters = sum(character.isalnum() for character in text)
    garbage_ratio = _garbage_ratio(text)
    status, reason = "pass", "Extractable document text is available"
    if total_characters < 50:
        status, reason = "fail", "DOCX is empty or image-only and needs OCR"
    elif garbage_ratio > 0.02:
        status, reason = "fail", "DOCX text contains excessive corrupt characters"
    elif total_characters < 200:
        status, reason = "warning", "DOCX contains very little extractable text"
    return OCRReport(
        path=source.path,
        status=status,
        reason=reason,
        total_characters=total_characters,
        text_pages=1 if total_characters else 0,
        garbage_ratio=garbage_ratio,
    )


def _verify_docx(source: LocalSource) -> tuple[OCRReport, tuple[int, ...]]:
    try:
        text = _docx_text(source)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        return OCRReport(source.path, "fail", f"DOCX extraction failed: {error}"), ()
    return _docx_report(source, text), extract_exam_years(text)


def verify_document_text(local_sources: list[LocalSource]) -> None:
    for source in local_sources:
        if source.is_preparation_planned:
            source.ocr = OCRReport(
                source.path,
                "planned",
                f"{source.preparation_action} will create the searchable upload artifact",
            )
            continue
        if source.preparation_action == "use_remote":
            source.ocr = OCRReport(
                source.path,
                "remote",
                "A ready NotebookLM equivalent is authoritative; local text is not required",
            )
            continue
        report: OCRReport | None = None
        text_years: tuple[int, ...] = ()
        effective_extension = source.upload_extension
        if effective_extension == ".pdf":
            report, text_years = _verify_pdf(source)
        elif effective_extension == ".docx":
            report, text_years = _verify_docx(source)
        source.ocr = report
        if not source.years_verified_by_manifest:
            source.years = tuple(sorted(set(source.years).union(text_years)))
