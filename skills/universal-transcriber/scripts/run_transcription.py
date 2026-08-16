#!/usr/bin/env python3
"""Resolve one module and run its NotebookLM transcription pipeline."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

from module_registry import (
    ModuleConfig,
    ModuleConfigError,
    configured_slide,
    discover_modules,
    resolve_module,
)


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


def _engine_path(workspace: Path) -> Path:
    local_engine = Path(__file__).resolve().parent / "universal_transcribe.py"
    if local_engine.is_file():
        return local_engine
    preferred = workspace / "universal_transcriber" / "universal_transcribe.py"
    if preferred.is_file():
        return preferred
    matches = [
        path
        for path in workspace.rglob("universal_transcribe.py")
        if ".git" not in path.parts
    ]
    if not matches:
        raise LauncherError("Could not find universal_transcribe.py")
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
    "aspirin": "acetyl salysilic",
    "salicylate": "acetyl salysilic",
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
}


def generate_auto_manifest(module_root: Path, lecture_query: str) -> Path:
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
            elif len(tok) >= 5 and (tok[:5] in name_lower or name_lower[:5] in tok):
                score += 15
        for syn in synonym_targets:
            if syn in name_lower or name_lower in syn:
                score += 30
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

    if not matched_audio:
        if best_slide and best_slide not in book_files:
            matched_audio = [best_slide.name]
        else:
            matched_audio = [f"{query_stem}.mp3"]

    slide_path = f"Lecture/{best_slide.name}" if best_slide else f"Lecture/{query_stem}.pdf"

    assessment_sources: list[dict[str, Any]] = []
    if questions_dir.is_dir():
        for item in sorted(questions_dir.iterdir()):
            if item.name.startswith(".") or item.suffix.lower() not in {".pdf", ".txt", ".docx"}:
                continue
            years = [int(match) for match in re.findall(r"\b(20[12]\d)\b", item.name)]
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
        "sample_scope": "Same college past exams and official question bank",
    }

    payload = {
        "title": query_stem,
        "recording_sources": matched_audio,
        "slides": {
            "path": slide_path,
            "action": "auto",
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


def _run_audit(command: list[str], source_root: Path) -> int:
    print("[Launcher] Starting read-only Phase 0 audit...", flush=True)
    return subprocess.run(
        [*command, "--audit-only"], cwd=source_root, check=False
    ).returncode


def _run_transcription(command: list[str], source_root: Path) -> int:
    audit_exit_code = _run_audit(command, source_root)
    if audit_exit_code != 0:
        return audit_exit_code
    print(
        "[Launcher] Audit passed; starting the five transcription phases...",
        flush=True,
    )
    return subprocess.run(command, cwd=source_root, check=False).returncode


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
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / f"lecture-{lecture_key}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
    approved_uploads = ()
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
    return _run_transcription(command, context.module.paths.root)


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
            auto_manifest_path = generate_auto_manifest(context.module.paths.root, recording.title)
            recording_manifest = _source_manifest(str(auto_manifest_path))
            print(f"\n[Batch {i}/{len(selected)}] >>> Generated Auto-Manifest for: {recording.title}")
        with _lecture_lock(context.module, recording, recording_manifest):
            exit_code = _execute_recording(args, context, recording, recording_manifest)
            if exit_code != 0:
                return exit_code
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
    parser.add_argument("--audit-only", action="store_true")
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
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(line_buffering=True)
    args = _parser().parse_args()
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
        if args.auto_manifest:
            if args.source_manifest:
                raise LauncherError("--auto-manifest cannot be combined with --source-manifest")
            auto_manifest_path = generate_auto_manifest(context.module.paths.root, args.auto_manifest)
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
