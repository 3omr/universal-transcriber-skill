#!/usr/bin/env python3
"""The NotebookLM prompts for the five transcript phases.

Pure string construction: nothing here reads the filesystem, calls nlm, or
touches a report -- the source manifest and badge instructions are rendered by
the engine and passed in as text. That is what makes the prompts reviewable on
their own, which matters because the contract they state is the only thing
holding the output to the five-section standard.

The NO_GROUNDED_* sentinels live here too: they are part of the prompt
contract, and the rule that a refusal must carry a written reason is stated in
the same place it is enforced.
"""

from __future__ import annotations

import json
import re
from typing import Any

IMP_HEADINGS = (
    "#### 1. \U0001f4cc Doctor's Spoken Pearls",
    "#### 2. \u26a0\ufe0f Diagnostic Traps",
    "#### 3. \U0001f6d1 Lethal Mistakes",
    "#### 4. \u2753 Interactive Doctor Questions",
    "#### 5. \U0001f4cb Exam Rules",
)
NO_MCQS = "NO_GROUNDED_MCQS"
NO_WRITTEN = "NO_GROUNDED_WRITTEN_QUESTIONS"
# The NotebookLM web query endpoint has a smaller effective question limit than
# the CLI's local 10,000-character validation. Assessment prompts used to embed
# the complete source manifest, which made an otherwise valid source request
# look like an invalid source-ID request. Keep the source list and the prompt
# contract compact enough for the provider, leaving room for a bounded repair
# suffix.
MAX_ASSESSMENT_CONTEXT_CHARS = 900
MAX_ASSESSMENT_QUERY_CHARS = 4000
MAX_ASSESSMENT_STYLE_CHARS = 750


def _truncate_query_fragment(text: str, limit: int) -> str:
    """Return a readable, line-safe fragment for a provider-bound query."""
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= 40:
        return text[:limit]
    shortened = text[: limit - 32].rsplit("\n", 1)[0].rstrip()
    if not shortened:
        shortened = text[: limit - 32].rstrip()
    return f"{shortened}\n[remaining guidance omitted for query size]"


def _compact_assessment_context(context: str) -> str:
    """Keep only source identity lines in assessment prompts.

    Guide/IMP prompts still receive the full authority manifest.  MCQ and
    written-question prompts already receive the exact assessment source IDs
    through ``--source-ids``; repeating the full manifest and enrichment
    policy only increases the provider request size and can trigger its
    generic ``invalid query`` response.  This fallback also protects callers
    that pass the old full manifest directly to a prompt builder.
    """
    context = context.strip()
    if len(context) <= MAX_ASSESSMENT_CONTEXT_CHARS:
        return context

    useful_lines: list[str] = []
    for line in context.splitlines():
        normalized = line.casefold()
        if (
            "verified past-exam" in normalized
            or "question-bank" in normalized
            or "canonical:" in normalized
            or re.match(r"\s*-\s*20\d{2}:", line)
        ):
            useful_lines.append(line.strip())
    compact = "\n".join(dict.fromkeys(useful_lines))
    if not compact:
        compact = context
    return _truncate_query_fragment(compact, MAX_ASSESSMENT_CONTEXT_CHARS)


def render_exam_style_profile(
    profile: dict[str, Any], max_chars: int | None = None
) -> str:
    """Render the agent's style observations as bounded, non-content guidance."""
    if not profile:
        rendered = (
            "No agent-supplied exam style profile is available. Infer formatting "
            "only from the verified past-exam/question-bank samples in the source scope."
        )
    else:
        rendered = (
            "AGENT-SUPPLIED EXAM STYLE PROFILE (format guidance only; never evidence or "
            "medical content):\n"
            + json.dumps(profile, ensure_ascii=False, indent=2)
        )
    return (
        _truncate_query_fragment(rendered, max_chars)
        if max_chars is not None
        else rendered
    )


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
STRICT LECTURE SCOPE CONSTRAINT: Extract ONLY questions directly relevant to the specific topics, mechanisms, and clinical conditions taught in this lecture's recording and slides for '{title}'. EXCLUDE questions belonging to other chapters or separate lectures that were not taught in this lecture (e.g. do not extract firearm wound mechanics or distant topics in a general mechanical wounds lecture). If a question's topic was not taught in this lecture, omit it entirely.
Preserve the original wording and meaning but
repair obvious OCR damage (split letters, joined words, and broken option
labels). This is OCR normalization, not rewriting: never modernize, paraphrase,
or improve the question's academic style. State the correct answer and give a concise
clinical explanation in Egyptian Arabic mixed with precise English medical
terms; explain distractors when the evidence supports it.

