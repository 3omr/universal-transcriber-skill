#!/usr/bin/env python3
"""Create and validate module folders without creating NotebookLM notebooks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from module_registry import (
    MODULE_ID_PATTERN,
    ModuleConfigError,
    discover_modules,
    modules_root,
    normalize_module_name,
    resolve_module,
)


class ModuleManagerError(RuntimeError):
    """Raised when a module mutation cannot be completed safely."""


@dataclass(frozen=True)
class NotebookSummary:
    notebook_id: str
    title: str


@dataclass(frozen=True)
class CreateRequest:
    workspace: Path
    modules_root: str | None
    module_id: str
    display_name: str
    aliases: tuple[str, ...]
    notebook_id: str | None
    notebook_title: str | None
    nlm_profile: str | None
    emoji: str
    apply: bool


def _nlm_executable() -> str:
    executable = shutil.which("nlm")
    if not executable:
        raise ModuleManagerError("nlm executable was not found on PATH")
    return executable


def _nlm_json(
    arguments: list[str], profile: str | None = None, timeout_seconds: int = 120
) -> Any:
    command = [_nlm_executable(), *arguments, "--json"]
    if profile:
        command.extend(["--profile", profile])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ModuleManagerError(f"{' '.join(arguments)} timed out") from error
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise ModuleManagerError(message[:500])
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ModuleManagerError("nlm returned invalid JSON") from error


def _notebook_summaries(profile: str | None) -> list[NotebookSummary]:
    payload = _nlm_json(["notebook", "list"], profile)
    if not isinstance(payload, list):
        raise ModuleManagerError("nlm notebook list returned an unexpected payload")
    return [
        NotebookSummary(str(entry.get("id", "")), str(entry.get("title", "")).strip())
        for entry in payload
        if isinstance(entry, dict) and entry.get("id")
    ]


def _matching_notebooks(
    notebooks: list[NotebookSummary], request: CreateRequest
) -> list[NotebookSummary]:
    if request.notebook_id:
        return [
            notebook
            for notebook in notebooks
            if notebook.notebook_id == request.notebook_id
        ]
    title_key = normalize_module_name(request.notebook_title or request.display_name)
    return [
        notebook
        for notebook in notebooks
        if normalize_module_name(notebook.title) == title_key
    ]


def _resolved_notebook(request: CreateRequest) -> NotebookSummary:
    matches = _matching_notebooks(_notebook_summaries(request.nlm_profile), request)
    if len(matches) != 1:
        reference = request.notebook_id or request.notebook_title or request.display_name
        raise ModuleManagerError(f"Notebook '{reference}' did not resolve uniquely")
    notebook = matches[0]
    if request.notebook_title and normalize_module_name(
        request.notebook_title
    ) != normalize_module_name(notebook.title):
        raise ModuleManagerError("Notebook ID and title refer to different notebooks")
    return notebook


def _existing_aliases(workspace: Path, requested_root: str | None) -> set[str]:
    root = modules_root(workspace, requested_root)
    if not root.is_dir():
        return set()
    if not any(path.is_dir() and (path / "module.json").is_file() for path in root.iterdir()):
        return set()
    modules = discover_modules(workspace, requested_root)
    return {
        normalize_module_name(alias)
        for module in modules
        for alias in module.aliases
    }


def _validated_request(request: CreateRequest) -> None:
    if not MODULE_ID_PATTERN.fullmatch(request.module_id):
        raise ModuleManagerError("module id must use lowercase kebab-case")
    requested_aliases = {
        normalize_module_name(alias)
        for alias in (request.module_id, request.display_name, *request.aliases)
    }
    duplicates = requested_aliases & _existing_aliases(
        request.workspace, request.modules_root
    )
    if duplicates:
        raise ModuleManagerError(f"Module aliases already exist: {', '.join(duplicates)}")


def _module_payload(request: CreateRequest, notebook: NotebookSummary) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "module_id": request.module_id,
        "display_name": request.display_name,
        "aliases": list(request.aliases),
        "notebook": {
            "id": notebook.notebook_id,
            "title": notebook.title,
            "profile": request.nlm_profile,
        },
        "output": {
            "emoji": request.emoji,
            "language": "Egyptian Arabic mixed with English medical terminology",
        },
        "lecture_slides": {},
    }


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _create_module(request: CreateRequest) -> int:
    _validated_request(request)
    notebook = _resolved_notebook(request)
    module_root = modules_root(request.workspace, request.modules_root) / request.module_id
    if module_root.exists():
        raise ModuleManagerError(f"Module directory already exists: {module_root}")
    print(f"Module: {request.module_id} ({request.display_name})")
    print(f"Notebook: {notebook.title} ({notebook.notebook_id})")
    print(f"Destination: {module_root}")
    if not request.apply:
        print("Dry run only. Re-run with --apply to create the module.")
        return 0
    for directory_name in ("Lecture", "Questions", "Exams"):
        (module_root / directory_name).mkdir(parents=True, exist_ok=True)
    _write_json_atomically(module_root / "module.json", _module_payload(request, notebook))
    print(f"Created module: {module_root}")
    return 0


def _validate_module(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    module = resolve_module(
        discover_modules(workspace, args.modules_root), args.module
    )
    payload = _nlm_json(
        ["notebook", "get", module.notebook.notebook_id], module.notebook.profile
    )
    resolved_id = str(payload.get("notebook_id", "")) if isinstance(payload, dict) else ""
    if resolved_id != module.notebook.notebook_id:
        raise ModuleManagerError("Configured notebook could not be verified")
    print(f"Valid module: {module.module_id} -> {module.notebook.title}")
    return 0


def _list_modules(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    for module in discover_modules(workspace, args.modules_root):
        print(f"{module.module_id}\t{module.display_name}\t{module.notebook.title}")
    return 0


def _create_request(args: argparse.Namespace) -> CreateRequest:
    return CreateRequest(
        workspace=Path(args.workspace).expanduser().resolve(),
        modules_root=args.modules_root,
        module_id=args.module,
        display_name=args.display_name,
        aliases=tuple(args.alias or ()),
        notebook_id=args.notebook_id,
        notebook_title=args.notebook_title,
        nlm_profile=args.nlm_profile,
        emoji=args.emoji,
        apply=args.apply,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage transcriber modules")
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--modules-root")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--module", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--alias", action="append")
    create.add_argument("--notebook-id")
    create.add_argument("--notebook-title")
    create.add_argument("--nlm-profile")
    create.add_argument("--emoji", default="📚")
    create.add_argument("--apply", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--module", required=True)
    commands.add_parser("list")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            return _create_module(_create_request(args))
        if args.command == "validate":
            return _validate_module(args)
        return _list_modules(args)
    except (ModuleConfigError, ModuleManagerError, OSError) as error:
        print(f"[Module Error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
