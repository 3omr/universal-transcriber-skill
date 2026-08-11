#!/usr/bin/env python3
"""Load and validate independent medical-course module definitions."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODULE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ModuleConfigError(RuntimeError):
    """Raised when module discovery cannot produce one safe configuration."""


@dataclass(frozen=True)
class NotebookConfig:
    notebook_id: str
    title: str
    profile: str | None


@dataclass(frozen=True)
class ModulePaths:
    root: Path
    lecture: Path
    questions: Path
    exams: Path
    transcripts: Path


@dataclass(frozen=True)
class ModuleConfig:
    module_id: str
    display_name: str
    aliases: tuple[str, ...]
    notebook: NotebookConfig
    emoji: str
    language: str
    lecture_slides: dict[str, str]
    paths: ModulePaths


def normalize_module_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name or "").casefold().strip()
    normalized = normalized.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"[^\w\u0600-\u06ff]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def modules_root(workspace: Path, requested: str | None = None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    canonical = workspace / "modules"
    misspelled = workspace / "moduels"
    if misspelled.exists() and not canonical.exists():
        raise ModuleConfigError("Rename 'moduels' to the canonical 'modules' directory")
    return canonical


def _required_text(payload: dict[str, Any], key: str, source: Path) -> str:
    text = str(payload.get(key, "")).strip()
    if not text:
        raise ModuleConfigError(f"{source}: '{key}' is required")
    return text


def _notebook_config(payload: dict[str, Any], source: Path) -> NotebookConfig:
    notebook_payload = payload.get("notebook", {})
    if not isinstance(notebook_payload, dict):
        raise ModuleConfigError(f"{source}: 'notebook' must be an object")
    notebook_id = str(notebook_payload.get("id", "")).strip()
    title = str(notebook_payload.get("title", "")).strip()
    if not notebook_id and not title:
        raise ModuleConfigError(f"{source}: notebook id or title is required")
    raw_profile = notebook_payload.get("profile")
    profile = str(raw_profile).strip() if raw_profile else None
    return NotebookConfig(notebook_id, title, profile)


def _aliases(payload: dict[str, Any], module_id: str, display_name: str) -> tuple[str, ...]:
    raw_aliases = payload.get("aliases", [])
    if not isinstance(raw_aliases, list) or not all(
        isinstance(alias, str) and alias.strip() for alias in raw_aliases
    ):
        raise ModuleConfigError("'aliases' must be a list of non-empty strings")
    aliases = [module_id, display_name, *raw_aliases]
    return tuple(dict.fromkeys(alias.strip() for alias in aliases))


def _output_settings(payload: dict[str, Any]) -> tuple[str, str]:
    output = payload.get("output", {})
    if not isinstance(output, dict):
        raise ModuleConfigError("'output' must be an object")
    emoji = str(output.get("emoji", "📚")).strip() or "📚"
    language = str(
        output.get(
            "language", "Egyptian Arabic mixed with English medical terminology"
        )
    ).strip()
    return emoji, language


def _slide_mappings(payload: dict[str, Any]) -> dict[str, str]:
    mappings = payload.get("lecture_slides", {})
    if not isinstance(mappings, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in mappings.items()
    ):
        raise ModuleConfigError("'lecture_slides' must map recording names to paths")
    return {normalize_module_name(key): value.strip() for key, value in mappings.items()}


def _module_paths(module_root: Path) -> ModulePaths:
    return ModulePaths(
        root=module_root,
        lecture=module_root / "Lecture",
        questions=module_root / "Questions",
        exams=module_root / "Exams",
        transcripts=module_root / "Transcripts",
    )


def load_module(module_root: Path) -> ModuleConfig:
    config_path = module_root / "module.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ModuleConfigError(f"Missing module config: {config_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ModuleConfigError(f"Could not read {config_path}: {error}") from error
    if not isinstance(payload, dict):
        raise ModuleConfigError(f"{config_path}: top-level JSON must be an object")
    return _module_config(payload, module_root, config_path)


def _module_config(
    payload: dict[str, Any], module_root: Path, config_path: Path
) -> ModuleConfig:
    module_id = _required_text(payload, "module_id", config_path)
    if not MODULE_ID_PATTERN.fullmatch(module_id) or module_root.name != module_id:
        raise ModuleConfigError(
            f"{config_path}: module_id must match its lowercase kebab-case directory"
        )
    display_name = _required_text(payload, "display_name", config_path)
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise ModuleConfigError(f"{config_path}: schema_version must be 1")
    emoji, language = _output_settings(payload)
    paths = _module_paths(module_root)
    if not paths.lecture.is_dir():
        raise ModuleConfigError(f"Missing Lecture directory: {paths.lecture}")
    return ModuleConfig(
        module_id=module_id,
        display_name=display_name,
        aliases=_aliases(payload, module_id, display_name),
        notebook=_notebook_config(payload, config_path),
        emoji=emoji,
        language=language,
        lecture_slides=_slide_mappings(payload),
        paths=paths,
    )


def discover_modules(workspace: Path, requested_root: str | None = None) -> list[ModuleConfig]:
    root = modules_root(workspace, requested_root)
    if not root.is_dir():
        raise ModuleConfigError(f"Modules directory not found: {root}")
    modules = [load_module(path) for path in sorted(root.iterdir()) if path.is_dir()]
    if not modules:
        raise ModuleConfigError(f"No modules were found under {root}")
    _validate_unique_aliases(modules)
    return modules


def _validate_unique_aliases(modules: list[ModuleConfig]) -> None:
    owners: dict[str, str] = {}
    for module in modules:
        for alias in module.aliases:
            normalized = normalize_module_name(alias)
            owner = owners.get(normalized)
            if owner and owner != module.module_id:
                raise ModuleConfigError(
                    f"Module alias '{alias}' is shared by '{owner}' and '{module.module_id}'"
                )
            owners[normalized] = module.module_id


def resolve_module(modules: list[ModuleConfig], requested: str | None) -> ModuleConfig:
    if not requested:
        if len(modules) == 1:
            return modules[0]
        names = ", ".join(module.module_id for module in modules)
        raise ModuleConfigError(f"Choose a module with --module. Available: {names}")
    requested_key = normalize_module_name(requested)
    matches = [
        module
        for module in modules
        if requested_key in {normalize_module_name(alias) for alias in module.aliases}
    ]
    if len(matches) != 1:
        raise ModuleConfigError(f"Module '{requested}' did not resolve uniquely")
    return matches[0]


def configured_slide(module: ModuleConfig, recording_title: str) -> Path | None:
    relative_path = module.lecture_slides.get(normalize_module_name(recording_title))
    if not relative_path:
        return None
    slide_path = (module.paths.root / relative_path).resolve()
    try:
        slide_path.relative_to(module.paths.root.resolve())
    except ValueError as error:
        raise ModuleConfigError("Configured slide path escapes the module root") from error
    if not slide_path.is_file():
        raise ModuleConfigError(f"Configured slide file not found: {slide_path}")
    return slide_path
