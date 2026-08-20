#!/usr/bin/env python3
"""Data models, constants, and custom exceptions for Universal Medical Transcriber."""

from __future__ import annotations

import fcntl
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator


# --- Timeouts & Limits ---
NLM_QUERY_TIMEOUT_SECONDS = 205
MAX_SOURCE_IDS_PER_QUERY = 3
MAX_ASSESSMENT_CONTEXT_CHARS = 900
MAX_ASSESSMENT_QUERY_CHARS = 4000
MAX_ASSESSMENT_STYLE_CHARS = 750
MAX_ATTEMPTS = 3
MAX_SOURCE_REPLACEMENT_ROUNDS = 1
LARGE_SOURCE_BYTES = 80 * 1024 * 1024
UPLOAD_POLL_SECONDS = 10
UPLOAD_POLL_ATTEMPTS = 6
LARGE_UPLOAD_POLL_ATTEMPTS = 36
SOURCE_DELETE_POLL_SECONDS = 2
SOURCE_DELETE_POLL_ATTEMPTS = 15
MIN_REASONABLE_EXAM_YEAR = 2000

# --- Versions ---
PROMPT_VERSION = "2026-08-12-question-recovery-v2"
ASSESSMENT_PROMPT_VERSION = "2026-08-17-scope-filter-v1"
VALIDATOR_VERSION = "2026-08-12-dynamic-years-v2"

# --- Phase Definitions ---
PHASE_ORDER = ("guide", "imp", "mcqs", "written", "cases")
PHASE_LABELS = {
    "guide": "Chronological Guide",
    "imp": "IMP Points",
    "mcqs": "MCQs",
    "written": "Written Questions",
    "cases": "Clinical Cases",
}
PHASE_SUCCESS_STATUSES = {"validated", "repaired"}

# --- Document Structure Constants ---
SECTION_HEADINGS = (
    "## 📖 Chronological Guide",
    "## 🌟 IMP Points",
    "## ❓ MCQs",
    "## ✍️ Written Questions",
    "## 🩺 Clinical Cases",
)
IMP_HEADINGS = (
    "#### 1. 📌 Doctor's Spoken Pearls",
    "#### 2. ⚠️ Diagnostic Traps",
    "#### 3. 🛑 Lethal Mistakes",
    "#### 4. ❓ Interactive Doctor Questions",
    "#### 5. 📋 Exam Rules",
)
ALLOWED_CALLOUTS = {"NOTE", "IMPORTANT", "WARNING", "CAUTION", "TIP"}
QUESTION_OPTION_KEYS = ("a", "b", "c", "d")
NO_MCQS = "NO_GROUNDED_MCQS"
NO_WRITTEN = "NO_GROUNDED_WRITTEN_QUESTIONS"

EDITORIAL_REVIEW_MARKERS = (
    "NEEDS_SOURCE_REVIEW",
    "NEEDS_OCR_REVIEW",
    "UNRESOLVED_CONFLICT",
)

# --- File Extensions ---
RECORDING_EXTENSIONS = {
    ".m4a",
    ".mp3",
    ".wav",
    ".aac",
    ".mp4",
    ".mkv",
    ".ogg",
    ".webm",
    ".avi",
    ".mov",
}
NLM_RECORDING_UPLOAD_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".ogg"}
SLIDE_EXTENSIONS = {".ppt", ".pptx", ".pps", ".ppsx"}
DOCUMENT_UPLOAD_EXTENSIONS = {
    ".pdf",
    ".pptx",
    ".docx",
}
NLM_UPLOAD_EXTENSIONS = DOCUMENT_UPLOAD_EXTENSIONS | NLM_RECORDING_UPLOAD_EXTENSIONS

# --- Regex Patterns & Translations ---
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
BROKEN_OCR_TOKEN_PATTERN = re.compile(r"\b(?:[A-Za-z]{1,3}\s+){4,}[A-Za-z]{1,3}\b")
JOINED_COMMON_WORD_PATTERN = re.compile(
    r"\b[A-Za-z]{3,}(?:of|the|and|are|from|with|except)[A-Za-z]{3,}\b",
    flags=re.IGNORECASE,
)
NOTEBOOK_CITATION_PATTERN = re.compile(
    r"\[\s*\d+(?:\s*[,،、;\-–—]\s*\d+)*\s*\]"
)
BADGE_LIKE_PATTERN = re.compile(
    r"\*{0,2}\[(?:IMP|Question Bank|Past Exams[^\]]*|Past year from doctor[^\]]*)\]\*{0,2}",
    flags=re.IGNORECASE,
)


MEDICAL_OCR_ALLOWLIST = frozenset({
    "amphetamine",
    "amfetamine",
    "anaesthesia",
    "anesthesia",
    "catheter",
    "chemotherapy",
    "chlorpromazine",
    "deferoxamine",
    "dexamethasone",
    "diethylcarbamazine",
    "dimercaprol",
    "erythema",
    "hyperthermia",
    "hypothermia",
    "hypothalamus",
    "methane",
    "neostigmine",
    "noradrenaline",
    "physostigmine",
    "polyurethane",
    "pralidoxime",
    "promethazine",
    "pyridostigmine",
    "quadrant",
    "radiotherapy",
    "succimer",
})


