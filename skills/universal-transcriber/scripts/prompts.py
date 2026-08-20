#!/usr/bin/env python3
"""Prompt generators and templates for Universal Medical Transcriber."""

from __future__ import annotations

import re
from typing import Any

from models import (
    IMP_HEADINGS,
    MAX_ASSESSMENT_CONTEXT_CHARS,
    MAX_ASSESSMENT_STYLE_CHARS,
    NO_MCQS,
    NO_WRITTEN,
)


def _compact_assessment_context(context: str) -> str:
    """Keep provider context compact to prevent payload limits."""
    lines = context.strip().splitlines()
    if not lines:
        return ""
    preserved: list[str] = []
    current_length = 0
    for line in lines:
        if line.startswith("- Assessment sources:"):
            continue
        line_cost = len(line) + 1
        if current_length + line_cost > MAX_ASSESSMENT_CONTEXT_CHARS:
            break
        preserved.append(line)
        current_length += line_cost
    return "\n".join(preserved).strip()


def render_exam_style_profile(
    profile: dict[str, Any], max_chars: int = MAX_ASSESSMENT_STYLE_CHARS
) -> str:
    """Render exam style preferences compactly into the prompt."""
    if not profile:
        return ""
    rendered_parts: list[str] = []
    mcq = profile.get("mcq") or {}
    if mcq.get("register"):
        rendered_parts.append(f"MCQ Style: {mcq['register']}")
    if mcq.get("max_stem_words"):
        rendered_parts.append(f"Max MCQ stem words: {mcq['max_stem_words']}")
    written = profile.get("written") or {}
    if written.get("answer_shape"):
        rendered_parts.append(f"Written Answer Style: {written['answer_shape']}")
    cases = profile.get("cases") or {}
    if cases.get("style"):
        rendered_parts.append(f"Cases Style: {cases['style']}")
    text = "Exam Style Guidance:\n" + "\n".join(f"- {p}" for p in rendered_parts)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text


def build_guide_prompt(subject: str, title: str, context: str) -> str:
    return f"""Create only the body of the 📖 Chronological Guide for {subject}: '{title}'.

{context}
The named recording is the sole authority for what the doctor said, the exact
teaching chronology, emphasis, dialogue, jokes, anecdotes, pauses, and
administrative remarks. Follow it step by step without summarizing, regrouping
into textbook order, or inventing transitions. Preserve quoted speech and
questions verbatim whenever the recording supports it.

Write the explanation in Egyptian Arabic mixed with precise English medical
terms. Use the slide source for titles, table structure, and figures that
correspond to spoken material. Use textbooks/references for terminology,
accuracy, and only the Agent-selected contextual details in the enrichment
policy. Never dump reference material or present it as spoken commentary. If an
unspoken book or slide detail directly clarifies a taught point, add it
selectively in this exact form and do not attribute it to the doctor:
> [!NOTE]
> **إضافة من الكتاب/السلايد — لم يشرحها الدكتور في التسجيل**
> concise contextual addition
If a reference corrects a spoken terminology error, preserve what was said and
add a clearly attributed NOTE. Surface conflicts for editorial review instead of
silently choosing one source.

Return section body only. Use ### and #### headings, never # or ##. Use only
> [!NOTE], > [!IMPORTANT], > [!WARNING], and > [!CAUTION]. Reserve CAUTION for
absolute contraindications, red flags, or lethal errors. Do not produce a summary."""


def build_imp_prompt(title: str, context: str) -> str:
    headings = "\n".join(IMP_HEADINGS)
    return f"""Create only the body of the 🌟 IMP Points section for '{title}'.

{context}
Use only points explicitly emphasized or spoken in the recording. Do not add
generic textbook high-yield facts. Return exactly these five #### headings in
exactly this order and no other headings:
{headings}

Under Diagnostic Traps, put every item in a > [!WARNING] block. Under Lethal
Mistakes, put every item in a > [!CAUTION] block. If either category has no
explicit item, keep its heading and place an explicit 'None explicitly stated in
the recording' message inside the required callout. Preserve every interactive
doctor question and the answer actually given. Exam Rules includes grading,
booklet, attendance, exam format, and other non-medical instructions. Write in
Egyptian Arabic mixed with English medical terms. Return the section body only."""