{badge_instructions}

{style_context}

Search every verified past-exam source in the evidence catalog. If the same
question and medically equivalent options appear in multiple verified years,
return one block only, collect all years in ascending order, and include one
**Source:** line for every supporting exam. Add **[Question Bank]** alongside
the Past Exams badge when a question-bank copy also supports it. Do not merge
questions when the options, negation, requested count, or clinical meaning differ.

Before returning the section, perform an editorial pass: put one option on each
line in the learned label order (a., b., c., d.), make Correct Answer start with an existing
option label, remove NotebookLM citation markers such as [34،86], and stop on
any word whose OCR cannot be restored confidently.

For every item use this exact field contract with ### MCQ N and its badge(s):
**Question:**, **Options:** (with each option on a new line: a. ..., b. ..., c. ..., d. ...),
**Source:** (if past exam/question bank), **Correct Answer:**, and **Clinical Explanation:**.
If no matching MCQ exists, return exactly {NO_MCQS}. Return section body only;
never use # or ## headings."""


MAX_EMPHASIS_CONTEXT_CHARS = 6_000


def emphasis_point_count(imp_section: str) -> int:
    """Count the individual points the IMP Points phase actually produced.

    Bullets and callout lines are the unit the doctor's emphasis arrives in;
    the five fixed #### headings are structure, not content.
    """
    count = 0
    for line in (imp_section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        body = stripped.lstrip("> ").strip()
        if not body or body.startswith("[!"):
            continue
        if re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", body):
            count += 1
    return count


def _emphasis_minimum(point_count: int) -> int:
    """How many IMP questions a section of this size should support."""
    if point_count <= 0:
        return 0
    return max(3, min(12, point_count // 6))


def _emphasis_context(imp_section: str, sentinel: str) -> str:
    """Render the verified IMP Points section as input for the IMP prompts.

    Before this the IMP prompts asked NotebookLM to rediscover the doctor's
    emphasis from the recording, even though the IMP Points phase had already
    produced and validated exactly that. On the OPs run the section held 274
    lines of emphasis and the MCQ prompt still returned the no-questions
    sentinel.
    """
    point_count = emphasis_point_count(imp_section)
    if not point_count:
        return (
            f"If no emphasized point supports an item, return {sentinel} on the "
            "first line followed by one sentence naming what was missing."
        )
    minimum = _emphasis_minimum(point_count)
    body = _truncate_query_fragment(imp_section, MAX_EMPHASIS_CONTEXT_CHARS)
    return f"""The 🌟 IMP Points section for this lecture has already been verified
against the recording. It contains {point_count} emphasized point(s). Work from
it directly instead of rediscovering the emphasis:

<imp_points>
{body}
</imp_points>

Cover the emphasized points that can carry a question. A section this size
should support at least {minimum} item(s); returning fewer means the emphasis
was not used. Returning {sentinel} is only acceptable if none of the
{point_count} points can carry one, and it must be followed on the next line by
one sentence naming why each category failed.
Silence is not an acceptable answer."""


def build_imp_mcq_prompt(
    title: str,
    exam_style_profile: dict[str, Any] | None = None,
    imp_section: str = "",
) -> str:
    style_context = render_exam_style_profile(exam_style_profile or {})
    emphasis_context = _emphasis_context(imp_section, NO_MCQS)
    return f"""Create only IMP MCQs for '{title}' from points explicitly emphasized
in the selected lecture recording. The selected slide source may clarify wording
but must not introduce an unspoken fact.

{emphasis_context}

{style_context}

Imitate the observed past-exam form exactly: stem length and command pattern,
four-option layout, option labels and case, punctuation, capitalization,
parallel option length, and distractor style. Do not copy a sample's subject
matter, wording, answer, or provenance. Keep stems short and direct; do not make
a clinical vignette unless the profile shows that pattern.

For every item use ### MCQ N **[IMP]**, then **Question:**, **Options:**,
**Correct Answer:**, and **Clinical Explanation:**. Put one
option on each line (a., b., c., d.), ensure the correct answer starts with an existing option
label, and use no Source field or verbatim label. Return section body only;
never use # or ## headings."""


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
STRICT LECTURE SCOPE CONSTRAINT: Extract ONLY questions directly relevant to the specific topics, classifications, and concepts taught in this lecture's recording and slides for '{title}'. EXCLUDE questions belonging to other lectures or separate chapters that were not taught in this lecture. If a question was not taught, omit it entirely.
Preserve the source wording and meaning while repairing obvious OCR damage in the question
text; do not paraphrase it into a new academic prompt.

{badge_instructions}

{style_context}

Search all verified assessment sources before returning the section. Merge only
exact or OCR-safe duplicate written questions, preserving every verified year
and source line. Keep questions with different command verbs, requested counts,
scope, or medical meaning separate; send uncertain semantic matches for Agent
review instead of merging them.

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
Run an editorial OCR pass before returning: repair split letters and joined words only when the source
supports the repair, remove NotebookLM citation markers, and flag unresolved wording instead of
guessing. No introduction, conclusion, or filler. If no grounded written
question exists, return exactly {NO_WRITTEN}. Return section body only; never use
# or ## headings."""


def build_imp_written_prompt(
    title: str,
    exam_style_profile: dict[str, Any] | None = None,
    imp_section: str = "",
) -> str:
    style_context = render_exam_style_profile(exam_style_profile or {})
    emphasis_context = _emphasis_context(imp_section, NO_WRITTEN)
    return f"""Create only IMP written questions for '{title}' from points explicitly
emphasized in the selected lecture recording. The slide source may clarify
wording but must not introduce an unspoken fact.

{emphasis_context}

{style_context}

Imitate the observed past-exam form: use the same short command verbs,
colon/dash/blank conventions, requested number of items, and concise numbered
answer shape. Do not replace a direct complete, enumerate, causes of, mechanism
of, treatment of, or give reason form with a long academic essay prompt unless
the profile shows that pattern.

For every item use ### Question N **[IMP]**, then **Question:**,
**Model Answer:**, and **Clinical Explanation:**. Use no Source field or verbatim label.
Model Answer must be in English only and strictly ULTRA-CONCISE keywords or short phrases (Egyptian exam marking key style, 1 to 5 words per point):
- For lists, blanks, and enumerations: provide only numbered concise keywords (1- Keyword 1\n2- Keyword 2\n...).
- For Give Reason: one concise clause.
- For Compare: a compact Markdown table with concise keywords.
- NEVER write long full-sentence explanations or paragraphs inside Model Answer.
Clinical Explanation must be in Egyptian Arabic explaining the clinical reasoning and exam pearls.
Return section body only; never use # or ## headings."""


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
STRICT LECTURE SCOPE CONSTRAINT: Sourced cases and questions MUST strictly fall within the taught scope, conditions, and mechanisms of '{title}' (recording and slides). Do not include case vignettes for other distinct lectures.
Study past exam patterns and observed question structures from the course to match:
- The typical case scenario style and length
- For cases sourced from past exams, reproduce all original sub-questions verbatim in their exact count, text, and sequence without omitting or shortening any sub-questions.
- For newly synthesized cases, questions MUST strictly follow the standard Egyptian medical exam case breakdown matching the subject/specialty (e.g. 1. Diagnosis / Most likely diagnosis, 2. DDx (Differential diagnosis) or Pathognomonic Clinical Picture (CP), 3. Diagnostic Investigations / Lab tests, 4. Treatment (TTT) / Specific Antidote / Emergency management / Precautions). NEVER create long essay sub-questions (e.g. 'Explain the dual physiological mechanisms...').
- Clear, concise, standard clinical exam questions without filler.

For every case use standard Markdown headings (do NOT use > [!TIP] blockquotes):
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
**Clinical Explanation:** Egyptian Arabic explanation covering comprehensive clinical reasoning, why specific signs are pathognomonic, and key points emphasized by the doctor.

Model Answer must be in English only and strictly ULTRA-CONCISE keywords or short phrases (Egyptian exam marking scheme style, 1 to 5 words per point). NEVER write long sentences, descriptive narratives, or paragraphs inside Model Answer. Put all detailed medical explanations and lecture context exclusively in **Clinical Explanation** (in Egyptian Arabic).

A case carrying a Past Exams or Question Bank badge must also contain
**Source:** with the exact source name and verified year.

{badge_instructions}
Use a past-exam or question-bank badge only for a verbatim or traceably adapted
cited scenario. Otherwise use exactly **[IMP]** only when the recording supports
the emphasis. Never leave either side of a badge unbolded. Return section body
only; never use # or ## headings."""
