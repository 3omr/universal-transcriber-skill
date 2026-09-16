#!/usr/bin/env python3
"""Error types and data records shared across the transcriber engine.

Extracted from universal_transcribe.py as the first step of splitting the
engine: every other module needs these records, so they have to live below
everything else for the imports to stay acyclic. universal_transcribe
re-exports the whole surface, so nothing that imported them from there had to
change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from source_preparation import PreparationReport

# How many times a phase query is retried before the run gives up. Lives here
# because PhaseValidationError reports against it.
MAX_ATTEMPTS = 3


class TranscriberError(RuntimeError):
    """Base error for failures that must not produce a transcript."""


class Phase0Error(TranscriberError):
    """Raised when the source audit cannot establish safe inputs."""


class NlmError(TranscriberError):
    """Raised when the NotebookLM CLI cannot produce a valid result."""

    def __init__(
        self,
        message: str,
        source_quarantine: tuple[SourceQuarantine, ...] = (),
    ) -> None:
        self.source_quarantine = tuple(source_quarantine)
        super().__init__(message)


class ValidationError(TranscriberError):
    """Raised when generated Markdown violates its phase contract."""


class CheckpointError(TranscriberError):
    """Raised when a saved run cannot be safely resumed."""


class PhaseValidationError(ValidationError):
    """A phase failed with its last response preserved for Agent recovery."""

    def __init__(
        self,
        phase_name: str,
        errors: list[str],
        answer: str = "",
        source_names: tuple[str, ...] = (),
        source_quarantine: tuple[SourceQuarantine, ...] = (),
        attempts: int | None = None,
        exhausted: bool = False,
    ) -> None:
        self.phase_name = phase_name
        self.errors = list(errors)
        self.answer = answer
        self.source_names = tuple(source_names)
        self.source_quarantine = tuple(source_quarantine)
        self.attempts = attempts
        self.exhausted = exhausted
        super().__init__(
            f"{phase_name} {self._attempt_summary()}: " + "; ".join(self.errors)
        )

    def _attempt_summary(self) -> str:
        """Say how many NotebookLM queries were actually spent.

        The loop stops at the first usable-but-invalid answer so the Agent can
        repair it in flight, which is usually attempt 1. Reporting the maximum
        every time made a single query look like three.
        """
        if self.attempts is None:
            return "failed"
        if self.exhausted:
            return f"failed after {self.attempts} attempts"
        plural = "attempt" if self.attempts == 1 else "attempts"
        return (
            f"failed on {self.attempts} {plural} of {MAX_ATTEMPTS} "
            "(stopped early for Agent repair)"
        )


@dataclass
class OCRReport:
    path: str
    status: str
    reason: str
    page_count: int = 0
    text_pages: int = 0
    total_characters: int = 0
    sparse_page_ratio: float = 0.0
    garbage_ratio: float = 0.0


@dataclass(frozen=True)
class PDFMetrics:
    page_count: int
    text_pages: int
    total_characters: int
    sparse_page_ratio: float
    garbage_ratio: float


@dataclass
class LocalSource:
    path: str
    relative_path: str
    name: str
    normalized_name: str
    normalized_stem: str
    extension: str
    size: int
    role: str
    years: tuple[int, ...] = ()
    ocr: OCRReport | None = None
    prepared_extension: str = ""
    original_path: str = ""
    original_size: int = 0
    preparation_action: str = "use"
    preparation_status: str = "ready"
    source_sha256: str = ""
    prepared_sha256: str = ""
    years_verified_by_manifest: bool = False

    @property
    def upload_extension(self) -> str:
        return self.prepared_extension or self.extension

    @property
    def is_preparation_planned(self) -> bool:
        return self.preparation_status == "planned"


@dataclass(frozen=True)
class RemoteSource:
    source_id: str
    title: str
    normalized_name: str
    normalized_stem: str
    source_type: str = ""
    notebook_uuid: str = ""
    content_hash: str = ""
    status: str = ""


@dataclass(frozen=True)
class NotebookTarget:
    library_id: str
    notebook_uuid: str
    url: str
    name: str


@dataclass(frozen=True)
class SourceQuarantine:
    notebook_uuid: str
    source_id: str
    source_name: str
    error: str


@dataclass(frozen=True)
class SourceReplacement:
    notebook_uuid: str
    old_source_id: str
    old_source_name: str
    local_path: str
    new_source_id: str
    new_source_name: str


@dataclass
class QueryResult:
    answer: str
    source_names: tuple[str, ...] = ()
    session_id: str | None = None
    source_quarantine: tuple[SourceQuarantine, ...] = ()


@dataclass
class Phase0Report:
    notebook: NotebookTarget
    local_sources: list[LocalSource]
    remote_sources: list[RemoteSource]
    notebooks: tuple[NotebookTarget, ...] = ()
    duplicates: list[LocalSource] = field(default_factory=list)
    ambiguous: list[LocalSource] = field(default_factory=list)
    missing_before_upload: list[LocalSource] = field(default_factory=list)
    unsupported: list[LocalSource] = field(default_factory=list)
    ignored: list[LocalSource] = field(default_factory=list)
    uploaded: list[LocalSource] = field(default_factory=list)
    replacements: list[SourceReplacement] = field(default_factory=list)
    year_map: dict[int, list[str]] = field(default_factory=dict)
    question_banks: list[str] = field(default_factory=list)
    question_bank_links: dict[str, list[str]] = field(default_factory=dict)
    recording_source: str = ""
    recording_sources: tuple[str, ...] = ()
    slide_source: str = ""
    blocking_errors: list[str] = field(default_factory=list)
    preparation: PreparationReport | None = None
    reference_guidance: list[dict[str, Any]] = field(default_factory=list)
    evidence_catalog: list[dict[str, Any]] = field(default_factory=list)
    assessment_sources: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Phase0Request:
    config: dict[str, Any]
    requested_notebook_ids: tuple[str, ...]
    subject: str
    sources_root: str
    lecture_name: str
    recording_sources: tuple[str, ...]
    slides_path: str | None
    approved_uploads: tuple[str, ...] = ()
    agent_reviewed: bool = False
    assessment_sources: tuple[dict[str, Any], ...] = ()
    preparation_manifest: dict[str, Any] | None = None
    prepare_sources: bool = True

    @property
    def requested_notebook_id(self) -> str:
        return self.requested_notebook_ids[0]


@dataclass(frozen=True)
class SourceAuthorityRequest:
    lecture_name: str
    recording_sources: tuple[str, ...]
    slides_path: str | None


@dataclass(frozen=True)
class PhaseQuery:
    config: dict[str, Any]
    notebook: NotebookTarget
    query_text: str
    phase_name: str
    validator: Callable[[QueryResult], list[str]]
    source_ids: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()
    notebook_ids: tuple[str, ...] = ()
    project_scopes: tuple[ProjectQueryScope, ...] = ()
    normalizer: Callable[[QueryResult], QueryResult] | None = None


@dataclass(frozen=True)
class NlmQueryRequest:
    config: dict[str, Any]
    notebook: NotebookTarget
    query_text: str
    source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    notebook_ids: tuple[str, ...] = ()
    phase_name: str = ""
    project_scopes: tuple[ProjectQueryScope, ...] = ()


@dataclass(frozen=True)
class ProjectQueryScope:
    notebook_uuid: str
    source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    source_names_by_id: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QueryScope:
    source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    project_scopes: tuple[ProjectQueryScope, ...] = ()


@dataclass(frozen=True)
class TranscriptIdentity:
    subject: str
    title: str
    emoji: str
    recording_source: str
    source_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedSections:
    guide: str
    imp: str
    mcqs: str
    written: str
    cases: str


@dataclass(frozen=True)
class OutputTarget:
    transcripts_dir: str
    file_name: str
    output_path: str


@dataclass(frozen=True)
class RunRequest:
    subject: str
    notebook_ids: tuple[str, ...]
    lecture_name: str
    recording_sources: tuple[str, ...]
    slides_path: str | None
    sources_root: str
    title: str
    emoji: str
    target: OutputTarget
    audit_only: bool
    approved_uploads: tuple[str, ...] = ()
    agent_reviewed: bool = False
    exam_style_profile: dict[str, Any] = field(default_factory=dict)
    assessment_sources: tuple[dict[str, Any], ...] = ()
    draft_only: bool = False
    finalize_draft: bool = False
    source_manifest: dict[str, Any] | None = None
    resume_run: str | None = None
    resume_latest: bool = False
    retry_phase: str | None = None
    recovery_phase: str | None = None
    recovery_response: str | None = None

    @property
    def notebook_id(self) -> str:
        return self.notebook_ids[0]


@dataclass(frozen=True)
class PipelineContext:
    config: dict[str, Any]
    report: Phase0Report
    identity: TranscriptIdentity
    source_manifest: str
    badge_instructions: str
    verified_years: set[int]
    evidence_sources: list[str]
    guide_scope: QueryScope
    assessment_scope: QueryScope
    exam_style_profile: dict[str, Any] = field(default_factory=dict)
    evidence_catalog: list[dict[str, Any]] = field(default_factory=list)
    assessment_source_scope: QueryScope = field(
        default_factory=lambda: QueryScope((), ())
    )


@dataclass(frozen=True)
class CaseEvidence:
    year_map: dict[int, list[str]]
    evidence_sources: list[str]
    recording_sources: tuple[str, ...]


@dataclass(frozen=True)
class QuestionEvidence:
    year_map: dict[int, list[str]]
    evidence_sources: list[str]
    exam_style_profile: dict[str, Any] = field(default_factory=dict)
    evidence_catalog: list[dict[str, Any]] = field(default_factory=list)
    recording_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionProvenanceContext:
    block: str
    heading_prefix: str
    number: str
    evidence: QuestionEvidence
    badges: tuple[str, ...]


@dataclass(frozen=True)
class UploadOutcome:
    remote_sources: list[RemoteSource]
    uploaded_by_run: bool


@dataclass(frozen=True)
class PhaseCheckpointUpdate:
    run_dir: Path
    checkpoint: dict[str, Any]
    phase: str
    status: str
    answer: str = ""
    errors: tuple[str, ...] = ()
    source_quarantine: tuple[SourceQuarantine, ...] = ()


@dataclass(frozen=True)
class RecoveryBundle:
    run_dir: Path
    phase: str
    answer: str
    errors: tuple[str, ...]
    checkpoint: dict[str, Any]
    source_names: tuple[str, ...] = ()
    source_quarantine: tuple[SourceQuarantine, ...] = ()


@dataclass(frozen=True)
class TranscriptSaveRequest:
    identity: TranscriptIdentity
    sections: GeneratedSections
    target: OutputTarget
    verified_years: set[int]
    exam_style_profile: dict[str, Any]
    evidence_catalog: list[dict[str, Any]]