def build_mcq_prompt(
    title: str,
    context: str,
    badge_instructions: str,
    exam_style_profile: dict[str, Any] | None = None,
) -> str:
    context = _compact_assessment_context(context)
    style_context = render_exam_style_profile(
        exam_style_profile or {}, MAX_ASSESSMENT_STYLE_CHARS
    )
    return f"""Create only the body of the ❓ MCQs section for '{title}'.

{context}
Extract every relevant MCQ from verified past-exam or question-bank sources.
STRICT LECTURE SCOPE CONSTRAINT: Extract ONLY questions directly relevant to the specific topics, mechanisms, and clinical conditions taught in this lecture's recording and slides for '{title}'. EXCLUDE questions belonging to other chapters or separate lectures that were not taught in this lecture.

VERBATIM EXTRACTION DIRECTIVE:
- Copy question stems and options (a., b., c., d.) word-for-word exactly as printed in the exam documents.
- Do NOT paraphrase, summarize, fix exam phrasing, or shorten options.
- Repair obvious OCR damage (split letters, joined words, and broken option labels) while preserving exact original wording.

State the correct answer and give a concise clinical explanation in Egyptian Arabic mixed with precise English medical terms; explain distractors when the evidence supports it.

{badge_instructions}

{style_context}

Search every verified past-exam source in the evidence catalog. If the same
question appears in multiple verified years, return one block only, collect all years in ascending order, and include one **Source:** line for supporting exams. Add **[Question Bank]** alongside the Past Exams badge when a question-bank copy also supports it.

For every item use this exact field contract with ### MCQ N and its badge(s):
**Question:**, **Options:** (with each option on a new line: - **a.** ..., - **b.** ..., - **c.** ..., - **d.** ...),
**Source:** (if past exam/question bank), **Correct Answer:**, and **Clinical Explanation:**.
If no matching MCQ exists, return exactly {NO_MCQS}. Return section body only;
never use # or ## headings."""


def build_imp_mcq_prompt(
    title: str, exam_style_profile: dict[str, Any] | None = None
) -> str:
    style_context = render_exam_style_profile(exam_style_profile or {})
    return f"""Create only IMP MCQs for '{title}' from points explicitly emphasized
in the selected lecture recording. The selected slide source may clarify wording
but must not introduce an unspoken fact.

{style_context}

Imitate the observed past-exam form exactly: stem length and command pattern,
four-option layout, option labels, and distractor style. Keep stems short and direct.

For every item use ### MCQ N **[IMP]**, then **Question:**, **Options:**,
**Correct Answer:**, and **Clinical Explanation:**. Put one
option on each line (- **a.** ..., - **b.** ..., - **c.** ..., - **d.** ...), ensure the correct answer starts with an existing option
label, and use no Source field. Return section body only;
never use # or ## headings. If no emphasized point supports an MCQ, return
exactly {NO_MCQS}."""


def build_written_prompt(
    title: str,
    context: str,
    badge_instructions: str,
    exam_style_profile: dict[str, Any] | None = None,
) -> str:
    context = _compact_assessment_context(context)
    style_context = render_exam_style_profile(
        exam_style_profile or {}, MAX_ASSESSMENT_STYLE_CHARS
    )
    return f"""Create only the body of the ✍️ Written Questions section for '{title}'.

{context}
Extract every matching Essay, Short Note, Enumerate, Compare, Give Reason, or
other written question from verified exam/question-bank sources.
STRICT LECTURE SCOPE CONSTRAINT: Extract ONLY questions directly relevant to the specific topics and concepts taught in this lecture's recording and slides for '{title}'.

VERBATIM EXTRACTION DIRECTIVE:
- Copy written question stems word-for-word exactly as printed in the exam documents.
- Preserve source wording and meaning while repairing obvious OCR damage in the question text. Do not paraphrase it into a new academic prompt.

{badge_instructions}

{style_context}

For every item use ### Question N with badge(s), then **Question:**,
**Source:** (if past exam/question bank), **Model Answer:**, and **Clinical Explanation:**.
Model Answer must be in English only and strictly ULTRA-CONCISE keywords or short phrases (Egyptian exam marking key style, 1 to 5 words per point):
- For lists, blanks, and enumerations (e.g. 1... 2... 3...): provide only numbered concise keywords:
  1- Concise keyword 1
  2- Concise keyword 2
  3- Concise keyword 3
- For Give Reason: one concise clause (e.g. Due to inhibition of Cytochrome Oxidase).
- For Compare: a compact Markdown table containing concise keywords.
- NEVER write long full-sentence explanations or paragraphs inside Model Answer.
Clinical Explanation must be in Egyptian Arabic explaining the detailed clinical reasoning, mechanisms, and doctor emphasis.
If no grounded written question exists, return exactly {NO_WRITTEN}. Return section body only; never use # or ## headings."""


