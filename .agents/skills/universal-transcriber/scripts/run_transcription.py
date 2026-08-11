#!/usr/bin/env python3
"""Resolve one module and run its NotebookLM transcription pipeline."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
import subprocess
import sys
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
    notebook_id: str
    recording: Any
    slides_path: Path | None


@dataclass(frozen=True)
class LauncherContext:
    engine_path: Path
    engine: ModuleType
    config: dict[str, Any]
    module: ModuleConfig
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


def _module_config_for_engine(
    engine_config: dict[str, Any], module: ModuleConfig
) -> dict[str, Any]:
    config = dict(engine_config)
    if module.notebook.profile:
        config["nlm_profile"] = module.notebook.profile
    config["default_subject"] = module.display_name
    return config


def _resolved_notebook(
    engine: ModuleType, config: dict[str, Any], module: ModuleConfig
) -> Any:
    notebook_reference = module.notebook.notebook_id or module.notebook.title
    try:
        return engine.resolve_notebook(
            config, notebook_reference, module.notebook.title or module.display_name
        )
    except engine.TranscriberError as error:
        raise LauncherError(f"Could not resolve module notebook: {error}") from error


def _launcher_context(args: argparse.Namespace) -> LauncherContext:
    workspace = Path(args.workspace).expanduser().resolve()
    modules = discover_modules(workspace, args.modules_root)
    module = resolve_module(modules, args.module)
    engine_path = _engine_path(workspace)
    engine = _load_engine(engine_path)
    config = _module_config_for_engine(engine.load_config(), module)
    notebook = _resolved_notebook(engine, config, module)
    return LauncherContext(engine_path, engine, config, module, notebook)


def _recordings(
    engine: ModuleType, notebook_uuid: str, config: dict[str, Any]
) -> list[Any]:
    try:
        remote_sources = engine.list_remote_sources(notebook_uuid, config)
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


def _requested_slides(requested: str, module: ModuleConfig) -> Path:
    requested_path = Path(requested).expanduser()
    slide_path = (
        requested_path.resolve()
        if requested_path.is_absolute()
        else (module.paths.root / requested_path).resolve()
    )
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
        str(invocation.engine_path),
        "--subject",
        invocation.module.display_name,
        "--emoji",
        invocation.module.emoji,
        "--notebook-id",
        invocation.notebook_id,
        "--lecture",
        invocation.recording.title,
        "--recording-source",
        invocation.recording.title,
        "--sources-root",
        str(invocation.module.paths.root),
        "--output-dir",
        str(invocation.module.paths.transcripts),
    ]
    if invocation.slides_path:
        command.extend(["--pptx", str(invocation.slides_path)])
    if invocation.module.notebook.profile:
        command.extend(["--nlm-profile", invocation.module.notebook.profile])
    return command


def _run_audit(command: list[str], source_root: Path) -> int:
    return subprocess.run(
        [*command, "--audit-only"], cwd=source_root, check=False
    ).returncode


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


def _print_modules(modules: list[ModuleConfig]) -> None:
    print("Configured modules:")
    for module in modules:
        notebook = module.notebook.notebook_id or module.notebook.title
        print(f"- {module.module_id}: {module.display_name} -> {notebook}")


@contextmanager
def _module_lock(module: ModuleConfig) -> Iterator[None]:
    lock_path = module.paths.root / ".transcriber.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LauncherError(f"Module '{module.module_id}' is already running") from error
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
    args: argparse.Namespace, context: LauncherContext, recording: Any
) -> int:
    invocation = EngineInvocation(
        engine_path=context.engine_path,
        module=context.module,
        notebook_id=context.notebook.notebook_uuid,
        recording=recording,
        slides_path=_slides_path(args.slides, context, recording.title),
    )
    command = _engine_command(invocation)
    if args.audit_only:
        return _run_audit(command, context.module.paths.root)
    return _run_transcription(command, context.module.paths.root)


def _execute_selected(
    args: argparse.Namespace, context: LauncherContext, selected: list[Any]
) -> int:
    if not selected:
        print(f"All recordings in module '{context.module.module_id}' are transcribed.")
        return 0
    with _module_lock(context.module):
        for recording in selected:
            exit_code = _execute_recording(args, context, recording)
            if exit_code != 0:
                return exit_code
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-module transcription launcher")
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--modules-root")
    parser.add_argument("--module")
    parser.add_argument("--slides")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--lecture")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--list-modules", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        workspace = Path(args.workspace).expanduser().resolve()
        if args.list_modules:
            _print_modules(discover_modules(workspace, args.modules_root))
            return 0
        context = _launcher_context(args)
        recordings = _recordings(
            context.engine, context.notebook.notebook_uuid, context.config
        )
        pending = _pending_recordings(
            context.engine, recordings, context.module.paths.transcripts
        )
        if args.list:
            _print_inventory(recordings, pending)
            return 0
        selected = _selected_recordings(_selection(args, context, recordings))
        return _execute_selected(args, context, selected)
    except (LauncherError, ModuleConfigError, OSError) as error:
        print(f"[Launcher Error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
