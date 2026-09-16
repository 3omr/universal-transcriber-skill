#!/usr/bin/env python3
"""Resolve one module and run its NotebookLM transcription pipeline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # imported lazily at runtime, so only the checker sees it
    from question_bank import BankQuestion, QuestionBank

from console import configure_console_streams
from file_lock import exclusive_file_lock
from module_registry import (
    ModuleConfig,
    ModuleConfigError,
    configured_slide,
    discover_modules,
    resolve_module,
)
from version_checker import (
    __version__,
    print_update_notice_if_available,
)

# Configure the console at import, not just in main(). Every print() in this
# module can carry Arabic or an emoji filename, and callers that import it as a
# library -- the test suite, an embedding agent -- never reach main() to have
# the streams fixed for them. On a cp1252 Windows console those calls raise
# UnicodeEncodeError; on POSIX this is a no-op.
configure_console_streams()

# Wall-clock ceilings for the engine subprocess. The engine checkpoints every
# phase, so a run stopped at the ceiling resumes with --resume-latest rather than
# starting over. Generous by design: five NotebookLM phases plus OCR and slide
# conversion are legitimately slow.
AUDIT_TIMEOUT_SECONDS = 30 * 60
TRANSCRIPTION_TIMEOUT_SECONDS = 4 * 60 * 60


class LauncherError(RuntimeError):
    """Raised when automatic discovery cannot make one safe choice."""


@dataclass(frozen=True)
class RecordingSelection:
    engine: ModuleType
    recordings: list[Any]
    transcripts_dir: Path
    requested: str | None
    run_all: bool


@dataclass(frozen=True)
class EngineInvocation:
    engine_path: Path
    module: ModuleConfig
    notebook_ids: tuple[str, ...]
    recording: Any
    slides_path: Path | None
    additional_recordings: tuple[Any, ...] = ()
    approved_uploads: tuple[str, ...] = ()
    title: str | None = None
    exam_style_profile: dict[str, Any] | None = None
    assessment_sources: tuple[dict[str, Any], ...] = ()
    assessment_manifest_provided: bool = False
    draft_only: bool = False
    finalize_draft: bool = False
    source_manifest_path: str | None = None
    resume_run: str | None = None
    resume_latest: bool = False
    retry_phase: str | None = None
    recovery_phase: str | None = None
    recovery_response: str | None = None


@dataclass(frozen=True)
class SourceManifest:
    title: str
    recording_sources: tuple[str, ...]
    slides: str | None
    approved_uploads: tuple[str, ...]
    exam_style_profile: dict[str, Any]
    slides_action: str = "auto"
    assessment_sources: tuple[dict[str, Any], ...] = ()
    references: tuple[dict[str, Any], ...] = ()
    manifest_path: str | None = None


@dataclass(frozen=True)
class LauncherContext:
    engine_path: Path
    engine: ModuleType
    config: dict[str, Any]
    module: ModuleConfig
    notebooks: tuple[Any, ...]

    @property
    def notebook(self) -> Any:
        return self.notebooks[0]


# .agents/skills is a generated mirror of skills/ (scripts/sync-agents-mirror.sh).
# Running the mirror instead of the source means edits appear to do nothing.
GENERATED_TREE_PARTS = frozenset({".git", ".agents", "__pycache__"})


def _engine_path(workspace: Path) -> Path:
    local_engine = Path(__file__).resolve().parent / "universal_transcribe.py"
    if local_engine.is_file():
        return local_engine
    matches = sorted(
        path
        for path in workspace.rglob("universal_transcribe.py")
        if not GENERATED_TREE_PARTS.intersection(path.parts)
    )
    if not matches:
        raise LauncherError(
            f"Could not find universal_transcribe.py under {workspace}"
        )
    if len(matches) > 1:
        print(
            "[!] Several engine copies found; using "
            f"{matches[0]}. Others: "
            + ", ".join(str(path) for path in matches[1:]),
            file=sys.stderr,
        )
    return matches[0]


def _load_engine(engine_path: Path) -> ModuleType:
    # The launcher loads the engine by file path, so Python does not otherwise
    # know the repository root or the sibling runtime package.
    for import_root in (engine_path.parent, engine_path.parent.parent):
        import_root_text = str(import_root)
        if import_root_text not in sys.path:
            sys.path.insert(0, import_root_text)
    module_spec = importlib.util.spec_from_file_location(
        "universal_transcriber_launcher_engine", engine_path
    )
    if module_spec is None or module_spec.loader is None:
        raise LauncherError(f"Could not load engine: {engine_path}")
    engine = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = engine
    module_spec.loader.exec_module(engine)
    return engine


def _module_config_for_engine(
    engine_config: dict[str, Any], module: ModuleConfig
) -> dict[str, Any]:
    config = dict(engine_config)
    if module.notebook.profile:
        config["nlm_profile"] = module.notebook.profile
    config["default_subject"] = module.display_name
    return config


def _resolved_notebooks(
    engine: ModuleType, config: dict[str, Any], module: ModuleConfig
) -> tuple[Any, ...]:
    resolved: list[Any] = []
    for notebook in module.notebook.notebooks:
        notebook_reference = notebook.notebook_id or notebook.title
        try:
            target = engine.resolve_notebook(
                config, notebook_reference, notebook.title or module.display_name
            )
        except engine.TranscriberError as error:
            raise LauncherError(f"Could not resolve module notebook: {error}") from error
        if target.notebook_uuid not in {item.notebook_uuid for item in resolved}:
            resolved.append(target)
    return tuple(resolved)


def _launcher_context(args: argparse.Namespace) -> LauncherContext:
    workspace = Path(args.workspace).expanduser().resolve()
    modules = discover_modules(workspace, args.modules_root)
    module = resolve_module(modules, args.module)
    engine_path = _engine_path(workspace)
    engine = _load_engine(engine_path)
    config = _module_config_for_engine(engine.load_config(), module)
    notebooks = _resolved_notebooks(engine, config, module)
    return LauncherContext(engine_path, engine, config, module, notebooks)


def _recordings(
    engine: ModuleType, notebook_uuids: tuple[str, ...], config: dict[str, Any]
) -> list[Any]:
    remote_sources: list[Any] = []
    try:
        for notebook_uuid in notebook_uuids:
            remote_sources.extend(engine.list_remote_sources(notebook_uuid, config))
    except engine.TranscriberError as error:
        raise LauncherError(f"Could not read NotebookLM sources: {error}") from error
    seen: set[tuple[str, str]] = set()
    unique_sources: list[Any] = []
    for source in remote_sources:
        source_key = (source.notebook_uuid, source.normalized_name)
        if source_key in seen:
            continue
        seen.add(source_key)
        unique_sources.append(source)
    remote_sources = unique_sources
    recordings = [
        source
        for source in remote_sources
        if engine._remote_role_matches(source, "recording")
    ]
    if not recordings:
        raise LauncherError("No audio/video recording sources exist in the notebook")
    return recordings


def _transcript_stems(engine: ModuleType, transcripts_dir: Path) -> set[str]:
    if not transcripts_dir.is_dir():
        return set()
    return {
        engine.normalize_source_stem(path.name)
        for path in transcripts_dir.glob("*.md")
        if path.name.casefold() != "index.md"
    }


def _pending_recordings(
    engine: ModuleType, recordings: list[Any], transcripts_dir: Path
) -> list[Any]:
    completed_stems = _transcript_stems(engine, transcripts_dir)
    return [
        recording
        for recording in recordings
        if recording.normalized_stem not in completed_stems
    ]


def _requested_recording(
    engine: ModuleType, recordings: list[Any], requested: str
) -> Any:
    requested_name = engine.normalize_source_key(requested)
    exact = [source for source in recordings if source.normalized_name == requested_name]
    matches = exact or [
        source
        for source in recordings
        if source.normalized_stem == engine.normalize_source_stem(requested)
    ]
    if len(matches) != 1:
        raise LauncherError(
            f"Recording '{requested}' did not resolve uniquely in NotebookLM"
        )
    return matches[0]


def _selected_recordings(selection: RecordingSelection) -> list[Any]:
    if selection.requested:
        return [
            _requested_recording(
                selection.engine, selection.recordings, selection.requested
            )
        ]
    pending = _pending_recordings(
        selection.engine, selection.recordings, selection.transcripts_dir
    )
    if selection.run_all or len(pending) <= 1:
        return pending
    names = "\n".join(f"- {source.title}" for source in pending)
    raise LauncherError(
        "Multiple pending recordings were found. Name one or pass --all:\n" + names
    )


def _topic_tokens(engine: ModuleType, source_name: str) -> set[str]:
    ignored = {"lecture", "recording", "poison", "poisons", "poisoning", "dr"}
    return {
        token
        for token in engine.normalize_source_stem(source_name).split()
        if len(token) >= 3 and token not in ignored
    }


def _matching_slides(
    engine: ModuleType, source_root: Path, recording_title: str
) -> Path | None:
    lecture_dir = source_root / "Lecture"
    if not lecture_dir.is_dir():
        return None
    recording_tokens = _topic_tokens(engine, recording_title)
    candidates = [
        source_file
        for source_file in lecture_dir.iterdir()
        if source_file.is_file()
        and source_file.suffix.lower() in {*engine.SLIDE_EXTENSIONS, ".pdf"}
        and "book" not in source_file.stem.lower()
    ]
    scored = [
        (len(recording_tokens & _topic_tokens(engine, source.name)), source)
        for source in candidates
    ]
    best_score = max((score for score, _source in scored), default=0)
    matches = [source for score, source in scored if score == best_score and score > 0]
    return matches[0] if len(matches) == 1 else None


def _requested_slides(requested: str, module: ModuleConfig) -> Path:
    requested_path = Path(requested).expanduser()
    slide_path = (
        requested_path.resolve()
        if requested_path.is_absolute()
        else (module.paths.root / requested_path).resolve()
    )
    try:
        slide_path.relative_to(module.paths.root.resolve())
    except ValueError as error:
        raise LauncherError(
            "Manifest slides must stay inside the selected module"
        ) from error
    if not slide_path.is_file():
        raise LauncherError(f"Slides file not found: {slide_path}")
    return slide_path


def _slides_path(
    requested: str | None,
    context: LauncherContext,
    recording_title: str,
) -> Path | None:
    if requested:
        return _requested_slides(requested, context.module)
    try:
        mapped = configured_slide(context.module, recording_title)
    except ModuleConfigError as error:
        raise LauncherError(str(error)) from error
    return mapped or _matching_slides(
        context.engine, context.module.paths.root, recording_title
    )


def _engine_command(invocation: EngineInvocation) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(invocation.engine_path),
        "--subject",
        invocation.module.display_name,
        "--emoji",
        invocation.module.emoji,
        "--lecture",
        invocation.title or invocation.recording.title,
        "--recording-source",
        invocation.recording.title,
        "--sources-root",
        str(invocation.module.paths.root),
        "--output-dir",
        str(invocation.module.paths.transcripts),
    ]
    for notebook_id in invocation.notebook_ids:
        command.extend(["--notebook-id", notebook_id])
    if invocation.slides_path:
        command.extend(["--pptx", str(invocation.slides_path)])
    for recording in invocation.additional_recordings:
        command.extend(["--recording-source", recording.title])
    for source_name in invocation.approved_uploads:
        command.extend(["--approved-upload", source_name])
    if invocation.exam_style_profile:
        command.extend(
            [
                "--exam-style-profile",
                json.dumps(
                    invocation.exam_style_profile,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ]
        )
    if invocation.assessment_manifest_provided:
        command.extend(
            [
                "--assessment-manifest",
                json.dumps(
                    invocation.assessment_sources,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ]
        )
    if invocation.source_manifest_path:
        command.extend(["--source-manifest", invocation.source_manifest_path])
    if invocation.title:
        command.append("--agent-reviewed")
    if invocation.draft_only:
        command.append("--draft-only")
    if invocation.finalize_draft:
        command.append("--finalize-draft")
    if invocation.resume_run:
        command.extend(["--resume-run", invocation.resume_run])
    if invocation.resume_latest:
        command.append("--resume-latest")
    if invocation.retry_phase:
        command.extend(["--retry-phase", invocation.retry_phase])
    if invocation.recovery_phase:
        command.extend(["--recovery-phase", invocation.recovery_phase])
    if invocation.recovery_response:
        command.extend(["--recovery-response", invocation.recovery_response])
    if invocation.module.notebook.profile:
        command.extend(["--nlm-profile", invocation.module.notebook.profile])
    return command


def _read_source_manifest(path: str) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise LauncherError(f"Source manifest not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LauncherError(f"Source manifest is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise LauncherError("Source manifest must be a JSON object")
    return payload


def _manifest_recording_names(payload: dict[str, Any]) -> tuple[str, ...]:
    recordings = payload.get("recording_sources")
    if not isinstance(recordings, list) or not recordings or not all(
        isinstance(item, (str, dict)) and _manifest_source_name(item) for item in recordings
    ):
        raise LauncherError(
            "Source manifest requires a non-empty recording_sources list"
        )
    names = [_manifest_source_name(item) for item in recordings]
    normalized_recordings = [item.casefold().strip() for item in names]
    if len(set(normalized_recordings)) != len(normalized_recordings):
        raise LauncherError("Source manifest cannot repeat a recording source")
    return tuple(item.strip() for item in names)


def _manifest_source_name(item: str | dict[str, Any]) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in ("source", "path", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _manifest_upload_names(payload: dict[str, Any]) -> tuple[str, ...]:
    uploads = payload.get("approved_uploads", [])
    if not isinstance(uploads, list) or not all(
        isinstance(item, str) and item.strip() for item in uploads
    ):
        raise LauncherError("Source manifest approved_uploads must be a string list")
    normalized_uploads: list[str] = []
    for item in uploads:
        candidate = item.strip().replace("\\", "/")
        if (
            candidate.startswith("/")
            or candidate == ".."
            or candidate.startswith("../")
            or "/../" in candidate
        ):
            raise LauncherError("Approved upload paths must stay inside the module")
        if "/" in candidate and not candidate.startswith(("Lecture/", "Questions/")):
            raise LauncherError(
                "Approved upload paths must start with Lecture/ or Questions/"
            )
        normalized_uploads.append(candidate.casefold())
    if len(set(normalized_uploads)) != len(normalized_uploads):
        raise LauncherError("Source manifest cannot repeat an approved upload")
    return tuple(item.strip() for item in uploads)


def _manifest_exam_style_profile(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.get("exam_style_profile")
    if not isinstance(profile, dict) or not profile:
        raise LauncherError(
            "Source manifest requires a non-empty exam_style_profile object"
        )
    try:
        serialized = json.dumps(profile, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise LauncherError(
            "Source manifest exam_style_profile must be JSON data"
        ) from error
    if len(serialized) > 20_000:
        raise LauncherError("Source manifest exam_style_profile is too large")
    return profile


def _manifest_assessment_sources(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    sources = payload.get("assessment_sources", [])
    if not isinstance(sources, list) or not all(
        isinstance(source, dict) for source in sources
    ):
        raise LauncherError("Source manifest assessment_sources must be an object list")
    normalized: list[dict[str, Any]] = []
    paths: set[str] = set()
    for source in sources:
        path = str(source.get("path", "")).strip()
        source_type = str(source.get("type", "")).strip()
        if not path or source_type not in {"past_exam", "question_bank", "ignore"}:
            raise LauncherError(
                "Each assessment source requires path and type "
                "(past_exam, question_bank, or ignore)"
            )
        has_year = source.get("year") is not None
        has_years = source.get("years") is not None
        if source_type == "past_exam" and not (has_year or has_years):
            raise LauncherError(
                f"Past exam manifest entry requires year or years: {path}"
            )
        if source_type != "past_exam" and (has_year or has_years):
            raise LauncherError(
                f"Only past_exam entries may declare year or years: {path}"
            )
        if has_year and has_years:
            single = {str(source.get("year")).strip()}
            raw_years = source.get("years")
            if not isinstance(raw_years, list):
                raw_years = [raw_years]
            multiple = {
                str(value).strip()
                for value in raw_years
            }
            if single != multiple:
                raise LauncherError(
                    f"Manifest year and years conflict for assessment source: {path}"
                )
        normalized_path = os.path.normpath(path)
        if normalized_path.startswith("..") or not (
            normalized_path == "Questions"
            or normalized_path.startswith("Questions" + os.sep)
        ):
            raise LauncherError("Manifest assessment paths must stay under Questions/")
        if normalized_path.casefold() in paths:
            raise LauncherError(f"Source manifest repeats assessment source: {path}")
        paths.add(normalized_path.casefold())
        normalized.append({**source, "path": normalized_path, "type": source_type})
    return tuple(normalized)


def _manifest_references(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    references = payload.get("references", [])
    if not isinstance(references, list) or not all(
        isinstance(reference, dict) for reference in references
    ):
        raise LauncherError("Source manifest references must be an object list")
    normalized: list[dict[str, Any]] = []
    paths: set[str] = set()
    for reference in references:
        path = _manifest_source_name(reference)
        if not path:
            raise LauncherError("Each reference requires a path or source")
        normalized_path = os.path.normpath(path)
        if normalized_path.startswith("..") or not normalized_path.startswith(
            ("Lecture" + os.sep, "Questions" + os.sep)
        ):
            raise LauncherError("Manifest reference paths must stay under Lecture/ or Questions/")
        key = normalized_path.casefold()
        if key in paths:
            raise LauncherError(f"Source manifest repeats reference: {path}")
        paths.add(key)
        normalized.append({**reference, "path": normalized_path})
    return tuple(normalized)


TOPIC_SYNONYMS: dict[str, str] = {
    "metal": "معادن",
    "heavy metal": "معادن",
    "heavy metals": "معادن",
    "metals": "معادن",
    "lead": "معادن",
    "arsenic": "معادن",
    "mercury": "معادن",
    "paracetamol": "paracetamol",
    "panadol": "paracetamol",
    "acetaminophen": "paracetamol",
    "corrosive": "corrosives",
    "corrosives": "corrosives",
    "acid": "corrosives",
    "alkali": "corrosives",
    "addiction": "addication",
    "dependence": "addication",
    "narcotic": "addication",
    "volatile": "kerosin",
    "hydrocarbon": "kerosin",
    "kerosene": "kerosin",
    "alcohol": "alcohol",
    "gas": "gaseous",
    "gaseous": "gaseous",
    "carbon monoxide": "gaseous",
    "snake": "animal",
    "scorpion": "animal",
    "viper": "animal",
    "plant": "plant",
    "atropine": "plant",
    "food": "food poisoning",
    "botulism": "food poisoning",
    "favism": "food poisoning",
    "psychotropic": "psychotropic",
    "antidepressant": "psychotropic",
    "salicylate": "salicylic",
    "salicylates": "salicylic",
    "salycylates": "salicylic",
    "aspirin": "salicylic",
    "acetyl salicylic": "salicylic",
    "acetyl salysilic": "salicylic",
    "أسبرين": "salicylic",
}


def _warn_remote_discovery(reason: str) -> None:
    print(
        f"[!] --auto-manifest remote discovery failed ({reason}); the manifest "
        "may be missing sources. Check `nlm auth` and the notebook id.",
        file=sys.stderr,
        flush=True,
    )


def generate_auto_manifest(
    module_root: Path, lecture_query: str, nlm_executable: str = "nlm"
) -> Path:
    lecture_dir = module_root / "Lecture"
    questions_dir = module_root / "Questions"
    query_clean = lecture_query.strip()
    query_stem = re.sub(
        r"\.(mp3|m4a|wav|aac|ogg|pdf|pptx|ppsx)$", "", query_clean, flags=re.IGNORECASE
    ).strip()
    query_tokens = [
        tok
        for tok in re.findall(r"\w+", query_stem.casefold())
        if len(tok) > 1 and tok not in {"د", "دكتور", "dr", "part", "lecture", "1", "2", "3"}
    ]

    synonym_targets: list[str] = []
    for tok in query_tokens:
        if tok in TOPIC_SYNONYMS:
            synonym_targets.append(TOPIC_SYNONYMS[tok].casefold())
    for phrase, target in TOPIC_SYNONYMS.items():
        if phrase in query_stem.casefold():
            synonym_targets.append(target.casefold())

    slide_files: list[Path] = []
    book_files: list[Path] = []
    audio_files: list[Path] = []
    if lecture_dir.is_dir():
        for item in sorted(lecture_dir.iterdir()):
            if item.name.startswith("."):
                continue
            suffix = item.suffix.lower()
            if suffix in {".pptx", ".pdf", ".ppsx", ".ppt", ".docx"}:
                if item.stem.casefold() in {"book", "textbook", "reference"}:
                    book_files.append(item)
                else:
                    slide_files.append(item)
            elif suffix in {".mp3", ".m4a", ".wav", ".aac", ".ogg"}:
                audio_files.append(item)

    def score_match(name: str) -> int:
        name_lower = name.casefold()
        score = 0
        if query_stem.casefold() in name_lower or name_lower in query_stem.casefold():
            score += 50
        for tok in query_tokens:
            if tok in name_lower or name_lower in tok:
                score += 20
            elif len(tok) >= 4 and (tok[:4] in name_lower or name_lower[:4] in tok):
                score += 15
        for syn in synonym_targets:
            if syn in name_lower or name_lower in syn:
                score += 30
            elif len(syn) >= 4 and (syn[:4] in name_lower or name_lower[:4] in syn):
                score += 25
        return score

    best_slide = None
    best_slide_score = 0
    for slide in slide_files:
        score = score_match(slide.name)
        if score > best_slide_score:
            best_slide_score = score
            best_slide = slide

    # Fallback to book file if no specific slide was matched
    if not best_slide and book_files:
        best_slide = book_files[0]

    matched_audio: list[str] = []
    if query_clean.lower().endswith((".mp3", ".m4a", ".wav", ".aac", ".ogg")):
        matched_audio = [query_clean]
    else:
        for audio in audio_files:
            if score_match(audio.name) > 0:
                matched_audio.append(audio.name)

    if not matched_audio and len(audio_files) == 1:
        # One recording in Lecture/ is unambiguous whatever it is called. This
        # is the Arabic case: "مبيد حشرى.m4a" shares no token with "OPs", so
        # scoring can never match it.
        matched_audio = [audio_files[0].name]
        print(
            f"[Launcher] No name match for '{query_clean}'; using the only "
            f"recording in Lecture/: {audio_files[0].name}"
        )
    elif not matched_audio and audio_files:
        candidates = "\n".join(f"  - {audio.name}" for audio in sorted(
            audio_files, key=lambda path: path.name
        ))
        raise LauncherError(
            f"--auto-manifest could not match a recording for '{query_clean}'.\n"
            f"Lecture/ holds {len(audio_files)} recordings:\n{candidates}\n"
            "Rerun with the exact filename, or write the manifest by hand."
        )

    slide_path = f"Lecture/{best_slide.name}" if best_slide else f"Lecture/{query_stem}.pdf"

    assessment_sources: list[dict[str, Any]] = []
    if questions_dir.is_dir():
        for item in sorted(questions_dir.iterdir()):
            if item.name.startswith(".") or item.suffix.lower() not in {".pdf", ".txt", ".docx"}:
                continue
            years = [int(match) for match in re.findall(r"(20[12]\d)", item.name)]
            if not years:
                short_years = [int(m) for m in re.findall(r"(?:^|[^0-9])([12]\d)(?:[^0-9]|$)", item.name)]
                for sy in short_years:
                    if 18 <= sy <= 30:
                        years.append(2000 + sy)
            if years:
                assessment_sources.append({
                    "path": f"Questions/{item.name}",
                    "type": "past_exam",
                    "year": max(years),
                    "action": "auto",
                })
            else:
                assessment_sources.append({
                    "path": f"Questions/{item.name}",
                    "type": "question_bank",
                    "action": "auto",
                })

    # If local assessment or audio/slide sources are empty, attempt remote discovery via module.json
    slides_action = "auto"
    module_json_path = module_root / "module.json"
    if (not assessment_sources or not audio_files or not slide_files) and module_json_path.is_file():
        try:  # noqa: PLR1702 - remote discovery is one cohesive best-effort block
            with open(module_json_path, encoding="utf-8") as f:
                mod_meta = json.load(f)
            notebooks = mod_meta.get("notebooks") or []
            if not notebooks and "notebook" in mod_meta:
                nb_obj = mod_meta["notebook"]
                notebooks = [nb_obj] if isinstance(nb_obj, dict) else [{"id": nb_obj}]
            if notebooks:
                nb_id = str(notebooks[0].get("id"))
                nb_profile = mod_meta.get("notebook_profile")
                nlm_cmd = [nlm_executable, "source", "list", nb_id, "--json"]
                if nb_profile:
                    nlm_cmd.extend(["--profile", str(nb_profile)])
                proc = subprocess.run(nlm_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                if proc.returncode == 0:
                    remote_list = json.loads(proc.stdout)
                    if isinstance(remote_list, dict):
                        remote_list = remote_list.get("sources") or []
                    if isinstance(remote_list, list):
                        need_remote_assessments = not assessment_sources
                        need_remote_audio = not audio_files or matched_audio == [f"{query_stem}.mp3"]
                        need_remote_slides = not best_slide
                        for r_src in remote_list:
                            r_title = str(r_src.get("title") or r_src.get("name") or "").strip()
                            if not r_title:
                                continue
                            r_lower = r_title.casefold()
                            is_book = any(k in r_lower for k in ["book", "textbook", "reference"])
                            is_exam = any(k in r_lower for k in ["exam", "final", "202", "questions", "bank", "august", "may", "دور"])

                            # Match remote assessment files if none found locally
                            if need_remote_assessments and is_exam and not is_book:
                                r_years = [int(m) for m in re.findall(r"(20[12]\d)", r_title)]
                                if not r_years:
                                    s_years = [int(m) for m in re.findall(r"(?:^|[^0-9])([12]\d)(?:[^0-9]|$)", r_title)]
                                    for sy in s_years:
                                        if 18 <= sy <= 30:
                                            r_years.append(2000 + sy)
                                if r_years:
                                    assessment_sources.append({
                                        "path": f"Questions/{r_title}" if not r_title.startswith("Questions/") else r_title,
                                        "type": "past_exam",
                                        "year": max(r_years),
                                        "action": "use_remote",
                                    })
                                else:
                                    assessment_sources.append({
                                        "path": f"Questions/{r_title}" if not r_title.startswith("Questions/") else r_title,
                                        "type": "question_bank",
                                        "action": "use_remote",
                                    })

                            # Match remote audio files if no local audio matched
                            if need_remote_audio and (any(r_lower.endswith(ext) for ext in [".mp3", ".m4a", ".wav", ".aac", ".ogg"]) or r_src.get("type") == "audio"):  # noqa: SIM102 - already seven levels deep; merging makes the line unreadable
                                if score_match(r_title) > 0:
                                    if matched_audio == [f"{query_stem}.mp3"]:
                                        matched_audio = [r_title]
                                    elif r_title not in matched_audio:
                                        matched_audio.append(r_title)

                            # Match remote slides or book if no local slide matched
                            if need_remote_slides and (is_book or any(r_lower.endswith(ext) for ext in [".pdf", ".pptx", ".ppsx", ".ppt", ".docx"])):  # noqa: SIM102 - already seven levels deep; merging makes the line unreadable
                                if score_match(r_title) > 0 or (is_book and not best_slide):
                                    slide_path = f"Lecture/{r_title}" if not r_title.startswith("Lecture/") else r_title
                                    slides_action = "use_remote"
                else:
                    _warn_remote_discovery(
                        f"nlm source list exited {proc.returncode}: "
                        f"{(proc.stderr or proc.stdout).strip()[:200]}"
                    )
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            # Remote discovery is best effort, but a silent failure here left
            # the manifest short of sources and the draft built on them
            # without a word -- and this is the path remote-only mode uses.
            _warn_remote_discovery(f"{type(error).__name__}: {error}")

    exam_style_profile = {
        "mcq": {
            "register": "Short direct factual stems with parallel concise options",
            "max_stem_words": 20,
            "options": {
                "count": 4,
                "labels": "lowercase a. through d.",
            },
            "stem_patterns": [
                "The following ...:-",
                "... are:",
                "... except:-",
            ],
        },
        "written": {
            "command_patterns": [
                "Causes of ...: 1.... 2....",
                "Treatment of ...",
                "Mechanism of ...",
            ],
            "answer_shape": "Numbered keywords matching requested count",
        },
        "cases": {
            "style": "Standard Egyptian medical exam case breakdown matching subject conventions",
            "sub_questions_pattern": [
                "1. Diagnosis (or Most likely diagnosis)",
                "2. DDx (Differential diagnosis) or Characteristic Clinical Picture (CP)",
                "3. Investigations / Confirmatory laboratory tests",
                "4. Treatment / Management (TTT / Antidote / Emergency measures)",
            ],
            "answer_shape": "Ultra-concise keyword bullets under standard clinical headings (1 to 5 words per point)",
        },
        "sample_scope": "Same college past exams and official question bank",
    }

    payload = {
        "title": query_stem,
        "recording_sources": matched_audio,
        "slides": {
            "path": slide_path,
            "action": slides_action,
        },
        "assessment_sources": assessment_sources,
        "exam_style_profile": exam_style_profile,
    }
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", query_stem).strip("-").lower() or "lecture"
    manifest_path = Path(tempfile.gettempdir()) / f"{slug}-auto-manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _source_manifest(path: str) -> SourceManifest:
    payload = _read_source_manifest(path)
    recordings = _manifest_recording_names(payload)
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise LauncherError("Source manifest requires a non-empty title")
    slides = payload.get("slides")
    slides_action = "auto"
    if slides is not None:
        slides_name = _manifest_source_name(slides)
        if not slides_name:
            raise LauncherError("Source manifest slides must include a path")
        if isinstance(slides, dict):
            slides_action = str(slides.get("action", "auto")).strip().casefold()
        slides = slides_name
    if slides_action not in {
        "auto",
        "use",
        "use_remote",
        "convert",
        "ocr",
        "compress",
        "chunk",
        "ignore",
        "wait",
    }:
        raise LauncherError(f"Unsupported slides action: {slides_action}")
    return SourceManifest(
        title=title.strip(),
        recording_sources=recordings,
        slides=slides.strip() if isinstance(slides, str) else None,
        slides_action=slides_action,
        approved_uploads=_manifest_upload_names(payload),
        exam_style_profile=_manifest_exam_style_profile(payload),
        assessment_sources=_manifest_assessment_sources(payload),
        references=_manifest_references(payload),
        manifest_path=str(Path(path).expanduser().resolve()),
    )


def _run_engine(command: list[str], source_root: Path, timeout: int, label: str) -> int:
    """Run the engine as a subprocess under a wall-clock ceiling.

    Without a timeout a wedged `nlm` call -- or a LibreOffice conversion that
    never returns -- hangs the launcher forever, which is especially bad when a
    sub-agent worker is waiting on it.
    """
    try:
        return subprocess.run(
            command, cwd=source_root, check=False, timeout=timeout
        ).returncode
    except subprocess.TimeoutExpired:
        print(
            f"[Launcher] {label} exceeded its {timeout}s limit and was stopped. "
            "Rerun with --resume-latest to continue from the last checkpoint.",
            file=sys.stderr,
            flush=True,
        )
        return 1


def _run_audit(command: list[str], source_root: Path) -> int:
    print("[Launcher] Starting read-only Phase 0 audit...", flush=True)
    return _run_engine(
        [*command, "--audit-only"], source_root, AUDIT_TIMEOUT_SECONDS, "Audit"
    )


def _needs_preflight_audit(invocation: EngineInvocation) -> bool:
    """Only a run that can upload sources needs the read-only preflight.

    --finalize-draft reads an existing draft and --recovery-phase applies an
    Agent-repaired response to a checkpoint. Neither uploads anything, so the
    separate audit subprocess was ~18s of NotebookLM traffic spent to validate
    a run that was never going to touch the notebook.
    """
    return not (invocation.finalize_draft or invocation.recovery_phase)


def _run_transcription(
    command: list[str], source_root: Path, invocation: EngineInvocation
) -> int:
    if _needs_preflight_audit(invocation):
        audit_exit_code = _run_audit(command, source_root)
        if audit_exit_code != 0:
            return audit_exit_code
        print(
            "[Launcher] Audit passed; starting the five transcription phases...",
            flush=True,
        )
    else:
        print(
            "[Launcher] Local-only invocation; skipping the read-only audit.",
            flush=True,
        )
    return _run_engine(
        command, source_root, TRANSCRIPTION_TIMEOUT_SECONDS, "Transcription"
    )


def _print_inventory(recordings: list[Any], pending: list[Any]) -> None:
    pending_ids = {source.source_id for source in pending}
    print("NotebookLM recordings:")
    for source in recordings:
        status = "pending" if source.source_id in pending_ids else "transcribed"
        print(f"- [{status}] {source.title}")


def _print_modules(modules: list[ModuleConfig]) -> None:
    print("Configured modules:")
    for module in modules:
        notebooks = ", ".join(
            reference.notebook_id or reference.title
            for reference in module.notebook.notebooks
        )
        print(f"- {module.module_id}: {module.display_name} -> {notebooks}")


def _lecture_key(
    module: ModuleConfig, recording: Any, manifest: SourceManifest | None
) -> str:
    title = manifest.title if manifest else recording.title
    recordings = manifest.recording_sources if manifest else (recording.title,)
    identity_parts = (module.module_id, title, *recordings)
    normalized = "\n".join(
        unicodedata.normalize("NFKC", part).strip().casefold().replace("\\", "/")
        for part in identity_parts
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@contextmanager
def _lecture_lock(
    module: ModuleConfig, recording: Any, manifest: SourceManifest | None
) -> Iterator[None]:
    lecture_key = _lecture_key(module, recording, manifest)
    lock_directory = module.paths.root / ".transcriber-cache" / "locks"
    lock_path = lock_directory / f"lecture-{lecture_key}.lock"
    with ExitStack() as stack:
        try:
            lock_file = stack.enter_context(
                exclusive_file_lock(lock_path, blocking=False)
            )
        except BlockingIOError as error:
            title = manifest.title if manifest else recording.title
            raise LauncherError(
                f"Lecture '{title}' is already running (key: {lecture_key})"
            ) from error
        lock_file.seek(0)
        lock_file.truncate()
        json.dump(
            {
                "lecture_key": lecture_key,
                "module": module.module_id,
                "title": manifest.title if manifest else recording.title,
                "pid": os.getpid(),
            },
            lock_file,
            ensure_ascii=False,
        )
        lock_file.flush()
        yield


def _selection(
    args: argparse.Namespace,
    context: LauncherContext,
    recordings: list[Any],
) -> RecordingSelection:
    return RecordingSelection(
        engine=context.engine,
        recordings=recordings,
        transcripts_dir=context.module.paths.transcripts,
        requested=args.lecture,
        run_all=args.all,
    )


def _execute_recording(
    args: argparse.Namespace,
    context: LauncherContext,
    recording: Any,
    manifest: SourceManifest | None = None,
) -> int:
    additional = ()
    title = None
    approved_uploads: tuple[str, ...] = ()
    slides: Path | None = None
    if manifest:
        selected = tuple(
            _requested_recording(
                context.engine,
                _recordings(
                    context.engine,
                    tuple(notebook.notebook_uuid for notebook in context.notebooks),
                    context.config,
                ),
                source,
            )
            for source in manifest.recording_sources
        )
        recording, additional = selected[0], selected[1:]
        title = manifest.title
        approved_uploads = manifest.approved_uploads
        if manifest.slides and manifest.slides_action != "ignore":
            slides = (
                Path(manifest.slides)
                if manifest.slides_action == "use_remote"
                else _requested_slides(manifest.slides, context.module)
            )
    else:
        slides = _slides_path(args.slides, context, recording.title)
    invocation = EngineInvocation(
        engine_path=context.engine_path,
        module=context.module,
        notebook_ids=tuple(notebook.notebook_uuid for notebook in context.notebooks),
        recording=recording,
        slides_path=slides,
        additional_recordings=additional,
        approved_uploads=approved_uploads,
        title=title,
        exam_style_profile=(manifest.exam_style_profile if manifest else None),
        assessment_sources=(manifest.assessment_sources if manifest else ()),
        assessment_manifest_provided=manifest is not None,
        draft_only=args.draft_only,
        finalize_draft=args.finalize_draft,
        source_manifest_path=(manifest.manifest_path if manifest else None),
        resume_run=getattr(args, "resume_run", None),
        resume_latest=bool(getattr(args, "resume_latest", False)),
        retry_phase=getattr(args, "retry_phase", None),
        recovery_phase=getattr(args, "recovery_phase", None),
        recovery_response=getattr(args, "recovery_response", None),
    )
    command = _engine_command(invocation)
    if args.audit_only:
        return _run_audit(command, context.module.paths.root)
    return _run_transcription(command, context.module.paths.root, invocation)


def _execute_selected(
    args: argparse.Namespace,
    context: LauncherContext,
    selected: list[Any],
    manifest: SourceManifest | None = None,
) -> int:
    if not selected:
        print(f"All recordings in module '{context.module.module_id}' are transcribed.")
        return 0
    for i, recording in enumerate(selected, 1):
        recording_manifest = manifest
        if recording_manifest is None:
            auto_manifest_path = generate_auto_manifest(
                context.module.paths.root,
                recording.title,
                str(context.config.get("nlm_executable") or "nlm"),
            )
            recording_manifest = _source_manifest(str(auto_manifest_path))
            print(f"\n[Batch {i}/{len(selected)}] >>> Generated Auto-Manifest for: {recording.title}")
        with _lecture_lock(context.module, recording, recording_manifest):
            exit_code = _execute_recording(args, context, recording, recording_manifest)
            if exit_code != 0:
                return exit_code
    return 0


def _requested_years(raw: str | None) -> tuple[int, ...]:
    """Parse --years as either a list (2022,2024) or a range (2020-2024)."""
    if not raw:
        return ()
    text = raw.strip()
    range_match = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", text)
    if range_match:
        first, last = int(range_match.group(1)), int(range_match.group(2))
        if first > last:
            first, last = last, first
        return tuple(range(first, last + 1))
    years = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        if not re.fullmatch(r"\d{4}", part):
            raise LauncherError(f"--years expects four-digit years, got {part!r}")
        years.append(int(part))
    return tuple(sorted(set(years)))


def _requested_kinds(raw: str, default: tuple[str, ...]) -> tuple[str, ...]:
    from question_bank import KINDS

    if not raw.strip():
        return default
    kinds = tuple(part.strip().casefold() for part in raw.split(",") if part.strip())
    unknown = [kind for kind in kinds if kind not in KINDS]
    if unknown:
        raise LauncherError(
            f"--kinds accepts {', '.join(KINDS)}; got {', '.join(unknown)}"
        )
    return kinds


def _export_target(args: argparse.Namespace, context: LauncherContext, stem: str, suffix: str) -> Path:
    if args.output:
        return Path(args.output).expanduser()
    return context.module.paths.transcripts / f"{stem}.{suffix}"


def _run_question_bank(args: argparse.Namespace, context: LauncherContext) -> int:
    from bank_export import (
        ExportError,
        render_bank_markdown,
        write_csv,
        write_json,
        write_xlsx,
    )
    from question_bank import (
        KINDS,
        QuestionBankError,
        build_bank,
        filter_bank,
        render_summary,
        sample_exam,
    )

    if args.question_bank and args.exam:
        raise LauncherError("--question-bank and --exam are separate commands")

    try:
        bank = build_bank(context.module.paths.transcripts, context.module.module_id)
    except QuestionBankError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1

    years = _requested_years(args.years)
    output_format = args.format.strip().casefold()

    if args.exam:
        kinds = _requested_kinds(args.kinds, ("mcq",))
        questions = sample_exam(
            bank, args.count, kinds=kinds, years=years, seed=args.seed
        )
        if not questions:
            print("[!] No questions matched that selection", file=sys.stderr)
            return 1
        title = f"{context.module.display_name} exam ({len(questions)} questions)"
        return _write_exam(args, context, bank, questions, title, output_format)

    print(render_summary(bank))
    kinds = _requested_kinds(args.kinds, KINDS)
    questions = filter_bank(bank, kinds=kinds, years=years)
    try:
        if output_format == "csv":
            result = write_csv(bank, _export_target(args, context, "question-bank", "csv"), questions)
        elif output_format == "json":
            result = write_json(bank, _export_target(args, context, "question-bank", "json"), questions)
        elif output_format == "xlsx":
            result = write_xlsx(bank, _export_target(args, context, "question-bank", "xlsx"), questions)
        else:
            target = _export_target(args, context, "question-bank", "md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_bank_markdown(bank, questions), encoding="utf-8")
            result = type("R", (), {"path": target, "written": len(questions)})()
    except ExportError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1
    print(f"\nWrote {result.written} question(s) to {result.path}")
    return 0


def _write_exam(
    args: argparse.Namespace,
    context: LauncherContext,
    bank: QuestionBank,
    questions: tuple[BankQuestion, ...],
    title: str,
    output_format: str,
) -> int:
    from bank_export import (
        ExportError,
        render_exam_html,
        render_exam_markdown,
        write_csv,
        write_docx,
    )

    try:
        if output_format == "html":
            target = _export_target(args, context, "exam", "html")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_exam_html(questions, title=title), encoding="utf-8")
            print(f"Wrote a self-grading paper with {len(questions)} question(s) to {target}")
            return 0
        if output_format == "docx":
            paper = _export_target(args, context, "exam", "docx")
            key = paper.with_name(f"{paper.stem}-answers{paper.suffix}")
            write_docx(bank, paper, questions, title=title)
            write_docx(bank, key, questions, title=f"{title} — answer key", with_answers=True)
            print(f"Wrote {paper} and {key}")
            return 0
        if output_format == "csv":
            result = write_csv(bank, _export_target(args, context, "exam", "csv"), questions)
            print(f"Wrote {result.written} question(s) to {result.path}")
            return 0
        paper_text, key_text = render_exam_markdown(questions, title=title)
        paper = _export_target(args, context, "exam", "md")
        key = paper.with_name(f"{paper.stem}-answers{paper.suffix}")
        paper.parent.mkdir(parents=True, exist_ok=True)
        paper.write_text(paper_text, encoding="utf-8")
        key.write_text(key_text, encoding="utf-8")
        # Two files on purpose: a paper with the answers under each question
        # cannot be sat.
        print(f"Wrote {paper} and {key}")
        return 0
    except ExportError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1


def _figure_slide_source(args: argparse.Namespace, context: LauncherContext) -> Path:
    """Which deck to illustrate: --slides if given, else the configured one."""
    if args.slides:
        candidate = Path(args.slides).expanduser()
        if not candidate.is_absolute():
            candidate = context.module.paths.root / candidate
        return candidate
    if not args.lecture:
        raise LauncherError(
            "--extract-figures needs --lecture (to find the configured slides) "
            "or --slides pointing at a deck"
        )
    configured = configured_slide(context.module, args.lecture)
    if configured:
        return configured
    raise LauncherError(
        f"No slides configured for '{args.lecture}' in module.json; "
        "pass --slides with the deck to illustrate"
    )


def _run_figure_extraction(args: argparse.Namespace, context: LauncherContext) -> int:
    from slide_figures import (
        DEFAULT_RESOLUTION,
        FigureExtractionError,
        extract_figures,
        render_reference_markdown,
        render_report,
    )

    source = _figure_slide_source(args, context)
    lecture = args.lecture or source.stem
    try:
        figure_set = extract_figures(
            source,
            context.module.paths.transcripts,
            lecture,
            resolution=args.figure_resolution or DEFAULT_RESOLUTION,
            include_text_pages=args.all_slide_pages,
        )
    except FigureExtractionError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1

    print(render_report(figure_set))
    markdown = render_reference_markdown(figure_set)
    if markdown:
        print("\nPaste into the transcript where the doctor showed them:\n")
        print(markdown)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-module transcription launcher")
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--modules-root")
    parser.add_argument("--module")
    parser.add_argument("--slides")
    parser.add_argument(
        "--source-manifest",
        help=(
            "Agent-approved JSON manifest for source selection, uploads, and "
            "multi-part lectures"
        ),
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--lecture")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--list-modules", action="store_true")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "Check that the external tooling the pipeline shells out to (nlm, "
            "poppler, ocrmypdf, libreoffice, ghostscript, ffmpeg, genanki) is "
            "installed, then exit"
        ),
    )
    parser.add_argument(
        "--doctor-live",
        action="store_true",
        help=(
            "Like --doctor, but actually runs each tool to prove it works -- "
            "most importantly whether `nlm` is authenticated. Slower, and the "
            "only version of the check that catches an installed-but-unusable "
            "tool before a run wastes half an hour on it"
        ),
    )
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument(
        "--extract-figures",
        action="store_true",
        help=(
            "Render the diagram pages of this lecture's slides into "
            "Transcripts/Figures/<lecture>/ and exit. Slides reach NotebookLM "
            "as text, so every picture in them is lost by the time a transcript "
            "is written; this is what puts them back"
        ),
    )
    parser.add_argument(
        "--figure-resolution",
        type=int,
        default=None,
        help="DPI for --extract-figures (default 150)",
    )
    parser.add_argument(
        "--question-bank",
        action="store_true",
        help=(
            "Collect every question in the module's transcripts into one bank, "
            "marking repeats, then exit"
        ),
    )
    parser.add_argument(
        "--exam",
        action="store_true",
        help="Draw an exam paper and a separate answer key from the bank, then exit",
    )
    parser.add_argument("--count", type=int, default=50, help="Questions in the --exam paper")
    parser.add_argument(
        "--years",
        help="Restrict to these past-exam years: a list (2022,2024) or a range (2020-2024)",
    )
    parser.add_argument(
        "--kinds",
        default="",
        help="Question kinds to include: mcq, written, case (comma separated)",
    )
    parser.add_argument(
        "--format",
        default="md",
        help=(
            "Output format: md, csv, json, html (self-grading paper), xlsx "
            "(needs openpyxl) or docx (needs python-docx)"
        ),
    )
    parser.add_argument("--output", help="Where to write the export (default: alongside Transcripts)")
    parser.add_argument("--seed", type=int, help="Make --exam sampling reproducible")
    parser.add_argument(
        "--all-slide-pages",
        action="store_true",
        help=(
            "With --extract-figures, render every slide rather than only the "
            "ones carrying a diagram"
        ),
    )
    parser.add_argument(
        "--sync-sources",
        action="store_true",
        help="Run the Agent-supervised module-wide source synchronization workflow",
    )
    parser.add_argument(
        "--source-sync-manifest",
        help="Agent-approved module source synchronization manifest",
    )
    parser.add_argument(
        "--auto-manifest",
        help="Automatically generate a complete manifest for the given lecture name/keyword",
    )
    parser.add_argument(
        "--transcribe-all-pending",
        action="store_true",
        help="Automatically discover and transcribe all pending untranscribed lectures in the module",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute an approved source synchronization plan",
    )
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument(
        "--draft-only",
        action="store_true",
        help="Write a draft for Agent review without updating the final transcript",
    )
    output_mode.add_argument(
        "--finalize-draft",
        action="store_true",
        help="Finalize the reviewed .draft.md and update the transcript/index",
    )
    parser.add_argument("--resume-run", help="Resume a saved run by ID or checkpoint directory")
    parser.add_argument(
        "--resume-latest",
        action="store_true",
        help="Resume the newest incomplete run for this lecture",
    )
    parser.add_argument(
        "--retry-phase",
        choices=("guide", "imp", "mcqs", "written", "cases"),
        help="Retry this phase and dependent phases from a saved run",
    )
    parser.add_argument(
        "--recovery-phase",
        choices=("guide", "imp", "mcqs", "written", "cases"),
        help="Phase repaired by the Agent response supplied with --recovery-response",
    )
    parser.add_argument(
        "--recovery-response",
        help="Path inside the run cache to the Agent-repaired phase response",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"universal-transcriber {__version__}",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Skip checking for newer versions",
    )
    return parser


def main() -> int:
    configure_console_streams()
    args = _parser().parse_args()
    workspace_for_cache = None
    if getattr(args, "workspace", None):
        try:
            workspace_for_cache = Path(args.workspace).expanduser().resolve()
        except Exception:
            pass
    print_update_notice_if_available(workspace=workspace_for_cache, quiet=getattr(args, "no_update_check", False))
    if args.doctor or args.doctor_live:
        from dependency_doctor import report as dependency_report

        return dependency_report(live=args.doctor_live)
    try:
        if bool(args.recovery_phase) != bool(args.recovery_response):
            raise LauncherError(
                "--recovery-phase and --recovery-response must be supplied together"
            )
        if args.recovery_response and not (args.resume_run or args.resume_latest):
            raise LauncherError("Agent recovery requires --resume-run or --resume-latest")
        if args.recovery_response and args.retry_phase:
            raise LauncherError("Agent recovery cannot be combined with --retry-phase")
        workspace = Path(args.workspace).expanduser().resolve()
        if args.list_modules:
            _print_modules(discover_modules(workspace, args.modules_root))
            return 0
        context = _launcher_context(args)
        if args.extract_figures:
            return _run_figure_extraction(args, context)
        if args.question_bank or args.exam:
            return _run_question_bank(args, context)
        if args.auto_manifest:
            if args.source_manifest:
                raise LauncherError("--auto-manifest cannot be combined with --source-manifest")
            auto_manifest_path = generate_auto_manifest(
                context.module.paths.root,
                args.auto_manifest,
                str(context.config.get("nlm_executable") or "nlm"),
            )
            args.source_manifest = str(auto_manifest_path)
            print(f"[Auto-Manifest] Generated manifest: {auto_manifest_path}")
        if args.sync_sources:
            if args.lecture or args.all or args.list or args.slides or args.source_manifest:
                raise LauncherError(
                    "--sync-sources cannot be combined with lecture selection or --source-manifest"
                )
            if args.apply == args.audit_only:
                raise LauncherError(
                    "--sync-sources requires exactly one of --audit-only or --apply"
                )
            from source_sync import (
                SourceSyncError,
                SourceSyncRequest,
                apply_source_sync,
                audit_source_sync,
                discover_local_sources,
                render_source_sync_report,
            )

            if not args.source_sync_manifest:
                if args.apply:
                    raise LauncherError("--apply requires --source-sync-manifest")
                print("\n=== Module Source Sync Inventory ===")
                print(f"Module: {context.module.module_id}")
                for path in discover_local_sources(context.module.paths.root):
                    print(f"[PENDING AGENT REVIEW] {path}")
                print("Create an Agent-reviewed manifest, then rerun the audit.")
                print("=== End Module Source Sync Inventory ===\n")
                return 0
            try:
                sync_request = SourceSyncRequest(
                    context.engine,
                    context.config,
                    context.module.module_id,
                    context.module.paths.root,
                    context.notebooks,
                    args.source_sync_manifest,
                )
                report = (
                    apply_source_sync(sync_request)
                    if args.apply
                    else audit_source_sync(sync_request)
                )
            except SourceSyncError as error:
                raise LauncherError(str(error)) from error
            print(render_source_sync_report(report))
            return 0 if report.status in {"planned", "completed"} else 1
        recordings = _recordings(
            context.engine,
            tuple(notebook.notebook_uuid for notebook in context.notebooks),
            context.config,
        )
        pending = _pending_recordings(
            context.engine, recordings, context.module.paths.transcripts
        )
        if args.list:
            _print_inventory(recordings, pending)
            return 0
        manifest = (
            _source_manifest(args.source_manifest)
            if args.source_manifest
            else None
        )
        if args.transcribe_all_pending:
            if args.lecture or args.source_manifest or args.auto_manifest:
                raise LauncherError(
                    "--transcribe-all-pending cannot be combined with --lecture, --source-manifest, or --auto-manifest"
                )
            selected = pending
            manifest = None
        elif manifest:
            if args.lecture or args.all or args.slides:
                raise LauncherError(
                    "--source-manifest cannot be combined with --lecture, "
                    "--all, or --slides"
                )
            selected = [
                _requested_recording(
                    context.engine, recordings, manifest.recording_sources[0]
                )
            ]
        else:
            if not args.audit_only:
                raise LauncherError(
                    "A source manifest is required for a real transcription; "
                    "pass --auto-manifest, --transcribe-all-pending, or --source-manifest"
                )
            selected = _selected_recordings(_selection(args, context, recordings))
        if not args.audit_only:
            from source_sync import source_sync_preflight

            pending_sync = source_sync_preflight(context.module.paths.root)
            if pending_sync:
                raise LauncherError(
                    "Module source sync requires Agent review before transcription: "
                    + "; ".join(pending_sync)
                )
        return _execute_selected(args, context, selected, manifest)
    except (LauncherError, ModuleConfigError, OSError) as error:
        print(f"[Launcher Error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