def build_imp_written_prompt(
    title: str, exam_style_profile: dict[str, Any] | None = None
) -> str:
    style_context = render_exam_style_profile(exam_style_profile or {})
    return f"""Create only IMP written questions for '{title}' from points explicitly
emphasized in the selected lecture recording. The slide source may clarify
wording but must not introduce an unspoken fact.

{style_context}

Imitate the observed past-exam form: use the same short command verbs and concise numbered answer shape.

For every item use ### Question N **[IMP]**, then **Question:**,
**Model Answer:**, and **Clinical Explanation:**. Use no Source field.
Model Answer must be in English only and strictly ULTRA-CONCISE keywords or short phrases (Egyptian exam marking key style, 1 to 5 words per point):
- For lists, blanks, and enumerations: provide only numbered concise keywords (1- Keyword 1\n2- Keyword 2\n...).
- For Give Reason: one concise clause.
- For Compare: a compact Markdown table with concise keywords.
- NEVER write long full-sentence explanations or paragraphs inside Model Answer.
Clinical Explanation must be in Egyptian Arabic explaining the clinical reasoning and exam pearls.
Return section body only; never use # or ## headings. If no emphasized point supports a written question, return
exactly {NO_WRITTEN}."""


def build_case_prompt(
    title: str,
    context: str,
    badge_instructions: str,
    exam_style_profile: dict[str, Any] | None = None,
) -> str:
    context = _compact_assessment_context(context)
    style_context = render_exam_style_profile(
        exam_style_profile or {}, MAX_ASSESSMENT_STYLE_CHARS
    )
    return f"""Create only the body of the 🩺 Clinical Cases section for '{title}'.

{context}

{style_context}

Create 2-3 clinically relevant cases within the recording's taught scope.
STRICT LECTURE SCOPE CONSTRAINT: Sourced cases and questions MUST strictly fall within the taught scope, conditions, and mechanisms of '{title}' (recording and slides).

VERBATIM PAST EXAM DIRECTIVE:
- For cases sourced from past exams, reproduce all original sub-questions verbatim in their exact count, text, and sequence without omitting or shortening any sub-questions.
- For newly synthesized cases, questions MUST strictly follow the standard Egyptian medical exam case breakdown matching the subject (1. Diagnosis / Most likely diagnosis, 2. DDx or Clinical Picture, 3. Diagnostic Investigations / Lab tests, 4. Treatment (TTT) / Antidote / Emergency management).

For every case use standard Markdown headings:
### Clinical Case N with evidence-backed badge(s)
**Scenario:** concise clinical scenario
**Questions:**
1. What is the most likely diagnosis?
2. What is the differential diagnosis (DDx) / characteristic clinical feature?
3. Mention key diagnostic investigations.
4. Outline the lines of treatment (TTT) / antidote.
**Model Answer:**
1. **Diagnosis:**
   - Concise keyword answer (1 to 5 words)
2. **DDx / Clinical Picture:**
   - Concise keyword 1
   - Concise keyword 2
3. **Investigations:**
   - Concise keyword
4. **Treatment (TTT):**
   - Concise keyword 1
   - Concise keyword 2
**Clinical Explanation:** Egyptian Arabic explanation covering comprehensive clinical reasoning and key doctor pearls.

Model Answer must be in English only and strictly ULTRA-CONCISE keywords or short phrases (Egyptian exam marking scheme style, 1 to 5 words per point). NEVER write long sentences or descriptive paragraphs inside Model Answer. Put all detailed medical explanations exclusively in **Clinical Explanation** (in Egyptian Arabic).

A case carrying a Past Exams or Question Bank badge must also contain **Source:** with the exact source name.

{badge_instructions}
Return section body only; never use # or ## headings."""


def repair_instructions(errors: list[str]) -> str:
    """Generate concise guidance for Agent repair."""
    joined = "; ".join(errors)
    return f"REPAIR REQUIRED: The previous response was rejected for these reasons: {joined}. Return the complete section body again and obey every original format rule."
