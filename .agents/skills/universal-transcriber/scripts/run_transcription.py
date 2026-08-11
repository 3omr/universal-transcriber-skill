#!/usr/bin/env python3
"""Discover NotebookLM lectures and invoke the universal transcriber safely."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


class LauncherError(RuntimeError):
    """Raised when automatic discovery cannot make a unique, safe choice."""


@dataclass(frozen=True)
class NotebookResolution:
    config: dict[str, Any]
    notebook_reference: str
    subject: str
    register_notebook: bool


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
    subject: str
    notebook_id: str
    source_root: Path
    recording: Any
    slides_path: Path | None


@dataclass(frozen=True)
class LauncherContext:
    engine_path: Path
    engine: ModuleType
    subject: str
    source_root: Path
    notebook: Any


def _engine_path(workspace: Path) -> Path:
    preferred = workspace / "universal_transcriber" / "universal_transcribe.py"
    if preferred.is_file():
        return preferred
    matches = [
        path
        for path in workspace.rglob("universal_transcribe.py")
        if ".agents" not in path.parts and ".git" not in path.parts
    ]
    if len(matches) != 1:
        raise LauncherError("Could not find one unique universal_transcribe.py")
    return matches[0]


def _load_engine(engine_path: Path) -> ModuleType:
    module_spec = importlib.util.spec_from_file_location(
        "universal_transcriber_launcher_engine", engine_path
    )
    if module_spec is None or module_spec.loader is None:
        raise LauncherError(f"Could not load engine: {engine_path}")
    engine = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = engine
    module_spec.loader.exec_module(engine)
    return engine


def _source_roots(workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    excluded_parts = {".git", ".agents", "__pycache__", "_ocr_backups"}
    for lecture_dir in workspace.rglob("Lecture"):
        if not lecture_dir.is_dir() or any(
            excluded in lecture_dir.parts for excluded in excluded_parts
        ):
            continue
        course_root = lecture_dir.parent
        if (course_root / "Questions").is_dir() or (course_root / "Exams").is_dir():
            candidates.append(course_root)
    return sorted(set(candidates))


def _source_root(workspace: Path, requested: str | None) -> Path:
    if requested:
        source_root = Path(requested).expanduser().resolve()
        if not (source_root / "Lecture").is_dir():
            raise LauncherError(f"Lecture directory not found under {source_root}")
        return source_root
    candidates = _source_roots(workspace)
    if len(candidates) != 1:
        rendered = ", ".join(str(path) for path in candidates) or "none"
        raise LauncherError(
            "Could not select one course root. Pass --sources-root. "
            f"Candidates: {rendered}"
        )
    return candidates[0]


def _notebook_reference(
    config: dict[str, Any], subject: str, requested: str | None
) -> str:
    notebook_reference = requested or config.get("notebook_ids", {}).get(subject)
    if not notebook_reference:
        raise LauncherError(
            f"No notebook is configured for {subject}; pass --notebook-id"
        )
    return str(notebook_reference)


def _notebook_url(engine: ModuleType, notebook_reference: str) -> str:
    if notebook_reference.startswith("https://"):
        return notebook_reference
    notebook_uuid = engine._extract_notebook_uuid(notebook_reference)
    if not notebook_uuid:
        raise LauncherError(
            "Registering a notebook requires a NotebookLM URL or UUID"
        )
    return f"https://notebooklm.google.com/notebook/{notebook_uuid}"


def _register_notebook(
    engine: ModuleType,
    config: dict[str, Any],
    notebook_reference: str,
    subject: str,
) -> None:
    registration = {
        "url": _notebook_url(engine, notebook_reference),
        "name": subject,
        "description": (
            f"Medical lecture recordings, slides, references, past exams, and "
            f"question banks for {subject}."
        ),
        "topics": [subject, "medical lectures", "past exams", "question banks"],
        "content_types": ["audio", "slides", "references", "exam questions"],
        "use_cases": [
            "Transcribing lectures",
            "Extracting exam questions",
            "Creating medical study guides",
        ],
        "tags": ["medical", "transcription", subject.casefold()],
    }
    try:
        response = engine.call_mcp_tool(
            config, "add_notebook", registration, timeout_seconds=60
        )
        engine._successful_tool_payload(response, "add_notebook")
    except engine.TranscriberError as error:
        raise LauncherError(f"Notebook registration failed: {error}") from error


def _registered_notebook(engine: ModuleType, resolution: NotebookResolution) -> Any:
    _register_notebook(
        engine,
        resolution.config,
        resolution.notebook_reference,
        resolution.subject,
    )
    try:
        return engine.resolve_notebook(
            resolution.config,
            resolution.notebook_reference,
            resolution.subject,
        )
    except engine.TranscriberError as error:
        raise LauncherError(f"Registered notebook could not be resolved: {error}") from error


def _resolved_notebook(
    engine: ModuleType, resolution: NotebookResolution
) -> Any:
    try:
        return engine.resolve_notebook(
            resolution.config,
            resolution.notebook_reference,
            resolution.subject,
        )
    except engine.TranscriberError as error:
        message = str(error).casefold()
        missing = isinstance(error, engine.Phase0Error) and (
            "empty" in message or "not found" in message
        )
        if not resolution.register_notebook or not missing:
            raise LauncherError(
                f"{error}. Run again with --register-notebook after explicit approval."
            ) from error
    return _registered_notebook(engine, resolution)


def _recordings(engine: ModuleType, notebook_uuid: str) -> list[Any]:
    try:
        remote_sources = engine.list_remote_sources(notebook_uuid)
    except engine.TranscriberError as error:
        raise LauncherError(f"Could not read NotebookLM sources: {error}") from error
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
        "Multiple untranscribed recordings were found. Name one lecture or explicitly "
        f"request all recordings:\n{names}"
    )


def _topic_tokens(engine: ModuleType, source_name: str) -> set[str]:
    ignored = {
        "lecture",
        "recording",
        "poison",
        "poisons",
        "poisoning",
        "dr",
        "doctor",
    }
    return {
        token
        for token in engine.normalize_source_stem(source_name).split()
        if len(token) >= 3 and token not in ignored
    }


def _matching_slides(
    engine: ModuleType, source_root: Path, recording_title: str
) -> Path | None:
    recording_tokens = _topic_tokens(engine, recording_title)
    candidates = [
        source
        for source in engine.scan_local_sources(str(source_root))
        if source.relative_path.startswith(f"Lecture{os.sep}")
        and source.role != "textbook"
        and source.extension in {*engine.SLIDE_EXTENSIONS, ".pdf"}
    ]
    scored = [
        (len(recording_tokens & _topic_tokens(engine, source.name)), source)
        for source in candidates
    ]
    best_score = max((score for score, _source in scored), default=0)
    matches = [source for score, source in scored if score == best_score and score > 0]
    return Path(matches[0].path) if len(matches) == 1 else None


def _engine_command(invocation: EngineInvocation) -> list[str]:
    command = [
        sys.executable,
        str(invocation.engine_path),
        "--subject",
        invocation.subject,
        "--notebook-id",
        invocation.notebook_id,
        "--lecture",
        invocation.recording.title,
        "--recording-source",
        invocation.recording.title,
        "--sources-root",
        str(invocation.source_root),
        "--output-dir",
        str(invocation.source_root / "Transcripts"),
    ]
    if invocation.slides_path:
        command.extend(["--pptx", str(invocation.slides_path)])
    return command


def _run_audit(command: list[str], source_root: Path) -> int:
    audit = subprocess.run(
        [*command, "--audit-only"], cwd=source_root, check=False
    )
    return audit.returncode


def _run_transcription(command: list[str], source_root: Path) -> int:
    audit_exit_code = _run_audit(command, source_root)
    if audit_exit_code != 0:
        return audit_exit_code
    return subprocess.run(command, cwd=source_root, check=False).returncode


def _print_inventory(recordings: list[Any], pending: list[Any]) -> None:
    pending_ids = {source.source_id for source in pending}
    print("NotebookLM recordings:")
    for source in recordings:
        status = "pending" if source.source_id in pending_ids else "transcribed"
        print(f"- [{status}] {source.title}")


def _launcher_context(args: argparse.Namespace) -> LauncherContext:
    workspace = Path(args.workspace).expanduser().resolve()
    engine_path = _engine_path(workspace)
    engine = _load_engine(engine_path)
    config = engine.load_config()
    subject = str(args.subject or config.get("default_subject", "Toxicology"))
    source_root = _source_root(workspace, args.sources_root)
    notebook_reference = _notebook_reference(config, subject, args.notebook_id)
    resolution = NotebookResolution(
        config=config,
        notebook_reference=notebook_reference,
        subject=subject,
        register_notebook=args.register_notebook,
    )
    notebook = _resolved_notebook(engine, resolution)
    return LauncherContext(
        engine_path=engine_path,
        engine=engine,
        subject=subject,
        source_root=source_root,
        notebook=notebook,
    )


def _slides_path(
    requested: str | None,
    engine: ModuleType,
    source_root: Path,
    recording_title: str,
) -> Path | None:
    if not requested:
        return _matching_slides(engine, source_root, recording_title)
    slides_path = Path(requested).expanduser().resolve()
    if not slides_path.is_file():
        raise LauncherError(f"Slides file not found: {slides_path}")
    return slides_path


def _selection(
    args: argparse.Namespace,
    context: LauncherContext,
    recordings: list[Any],
) -> RecordingSelection:
    return RecordingSelection(
        engine=context.engine,
        recordings=recordings,
        transcripts_dir=context.source_root / "Transcripts",
        requested=args.lecture,
        run_all=args.all,
    )


def _execute_recording(
    args: argparse.Namespace, context: LauncherContext, recording: Any
) -> int:
    slides_path = _slides_path(
        args.slides, context.engine, context.source_root, recording.title
    )
    invocation = EngineInvocation(
        engine_path=context.engine_path,
        subject=context.subject,
        notebook_id=context.notebook.library_id,
        source_root=context.source_root,
        recording=recording,
        slides_path=slides_path,
    )
    command = _engine_command(invocation)
    if args.audit_only:
        return _run_audit(command, context.source_root)
    return _run_transcription(command, context.source_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Antigravity transcription launcher")
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--sources-root")
    parser.add_argument("--subject")
    parser.add_argument("--notebook-id")
    parser.add_argument("--slides")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--lecture")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--register-notebook", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        context = _launcher_context(args)
        recordings = _recordings(context.engine, context.notebook.notebook_uuid)
        transcripts_dir = context.source_root / "Transcripts"
        pending = _pending_recordings(context.engine, recordings, transcripts_dir)
        if args.list:
            _print_inventory(recordings, pending)
            return 0
        selected = _selected_recordings(_selection(args, context, recordings))
        if not selected:
            print("All NotebookLM recordings already have matching transcripts.")
            return 0
        for recording in selected:
            exit_code = _execute_recording(args, context, recording)
            if exit_code != 0:
                return exit_code
        return 0
    except (LauncherError, OSError) as error:
        print(f"[Launcher Error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
