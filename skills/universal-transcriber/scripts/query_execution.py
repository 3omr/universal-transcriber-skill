#!/usr/bin/env python3
"""Asking a phase, and everything that happens when the answer is no good.

This is the loop the whole pipeline turns on. A phase prompt goes out, and what
comes back may be an answer, a refusal, a truncation, a rejected source group,
or a service that decided the request was malformed. run_phase_query is what
turns that into either a validated section or a diagnosis precise enough for
the Agent to repair.

The important property, and the reason this is its own module: none of it knows
which backend produced the text. The engine chooses one (see engines/), this
decides whether to accept it, retry it, narrow the source scope, compact the
prompt, or stop and hand the evidence back. A backend that retried internally
would defeat every one of those.

NLM_QUERY_TIMEOUT_SECONDS and MAX_SOURCE_IDS_PER_QUERY live here because they
are limits of the request this module makes, not of the engine around it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from engine_utils import _is_any_empty_sentinel, _unique_strings
from nlm_client import _run_nlm_json
from question_prompts import (
    IMP_HEADINGS,
    MAX_ASSESSMENT_QUERY_CHARS,
    _truncate_query_fragment,
)
from transcriber_models import (
    MAX_ATTEMPTS,
    NlmError,
    NlmQueryRequest,
    PhaseQuery,
    PhaseValidationError,
    ProjectQueryScope,
    QueryResult,
    SourceQuarantine,
)

# How long a single NotebookLM query may take. The service is slow enough that
# a short timeout turns a working run into a flaky one.
NLM_QUERY_TIMEOUT_SECONDS = 205
# NotebookLM rejects a query scoped to too many sources at once, so a phase is
# asked in groups of this size and the answers merged.
MAX_SOURCE_IDS_PER_QUERY = 3


def _reference_names(payload: Any) -> list[str]:
    entries = payload if isinstance(payload, list) else []
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(
            entry.get("title")
            or entry.get("source_title")
            or entry.get("source_name")
            or ""
        ).strip()
        if name and name not in names:
            names.append(name)
    return names


def _nlm_query_arguments(
    request: NlmQueryRequest,
    notebook_id: str | None = None,
    include_source_ids: bool = True,
    source_ids: tuple[str, ...] | None = None,
) -> list[str]:
    target_notebook = notebook_id or request.notebook.notebook_uuid
    arguments = [
        "notebook",
        "query",
        target_notebook,
        request.query_text,
        "--timeout",
        "180",
    ]
    selected_source_ids = request.source_ids if source_ids is None else source_ids
    if include_source_ids and selected_source_ids:
        arguments.extend(["--source-ids", ",".join(selected_source_ids)])
    return arguments


def _query_result_from_payload(
    payload: Any, request: NlmQueryRequest
) -> QueryResult:
    if not isinstance(payload, dict):
        raise NlmError("nlm notebook query returned an unexpected payload")
    names = list(request.source_names)
    for key in ("references", "sources_used"):
        for name in _reference_names(payload.get(key, [])):
            if name not in names:
                names.append(name)
    return QueryResult(
        answer=str(payload.get("answer") or payload.get("response") or "").strip(),
        source_names=tuple(names),
        session_id=(
            str(payload.get("conversation_id"))
            if payload.get("conversation_id")
            else None
        ),
    )


def _merge_imp_answers(answers: list[str]) -> str:
    sections: dict[str, list[str]] = {heading: [] for heading in IMP_HEADINGS}
    for answer in answers:
        for index, heading in enumerate(IMP_HEADINGS):
            next_headings = IMP_HEADINGS[index + 1 :]
            boundary = "|".join(re.escape(item) for item in next_headings)
            pattern = rf"(?ms)^{re.escape(heading)}\s*\n?(.*?)(?=^(?:{boundary})\s*$|\Z)"
            match = re.search(pattern, answer)
            if match and match.group(1).strip():
                sections[heading].append(match.group(1).strip())
    if not any(sections.values()):
        return "\n\n".join(answer.strip() for answer in answers if answer.strip())
    return "\n\n".join(
        heading
        + "\n"
        + ("\n\n".join(_unique_strings(sections[heading])) or "None explicitly stated")
        for heading in IMP_HEADINGS
    )


def _project_heading_pattern(phase_name: str) -> re.Pattern[str] | None:
    if phase_name == "MCQs":
        return re.compile(r"^### MCQ\s+\d+", flags=re.MULTILINE)
    if phase_name == "Written Questions":
        return re.compile(r"^### Question\s+\d+", flags=re.MULTILINE)
    if phase_name == "Clinical Cases":
        return re.compile(
            r"(?:^### (?:Clinical )?Case\s+\d+|(\*\*🩺 Clinical Case )\d+(:\*\*))",
            flags=re.MULTILINE,
        )
    return None


def _renumber_project_answer(
    answer: str, phase_name: str, start_number: int
) -> tuple[str, int]:
    pattern = _project_heading_pattern(phase_name)
    if pattern is None:
        return answer, start_number
    number = start_number

    def replace(match: re.Match[str]) -> str:
        nonlocal number
        if phase_name == "MCQs":
            replacement = f"### MCQ {number}"
        elif phase_name == "Written Questions":
            replacement = f"### Question {number}"
        elif phase_name == "Clinical Cases":
            matched_str = match.group(0)
            if matched_str.startswith("###"):
                replacement = f"### Clinical Case {number}"
            else:
                replacement = f"**🩺 Clinical Case {number}:**"
        else:
            replacement = match.group(0)
        number += 1
        return replacement

    return pattern.sub(replace, answer), number



def _usable_query_results(query_results: list[QueryResult]) -> list[QueryResult]:
    return [
        query_result
        for query_result in query_results
        if query_result.answer.strip()
        and not _is_any_empty_sentinel(query_result.answer)
    ]


def _query_source_names(query_results: list[QueryResult]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            name for query_result in query_results for name in query_result.source_names
        )
    )


def _query_source_quarantine(
    query_results: list[QueryResult],
) -> tuple[SourceQuarantine, ...]:
    unique: dict[tuple[str, str], SourceQuarantine] = {}
    for query_result in query_results:
        for quarantine in query_result.source_quarantine:
            unique[(quarantine.notebook_uuid, quarantine.source_id)] = quarantine
    return tuple(unique.values())


PROSE_PHASES = frozenset({"Chronological Guide"})


def deduplicate_prose_blocks(text: str) -> str:
    """Drop repeated ### blocks produced by querying one phase in slices.

    NotebookLM caps how many source IDs one request may carry, so a phase with
    more sources than the cap is sent as several sliced queries and the answers
    are concatenated. For the question phases that is what we want -- each
    slice finds different questions. For narrative prose it is not: every slice
    returns the same walkthrough of the same lecture, so the guide arrived at
    exactly double length with all 28 of its sections repeated verbatim.
    """
    blocks = re.split(r"(?m)^(?=#{3,6} )", text)
    seen: set[str] = set()
    kept: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        key = re.sub(r"\s+", " ", stripped)
        if key in seen:
            continue
        seen.add(key)
        kept.append(stripped)
    return "\n\n".join(kept)


def _merge_answer_bodies(query_results: list[QueryResult], phase_name: str) -> str:
    if phase_name == "IMP Points":
        return _merge_imp_answers([query_result.answer for query_result in query_results])
    answer_parts: list[str] = []
    next_number = 1
    for query_result in query_results:
        numbered, next_number = _renumber_project_answer(
            query_result.answer, phase_name, next_number
        )
        answer_parts.append(numbered.strip())
    merged = "\n\n".join(answer_parts)
    if phase_name in PROSE_PHASES:
        return deduplicate_prose_blocks(merged)
    return merged


def _merge_notebook_query_results(
    query_results: list[QueryResult], phase_name: str
) -> QueryResult:
    usable = _usable_query_results(query_results)
    if not usable:
        merged_answer = next(
            (query_result.answer.strip() for query_result in query_results if query_result.answer.strip()),
            "",
        )
    else:
        merged_answer = _merge_answer_bodies(usable, phase_name)
    return QueryResult(
        answer=merged_answer,
        source_names=_query_source_names(query_results),
        source_quarantine=_query_source_quarantine(query_results),
    )


def _query_project_scope(
    request: NlmQueryRequest, scope: ProjectQueryScope
) -> QueryResult:
    scoped_names = _scope_names_for_ids(scope, scope.source_ids)
    scoped_request = NlmQueryRequest(
        config=request.config,
        notebook=request.notebook,
        query_text=request.query_text,
        source_ids=scope.source_ids,
        source_names=scoped_names,
        notebook_ids=(scope.notebook_uuid,),
        phase_name=request.phase_name,
        project_scopes=(scope,),
    )
    payload = _run_nlm_json(
        request.config,
        _nlm_query_arguments(
            scoped_request,
            notebook_id=scope.notebook_uuid,
            source_ids=scope.source_ids,
        ),
        NLM_QUERY_TIMEOUT_SECONDS,
        f"nlm notebook query ({scope.notebook_uuid})",
    )
    return _query_result_from_payload(payload, scoped_request)


def _scope_names_for_ids(
    scope: ProjectQueryScope, source_ids: tuple[str, ...]
) -> tuple[str, ...]:
    names_by_id = dict(scope.source_names_by_id)
    if names_by_id:
        return tuple(
            names_by_id[source_id]
            for source_id in source_ids
            if names_by_id.get(source_id)
        )
    if len(scope.source_ids) == len(scope.source_names):
        names_by_position = dict(zip(scope.source_ids, scope.source_names))
        return tuple(names_by_position[source_id] for source_id in source_ids)
    return ()


def _slice_project_scope(
    scope: ProjectQueryScope, start: int, stop: int
) -> ProjectQueryScope:
    source_ids = scope.source_ids[start:stop]
    return replace(
        scope,
        source_ids=source_ids,
        source_names=_scope_names_for_ids(scope, source_ids),
        source_names_by_id=tuple(
            (source_id, source_name)
            for source_id, source_name in scope.source_names_by_id
            if source_id in source_ids
        ),
    )


def _source_rejection_error(
    error: NlmError, scope: ProjectQueryScope
) -> bool:
    """Return whether NotebookLM identified a source-specific rejection.

    NotebookLM maps several unrelated provider errors to the same public
    message (``The query request is invalid. Check ... source IDs ...``).
    Treating that generic message as proof that every selected source is bad
    caused the old MCQ path to delete and re-upload healthy files.  Only an
    explicit source/group marker, or a singleton request with a source-specific
    marker, is safe to quarantine.
    """
    message = str(error).casefold()
    if "query request is invalid" in message:
        # The provider's generic hint mentions "source IDs" even when the
        # actual problem is the question text.  It is source-specific only if
        # the error names one of the concrete IDs in this scope.
        return any(source_id.casefold() in message for source_id in scope.source_ids)
    if any(
        marker in message
        for marker in (
            "source group was rejected",
            "source group rejected",
            "one or more source",
        )
    ):
        return True
    if any(source_id.casefold() in message for source_id in scope.source_ids):
        return True
    source_missing_marker = any(
        marker in message
        for marker in (
            "source not found",
            "source unavailable",
            "source is not ready",
        )
    )
    if source_missing_marker:
        return True
    source_id_marker = any(
        marker in message
        for marker in (
            "invalid source",
            "source id",
            "source_ids",
        )
    )
    return source_id_marker and len(scope.source_ids) == 1


def _query_project_scope_with_fallback(
    request: NlmQueryRequest, scope: ProjectQueryScope
) -> list[QueryResult]:
    try:
        return [_query_project_scope(request, scope)]
    except NlmError as error:
        if not _source_rejection_error(error, scope):
            raise
        if len(scope.source_ids) <= 1:
            source_id = scope.source_ids[0]
            source_name = _scope_names_for_ids(scope, (source_id,))
            quarantine = SourceQuarantine(
                notebook_uuid=scope.notebook_uuid,
                source_id=source_id,
                source_name=source_name[0] if source_name else "",
                error=str(error),
            )
            print(
                f"[!] {request.phase_name or 'Query'} quarantined source "
                f"{source_name[0] if source_name else source_id}"
            )
            return [QueryResult(answer="", source_quarantine=(quarantine,))]
    midpoint = len(scope.source_ids) // 2
    print(
        f"[!] {request.phase_name or 'Query'} source group was rejected; "
        "retrying with smaller source groups"
    )
    child_scopes = (
        _slice_project_scope(scope, 0, midpoint),
        _slice_project_scope(scope, midpoint, len(scope.source_ids)),
    )
    return [
        query_result
        for child_scope in child_scopes
        for query_result in _query_project_scope_with_fallback(request, child_scope)
    ]


def _run_nlm_cli_query(request: NlmQueryRequest) -> QueryResult:
    scopes = request.project_scopes
    if not scopes:
        scopes = (
            ProjectQueryScope(
                notebook_uuid=request.notebook.notebook_uuid,
                source_ids=request.source_ids,
                source_names=request.source_names,
            ),
        )
    usable_scopes = tuple(
        _slice_project_scope(scope, start, start + MAX_SOURCE_IDS_PER_QUERY)
        for scope in scopes
        if scope.source_ids
        for start in range(0, len(scope.source_ids), MAX_SOURCE_IDS_PER_QUERY)
    )
    if not usable_scopes:
        raise NlmError(
            f"{request.phase_name or 'Query'} has no approved NotebookLM sources"
        )
    query_results = [
        query_result
        for scope in usable_scopes
        for query_result in _query_project_scope_with_fallback(request, scope)
    ]
    if not any(query_result.answer.strip() for query_result in query_results):
        quarantined = _query_source_quarantine(query_results)
        if quarantined:
            raise NlmError(
                f"{request.phase_name or 'Query'} has no queryable sources after "
                "source quarantine",
                quarantined,
            )
    return _merge_notebook_query_results(query_results, request.phase_name)


def _run_query_once(query: PhaseQuery, query_text: str) -> QueryResult:
    return _run_nlm_cli_query(
        NlmQueryRequest(
            config=query.config,
            notebook=query.notebook,
            query_text=query_text,
            source_ids=query.source_ids,
            source_names=query.source_names,
            notebook_ids=query.notebook_ids,
            phase_name=query.phase_name,
            project_scopes=query.project_scopes,
        )
    )




PhaseValidator = Callable[[QueryResult], list[str]]


def _query_response_errors(
    query_result: QueryResult, validator: PhaseValidator
) -> list[str]:
    if len(query_result.answer) < 50 and not _is_any_empty_sentinel(
        query_result.answer
    ):
        errors = ["response is empty or too short"]
    else:
        errors = []
    lowered_answer = query_result.answer.casefold()
    error_signals = (
        "request failed",
        "error parsing response",
        '"success": false',
        "timed out",
        "traceback (most recent call last)",
    )
    if any(signal in lowered_answer for signal in error_signals):
        errors.append("response contains an error payload")
    return errors + validator(query_result)


def _repair_instructions(validation_errors: list[str]) -> str:
    instruction = (
        "\n\nREPAIR REQUIRED: The previous response was rejected for these reasons: "
        + "; ".join(validation_errors)
        + ". Return the complete section body again and obey every original format rule."
    )
    if any("clinical-case" in error for error in validation_errors):
        instruction += (
            " For every case, prefix every non-empty line with > and use each required "
            "field label exactly once."
        )
    if any("not concise" in error for error in validation_errors):
        instruction += (
            " Limit each Model Answer (Short) to 3-6 one-sentence bullets and at most "
            "900 characters, including all quoted Markdown after that field."
        )
    if any("invalid_badge_format" in error for error in validation_errors):
        instruction += (
            " Rebuild every badge with the canonical bold syntax and only verified "
            "years; do not leave a malformed or unverified provenance badge."
        )
    if any("missing_field" in error for error in validation_errors):
        instruction += (
            " Restore the exact missing question, options, answer, explanation, or "
            "model-answer field required by the phase contract."
        )
    if any("OCR" in error or "joined" in error for error in validation_errors):
        instruction += (
            " Normalize only clearly recoverable OCR damage; join split letters, "
            "separate joined words, and do not guess uncertain medical terms."
        )
    if any("options" in error or "option" in error for error in validation_errors):
        instruction += (
            " Put exactly one option per line in the learned order and make the "
            "Correct Answer label match an existing option."
        )
    if any("IMP stem" in error for error in validation_errors):
        instruction += (
            " Rewrite the IMP stem as a short direct exam question matching the "
            "observed past-exam command pattern; do not use a clinical vignette."
        )
    if any("duplicate_question" in error for error in validation_errors):
        instruction += (
            " Merge only the named exact/OCR-safe duplicate blocks, preserve every "
            "verified year and Source line, and renumber the remaining questions."
        )
    if any("unsafe_duplicate_merge" in error for error in validation_errors):
        instruction += (
            " Review the named blocks as a semantic duplicate candidate. Merge them "
            "only when the requested task, negation, options, answer, and provenance "
            "are equivalent; otherwise keep both blocks and explain the distinction "
            "through their separate evidence."
        )
    if any(
        any(code in error for code in ("missing_source", "unknown_source", "ambiguous_source"))
        for error in validation_errors
    ):
        instruction += (
            " Use only canonical sources from the evidence catalog. Add a Source "
            "line only when the match is unique; stop on ambiguity rather than guessing."
        )
    if any(
        any(code in error for code in ("source_year_mismatch", "missing_supported_year", "unverified_badge_year"))
        for error in validation_errors
    ):
        instruction += (
            " Rebuild the Past Exams badge from the years actually evidenced by the "
            "listed sources, in ascending order; never invent or retain an unsupported year."
        )
    return instruction


def _query_text_for_attempt(query: PhaseQuery, repair_context: str) -> str:
    """Append validation repair guidance without crossing the assessment limit."""
    if query.phase_name not in {"MCQs", "Written Questions"} or not repair_context:
        return query.query_text + repair_context
    suffix = repair_context.strip()
    available = MAX_ASSESSMENT_QUERY_CHARS - len(query.query_text) - 2
    if available <= 0:
        return query.query_text
    if len(suffix) > available:
        suffix = (
            "REPAIR REQUIRED: correct the previous validation errors and return the "
            "complete section body while obeying the original contract."
        )
        suffix = _truncate_query_fragment(suffix, available)
    return f"{query.query_text}\n\n{suffix}"


def _compact_assessment_query_text(query_text: str) -> str:
    """Remove duplicated manifest prose for a final provider-side retry."""
    markers = (
        "Extract every relevant MCQ",
        "Extract every matching Essay",
    )
    body_start = next(
        (query_text.find(marker) for marker in markers if query_text.find(marker) >= 0),
        -1,
    )
    if body_start < 0:
        return query_text
    heading = query_text.splitlines()[0].strip()
    return (
        f"{heading}\n\n"
        "Use only the NotebookLM source IDs selected for this request as evidence. "
        "Copy canonical source names exactly when provenance is required.\n\n"
        + query_text[body_start:]
    )


def _is_generic_query_argument_error(error: NlmError | TimeoutError) -> bool:
    if not isinstance(error, NlmError):
        return False
    message = str(error).casefold()
    return "query request is invalid" in message and not error.source_quarantine


_PHASE_ENGINE: Any | None = None


def set_phase_engine(engine: Any | None) -> None:
    """Choose which backend answers phase prompts. None restores NotebookLM.

    The retry, repair, quarantine and checkpoint machinery below is
    engine-neutral and must stay that way: an engine does the single call, and
    everything that decides whether to call again lives here.
    """
    global _PHASE_ENGINE
    _PHASE_ENGINE = engine


def get_phase_engine() -> Any:
    global _PHASE_ENGINE
    if _PHASE_ENGINE is None:
        from engines import get_phase_engine as _build

        # Hand the engine this module's own single-call function rather than
        # letting it import universal_transcribe for itself. The test suite
        # loads the engine under its own module name, so a fresh import would
        # produce a second copy of it -- and every patch the tests apply would
        # land on the copy nobody is running.
        #
        # Looked up through the module globals on each call, not captured once:
        # rebinding _run_query_once has always taken effect immediately, and
        # caching the reference here would silently break that.
        def _call(query: PhaseQuery, query_text: str) -> QueryResult:
            return _run_query_once(query, query_text)

        _PHASE_ENGINE = _build(runner=_call)
    return _PHASE_ENGINE


def run_phase_query(query: PhaseQuery) -> QueryResult:
    repair_context = ""
    active_query_text = query.query_text
    compact_retry_used = False
    last_errors: list[str] = []
    last_answer = ""
    last_source_names: tuple[str, ...] = ()
    last_source_quarantine: tuple[SourceQuarantine, ...] = ()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            active_query = replace(query, query_text=active_query_text)
            # The engine receives the exact text this attempt should send, so
            # that a backend only has to answer a prompt -- never to work out
            # which prompt it is on.
            dispatch_query = replace(
                active_query,
                query_text=_query_text_for_attempt(active_query, repair_context),
            )
            query_result = get_phase_engine().run_phase(dispatch_query)
            if query.normalizer:
                query_result = query.normalizer(query_result)
            last_answer = query_result.answer
            last_source_names = query_result.source_names
            last_source_quarantine = query_result.source_quarantine
            last_errors = _query_response_errors(query_result, query.validator)
            if not last_errors:
                return query_result
        except (NlmError, TimeoutError) as error:
            last_errors = [str(error)]
            last_source_quarantine = (
                error.source_quarantine if isinstance(error, NlmError) else ()
            )

            # A source quarantine is already a complete, evidence-preserving
            # diagnosis. Repeating the identical NotebookLM request cannot
            # repair the source and only delays the autonomous delete/re-upload
            # recovery performed by the checkpoint runner. Surface it
            # immediately so that recovery starts after the first rejected
            # source-scoped query traversal.
            if last_source_quarantine:
                raise PhaseValidationError(
                    query.phase_name,
                    last_errors,
                    last_answer,
                    last_source_names,
                    last_source_quarantine,
                    attempts=attempt,
                ) from error
            if (
                query.phase_name in {"MCQs", "Written Questions"}
                and not compact_retry_used
                and _is_generic_query_argument_error(error)
            ):
                compacted_query = _compact_assessment_query_text(active_query_text)
                if compacted_query != active_query_text:
                    active_query_text = compacted_query
                    compact_retry_used = True
                    repair_context = ""
                    print(
                        f"[Recovery] {query.phase_name} query arguments rejected; "
                        "retrying with the compact assessment prompt"
                    )
                    continue

        if last_errors and last_answer:
            print(
                f"[Recovery] {query.phase_name} produced raw text with "
                f"{len(last_errors)} validation issue(s) on attempt "
                f"{attempt}/{MAX_ATTEMPTS}; bypassing redundant LLM query "
                "retries for immediate Agent in-flight repair"
            )
            raise PhaseValidationError(
                query.phase_name,
                last_errors,
                last_answer,
                last_source_names,
                last_source_quarantine,
                attempts=attempt,
            )

        if attempt < MAX_ATTEMPTS:
            print(
                f"[!] {query.phase_name} failed on attempt "
                f"{attempt}/{MAX_ATTEMPTS}: "
                + "; ".join(last_errors)
            )
            repair_context = _repair_instructions(last_errors)
            time.sleep(attempt * 5)
    raise PhaseValidationError(
        query.phase_name,
        last_errors,
        last_answer,
        last_source_names,
        last_source_quarantine,
        attempts=MAX_ATTEMPTS,
        exhausted=True,
    )