# --- Custom Exceptions ---
class TranscriberError(Exception):
    """Base exception for all universal transcriber failures."""


class ManifestError(TranscriberError):
    """Raised when the lecture manifest is invalid."""


class ValidationError(TranscriberError):
    """Raised when generated Markdown fails contract validation."""


class PhaseValidationError(ValidationError):
    """Raised when a specific phase output fails contract validation."""

    def __init__(
        self,
        phase: str,
        errors: list[str],
        answer: str = "",
        source_names: tuple[str, ...] = (),
        source_quarantine: tuple[str, ...] = (),
    ) -> None:
        self.phase = phase
        self.errors = list(errors)
        self.answer = answer
        self.source_names = tuple(source_names)
        self.source_quarantine = tuple(source_quarantine)
        joined_errors = "; ".join(self.errors) if self.errors else "validation failed"
        label = PHASE_LABELS.get(phase, phase)
        super().__init__(f"{label} failed validation: {joined_errors}")


class NlmError(TranscriberError):
    """Raised when a NotebookLM CLI interaction fails."""

    def __init__(
        self,
        message: str,
        returncode: int = 1,
        source_quarantine: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.source_quarantine = tuple(source_quarantine)


class PreparationError(TranscriberError):
    """Raised when source preflight checks or conversions fail."""


# --- Data Classes ---
@dataclass(frozen=True)
class RecordingSource:
    path: str
    action: str = "auto"
    role: str = "recording"


@dataclass(frozen=True)
class SlideSource:
    path: str
    action: str = "auto"
    role: str = "slides"


@dataclass(frozen=True)
class AssessmentSource:
    path: str
    source_type: str
    year: int | None = None
    action: str = "auto"
    links: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExamStyleProfile:
    mcq: dict[str, Any] = field(default_factory=dict)
    written: dict[str, Any] = field(default_factory=dict)
    cases: dict[str, Any] = field(default_factory=dict)
    sample_scope: str | None = None


@dataclass(frozen=True)
class Manifest:
    title: str
    recording_sources: tuple[RecordingSource, ...]
    slides: SlideSource
    assessment_sources: tuple[AssessmentSource, ...] = field(default_factory=tuple)
    exam_style_profile: ExamStyleProfile | None = None
    notebook_id: str | None = None
    notebook_profile: str | None = None
    subject: str = "Medical Transcription"
    language: str = "Egyptian Arabic mixed with English medical terminology"
    emoji: str = "🩺"
    extra_sources: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TranscriptIdentity:
    title: str
    subject: str
    language: str
    emoji: str


@dataclass(frozen=True)
class OutputTarget:
    output_path: str
    index_path: str
    file_name: str


@dataclass(frozen=True)
class QuestionEvidence:
    catalog_year_map: dict[str, int]
    catalog_names: set[str]
    evidence_catalog: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class PhaseCheckpointUpdate:
    run_dir: Path
    checkpoint: dict[str, Any]
    phase: str
    status: str
    answer: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)
    source_quarantine: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RecoveryBundle:
    run_dir: Path
    phase: str
    answer: str
    errors: tuple[str, ...]
    checkpoint: dict[str, Any]
    source_names: tuple[str, ...] = field(default_factory=tuple)
    source_quarantine: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PipelineContext:
    manifest: Manifest
    identity: TranscriptIdentity
    target: OutputTarget
    notebook_ids: list[str]
    evidence_catalog: list[dict[str, Any]]
    run_cache_dir: Path
    profile: str | None = None
    dry_run: bool = False
    draft_only: bool = False
    force: bool = False


@dataclass(frozen=True)
class RunRequest:
    manifest_path: Path
    sources_root: Path
    dest_path: Path
    notebooks: list[str]
    profile: str | None = None
    subject: str = "Medical Transcription"
    language: str = "Egyptian Arabic mixed with English medical terminology"
    emoji: str = "🩺"
    dry_run: bool = False
    draft_only: bool = False
    force: bool = False
    interactive: bool = False
    resume_run: str | None = None
    resume_latest: bool = False
    retry_phase: str | None = None
    recovery_phase: str | None = None
    recovery_response: Path | None = None
    source_manifest: dict[str, Any] | None = None


@dataclass(frozen=True)
class GeneratedSections:
    guide: str
    imp: str
    mcqs: str
    written: str
    cases: str


@dataclass(frozen=True)
class PhaseCandidate:
    answer: str
    source_names: tuple[str, ...] = field(default_factory=tuple)
    source_quarantine: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QueryResult:
    answer: str
    source_quarantine: tuple[str, ...] = field(default_factory=tuple)


# --- Helper Utilities ---
@contextmanager
def exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def is_reasonable_exam_year(year: int) -> bool:
    return MIN_REASONABLE_EXAM_YEAR <= year <= date.today().year + 1
