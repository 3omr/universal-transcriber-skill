#!/usr/bin/env python3
"""Text cleaning, normalization, deduplication, and formatting utilities."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from models import (
    ARABIC_DIGITS,
    BADGE_LIKE_PATTERN,
    NOTEBOOK_CITATION_PATTERN,
)


def normalize_source_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").translate(ARABIC_DIGITS)
    normalized = re.sub(r"\s+", " ", normalized.casefold()).strip()
    return re.sub(r"[^\w\u0600-\u06ff]+", " ", normalized).strip()


def normalize_source_stem(value: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", value or "").strip()
    return normalize_source_key(stem)


def clean_notebooklm_phrases(text: str) -> str:
    """Strip chatty NotebookLM conversational residue and sign-offs."""
    patterns = [
        r"(?ms)\n*^[^\w\n]*\s*إيه رأيك.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*ما رأيك.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*هل تود.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*هل تحب.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*هل ترغب.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*ننتقل بعد كدة.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*لو ننتقل بعد كدة.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*كده جمعنا.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*لقد قمت باستخلاص.*?(?:\n\n|\Z)",
        r"(?ms)\n*^[^\w\n]*\s*أتمنى أن تكون هذه الأسئلة.*?(?:\n\n|\Z)",
        r"(?ms)\n*I have generated.*?(?:\n\n|\Z)",
        r"(?ms)\n*Let me know if.*?(?:\n\n|\Z)",
        r"(?ms)\n*Would you like me to.*?(?:\n\n|\Z)",
    ]
    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned)
    return cleaned.strip()




def format_markdown_tables(text: str) -> str:
    """Ensure tables have proper spacing in Markdown."""
    lines = text.splitlines()
    output: list[str] = []
    for line in lines:
        if (
            line.strip().startswith("|")
            and output
            and output[-1].strip()
            and not output[-1].strip().startswith("|")
        ):
            output.append("")
        output.append(line)
        if (
            line.strip().startswith("|")
            and lines
            and len(output) < len(lines)
            and lines[len(output)].strip()
            and not lines[len(output)].strip().startswith("|")
        ):
            output.append("")
    return "\n".join(output)


def remove_evidence_fields(text: str) -> str:
    """Strip internal evidence fields (**Source:**) for student presentation."""
    return re.sub(r"(?m)^[ \t]*(?:> )?\*\*Source:\*\*.*(?:\n|$)", "", text)


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


def _question_number(block: str, heading_prefix: str) -> str:
    match = re.search(
        rf"^### {re.escape(heading_prefix)}\s+(\d+)", block, re.MULTILINE
    )
    return match.group(1) if match else "?"


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
        model_ans = _field_content(block, "Model Answer")
        diag_match = re.search(
            r"(?:1\.\s*)?\*\*Diagnosis:\*\*\s*(?:-\s*)?([^\n]+)",
            model_ans,
            re.IGNORECASE,
        )
        if diag_match and "**[IMP]**" in block:
            raw_diag = diag_match.group(1).strip()
            clean_diag = re.sub(r"\(.*?\)", "", raw_diag)
            clean_diag = re.sub(
                r"\b(?:left|right|bilateral|unilateral|rt|lt)\b",
                "",
                clean_diag,
                flags=re.IGNORECASE,
            ).strip()
            return _question_fingerprint_text(clean_diag), True
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


def _unique_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _merged_badges(
    blocks: list[str],
    year_map: dict[int, list[str]],
    evidence_catalog: list[dict[str, Any]] | None,
) -> list[str]:
    years: set[int] = set()
    roles: set[str] = set()
    for block in blocks:
        for badge in BADGE_LIKE_PATTERN.findall(block):
            if badge.startswith("**[Past Exams"):
                years.update(int(year) for year in re.findall(r"20\d{2}", badge))
        for source_field in _source_fields(block):
            norm = normalize_source_key(source_field)
            if evidence_catalog:
                for entry in evidence_catalog:
                    if norm == str(entry.get("normalized_name", "")):
                        years.update(int(y) for y in entry.get("verified_years", []))
                        if entry.get("role"):
                            roles.add(str(entry["role"]))
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
        badges.append(f"**[Past Exams - {year_text}]**")
    if question_bank:
        badges.append("**[Question Bank]**")
    if imp:
        badges.append("**[IMP]**")
    if not badges:
        badges.append("**[IMP]**")
    return _unique_strings(badges)


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
            source
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
