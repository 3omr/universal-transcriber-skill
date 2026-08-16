#!/usr/bin/env python3
"""Create, validate, and migrate agent-owned medical modules."""

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
    created: bool = False


@dataclass(frozen=True)
class CreateRequest:
    workspace: Path
    modules_root: str | None
    module_id: str
    display_name: str
    aliases: tuple[str, ...]
    notebook_ids: tuple[str, ...]
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
    if isinstance(payload, dict):
        payload = payload.get("notebooks")
    if not isinstance(payload, list):
        raise ModuleManagerError("nlm notebook list returned an unexpected payload")
    return [
        NotebookSummary(
            str(entry.get("id", "")), str(entry.get("title") or "").strip()
        )
        for entry in payload
        if isinstance(entry, dict) and entry.get("id")
    ]


def _matching_notebooks(
    notebooks: list[NotebookSummary], request: CreateRequest
) -> list[NotebookSummary]:
    if request.notebook_ids:
        return [
            notebook
            for notebook in notebooks
            if notebook.notebook_id in request.notebook_ids
        ]
    title_key = normalize_module_name(request.notebook_title or request.display_name)
    return [
        notebook
        for notebook in notebooks
        if normalize_module_name(notebook.title) == title_key
    ]


def _create_notebook(request: CreateRequest) -> NotebookSummary:
    title = request.notebook_title or request.display_name
    payload = _nlm_json(["notebook", "create", title], request.nlm_profile)
    if not isinstance(payload, dict):
        raise ModuleManagerError("nlm notebook create returned an unexpected payload")
    notebook_id = str(payload.get("id") or payload.get("notebook_id") or "").strip()
    if not notebook_id:
        raise ModuleManagerError("nlm notebook create returned no notebook ID")
    return NotebookSummary(
        notebook_id,
        str(payload.get("title") or title).strip(),
        created=True,
    )


def _resolved_notebooks(request: CreateRequest) -> list[NotebookSummary]:
    matches = _matching_notebooks(_notebook_summaries(request.nlm_profile), request)
    if request.notebook_ids:
        if len(matches) != len(request.notebook_ids):
            missing = sorted(set(request.notebook_ids) - {match.notebook_id for match in matches})
            raise ModuleManagerError("Notebook IDs were not found: " + ", ".join(missing))
        matches_by_id = {match.notebook_id: match for match in matches}
        return [matches_by_id[notebook_id] for notebook_id in request.notebook_ids]
    if len(matches) > 1:
        options = ", ".join(f"{match.title} ({match.notebook_id})" for match in matches)
        raise ModuleManagerError(
            "Multiple matching notebooks found; choose one or more with "
            f"--notebook-id: {options}"
        )
    if not matches:
        if not request.apply:
            return [
                NotebookSummary(
                    "<created-on-apply>", request.notebook_title or request.display_name
                )
            ]
        return [_create_notebook(request)]
    notebook = matches[0]
    if request.notebook_title and normalize_module_name(
        request.notebook_title
    ) != normalize_module_name(notebook.title):
        raise ModuleManagerError("Notebook ID and title refer to different notebooks")
    return [notebook]


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


def _module_payload(
    request: CreateRequest, notebooks: list[NotebookSummary]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "module_id": request.module_id,
        "display_name": request.display_name,
        "aliases": list(request.aliases),
        "notebooks": [
            {"id": notebook.notebook_id, "title": notebook.title}
            for notebook in notebooks
        ],
        "notebook_profile": request.nlm_profile,
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
    module_root = modules_root(request.workspace, request.modules_root) / request.module_id
    if module_root.exists():
        raise ModuleManagerError(f"Module directory already exists: {module_root}")
    notebooks = _resolved_notebooks(request)
    print(f"Module: {request.module_id} ({request.display_name})")
    print(
        "Notebooks: "
        + ", ".join(f"{notebook.title} ({notebook.notebook_id})" for notebook in notebooks)
    )
    created_notebook_titles = [
        notebook.title for notebook in notebooks if notebook.created
    ]
    if created_notebook_titles:
        print(
            "Created NotebookLM project(s): "
            + ", ".join(created_notebook_titles)
        )
    print(f"Destination: {module_root}")
    if not request.apply:
        print("Dry run only. Re-run with --apply to create the module.")
        return 0
    for directory_name in ("Lecture", "Questions", "Transcripts"):
        (module_root / directory_name).mkdir(parents=True, exist_ok=True)
    _write_json_atomically(
        module_root / "module.json", _module_payload(request, notebooks)
    )
    print(f"Created module: {module_root}")
    return 0


def _validate_module(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    module = resolve_module(
        discover_modules(workspace, args.modules_root), args.module
    )
    for notebook in module.notebook.notebooks:
        reference = notebook.notebook_id or notebook.title
        payload = _nlm_json(["notebook", "get", reference], module.notebook.profile)
        resolved_id = (
            str(payload.get("notebook_id") or payload.get("id") or "")
            if isinstance(payload, dict)
            else ""
        )
        if notebook.notebook_id and resolved_id != notebook.notebook_id:
            raise ModuleManagerError(
                f"Configured notebook could not be verified: {reference}"
            )
    print(f"Valid module: {module.module_id} -> {module.notebook.title}")
    return 0


def _list_modules(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    for module in discover_modules(workspace, args.modules_root):
        titles = ", ".join(reference.title for reference in module.notebook.notebooks)
        print(f"{module.module_id}\t{module.display_name}\t{titles}")
    return 0


def _merge_legacy_exams(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    module = resolve_module(discover_modules(workspace, args.modules_root), args.module)
    legacy = module.paths.legacy_exams
    questions = module.paths.questions
    if not legacy.is_dir():
        print(f"No legacy Exams directory found for {module.module_id}.")
        return 0
    questions.mkdir(parents=True, exist_ok=True)
    conflicts: list[str] = []
    for source in sorted(path for path in legacy.rglob("*") if path.is_file()):
        destination = questions / source.relative_to(legacy)
        if destination.exists():
            conflicts.append(str(destination.relative_to(module.paths.root)))
    if conflicts:
        raise ModuleManagerError(
            "Cannot merge Exams; destination conflicts: " + ", ".join(conflicts)
        )
    moved = 0
    for source in sorted(path for path in legacy.rglob("*") if path.is_file()):
        destination = questions / source.relative_to(legacy)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved += 1
    print(f"Moved {moved} file(s) from Exams/ to Questions/ for {module.module_id}.")
    return 0


def _create_request(args: argparse.Namespace) -> CreateRequest:
    return CreateRequest(
        workspace=Path(args.workspace).expanduser().resolve(),
        modules_root=args.modules_root,
        module_id=args.module,
        display_name=args.display_name,
        aliases=tuple(args.alias or ()),
        notebook_ids=tuple(args.notebook_id or ()),
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
    create.add_argument("--notebook-id", action="append")
    create.add_argument("--notebook-title")
    create.add_argument("--nlm-profile")
    create.add_argument("--emoji", default="📚")
    create.add_argument("--apply", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--module", required=True)
    commands.add_parser("list")
    migrate = commands.add_parser("merge-exams")
    migrate.add_argument("--module", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            return _create_module(_create_request(args))
        if args.command == "validate":
            return _validate_module(args)
        if args.command == "merge-exams":
            return _merge_legacy_exams(args)
        return _list_modules(args)
    except (ModuleConfigError, ModuleManagerError, OSError) as error:
        print(f"[Module Error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
