#!/usr/bin/env python3
"""Everything that talks to NotebookLM through the `nlm` CLI.

This is the layer the rest of the engine was silently built on top of: reading
config.json, finding and invoking the `nlm` binary, resolving a notebook by
name, listing what a notebook already holds, and caching that listing so a
read-only audit and the run that follows it do not both pay for it.

Extracted first when the engine was split because it is the project's single
external dependency and its single largest risk -- `nlm` is a reverse-engineered
client for a service with no public API. Everything specific to that being true
is now in one file, which is what makes a second backend possible at all (see
engines/).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from atomic_io import _atomic_write_json
from source_naming import normalize_source_key, normalize_source_stem
from transcriber_models import NlmError, NotebookTarget, Phase0Error, RemoteSource

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


DEFAULT_CONFIG: dict[str, Any] = {
    # The tool is subject-agnostic; the module supplies the real subject.
    "default_subject": "",
    "notebook_ids": {},
    "nlm_executable": "nlm",
    "nlm_profile": None,
    "modules_root": "modules",
    "transcripts_root": "Transcripts",
    "emoji_by_subject": {},
}


def load_config() -> dict[str, Any]:
    """Read config.json, saying so loudly when it exists but cannot be used.

    A trailing comma used to be swallowed silently and the run continued on
    defaults, so a user who had configured an nlm profile or a modules root
    never learned their file was ignored.
    """
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as config_file:
            loaded_config = json.load(config_file)
    except json.JSONDecodeError as error:
        print(
            f"[!] {CONFIG_PATH} is not valid JSON ({error}); falling back to "
            "defaults. Every setting in that file is being ignored.",
            file=sys.stderr,
        )
        return dict(DEFAULT_CONFIG)
    except OSError as error:
        print(
            f"[!] {CONFIG_PATH} could not be read ({error}); falling back to "
            "defaults.",
            file=sys.stderr,
        )
        return dict(DEFAULT_CONFIG)
    if not isinstance(loaded_config, dict):
        print(
            f"[!] {CONFIG_PATH} must contain a JSON object, not "
            f"{type(loaded_config).__name__}; falling back to defaults.",
            file=sys.stderr,
        )
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **loaded_config}


def get_project_dir() -> str:
    cwd = os.getcwd()
    markers = ("Lecture", "Transcripts", "Questions", "Exams", "المحاضرات")
    if any(os.path.exists(os.path.join(cwd, marker)) for marker in markers):
        return cwd
    parent = os.path.dirname(SCRIPT_DIR)
    if any(os.path.exists(os.path.join(parent, marker)) for marker in markers):
        return parent
    return parent


def _extract_notebook_uuid(notebook_reference: str) -> str:
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        notebook_reference or "",
    )
    return match.group(0).lower() if match else ""


def _find_nlm_executable(config: dict[str, Any] | None = None) -> str:
    configured = str((config or {}).get("nlm_executable") or "nlm")
    if os.path.isabs(configured) and os.path.isfile(configured):
        return configured
    discovered = shutil.which(configured)
    if discovered:
        return discovered
    raise Phase0Error(f"The nlm CLI executable '{configured}' was not found")


def _nlm_command(config: dict[str, Any], arguments: list[str]) -> list[str]:
    command = [_find_nlm_executable(config), *arguments]
    profile = config.get("nlm_profile")
    if profile:
        command.extend(["--profile", str(profile)])
    return command


INVENTORY_CACHE_TTL_SECONDS = 180
# nlm verbs that change what a notebook contains. Any of them makes a cached
# source inventory wrong, so the cache is dropped the moment one succeeds.
MUTATING_NLM_VERBS = frozenset(
    {"add", "create", "delete", "import", "remove", "rm", "upload"}
)
_INVENTORY_CACHE_ROOT: Path | None = None


def set_inventory_cache_root(sources_root: str | None) -> None:
    """Point the remote-inventory cache at this run's module cache directory.

    The launcher runs the read-only audit and the real run as two processes, so
    the cache has to live on disk for the second one to benefit from the first.
    """
    global _INVENTORY_CACHE_ROOT
    if not sources_root or os.environ.get("TRANSCRIBER_DISABLE_INVENTORY_CACHE"):
        _INVENTORY_CACHE_ROOT = None
        return
    _INVENTORY_CACHE_ROOT = Path(sources_root) / ".transcriber-cache" / "inventory"


def _inventory_cache_ttl() -> int:
    raw = os.environ.get("TRANSCRIBER_INVENTORY_CACHE_TTL")
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return INVENTORY_CACHE_TTL_SECONDS


def _inventory_cache_file(notebook_uuid: str) -> Path | None:
    if _INVENTORY_CACHE_ROOT is None or not notebook_uuid:
        return None
    key = hashlib.sha256(notebook_uuid.encode("utf-8")).hexdigest()[:16]
    return _INVENTORY_CACHE_ROOT / f"sources-{key}.json"


def _read_cached_inventory(notebook_uuid: str) -> Any | None:
    path = _inventory_cache_file(notebook_uuid)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(payload["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if age < 0 or age > _inventory_cache_ttl():
        return None
    return payload.get("inventory")


def _store_cached_inventory(notebook_uuid: str, inventory: Any) -> None:
    path = _inventory_cache_file(notebook_uuid)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, {"fetched_at": time.time(), "inventory": inventory})
    except OSError:
        # A cache that cannot be written is a missed optimisation, never a
        # reason to fail the run.
        pass


def invalidate_inventory_cache() -> None:
    if _INVENTORY_CACHE_ROOT is None:
        return
    try:
        shutil.rmtree(_INVENTORY_CACHE_ROOT)
    except OSError:
        pass


def _run_nlm_json(
    config: dict[str, Any],
    arguments: list[str],
    timeout_seconds: int,
    operation: str,
) -> Any:
    command = _nlm_command(config, [*arguments, "--json"])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise NlmError(f"{operation} timed out") from error
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise NlmError(f"{operation} failed: {message[:500]}")
    if MUTATING_NLM_VERBS.intersection(arguments):
        invalidate_inventory_cache()
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise NlmError(f"{operation} returned invalid JSON") from error


def _notebook_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("notebooks")
    if not isinstance(payload, list):
        raise Phase0Error("nlm notebook list returned an unexpected payload")
    entries = [entry for entry in payload if isinstance(entry, dict) and entry.get("id")]
    if not entries:
        raise Phase0Error("nlm notebook list returned no notebooks")
    return entries


def _matching_notebook_entries(
    entries: list[dict[str, Any]], requested_id: str, subject: str
) -> list[dict[str, Any]]:
    requested_uuid = _extract_notebook_uuid(requested_id)
    requested_key = normalize_source_key(requested_id)
    exact = [
        entry
        for entry in entries
        if str(entry.get("id", "")).casefold() == requested_id.casefold()
        or (requested_uuid and str(entry.get("id", "")).casefold() == requested_uuid)
    ]
    if exact:
        return exact
    title_key = requested_key or normalize_source_key(subject)
    return [
        entry
        for entry in entries
        if normalize_source_key(str(entry.get("title", ""))) == title_key
    ]


def _unique_notebook_summary(
    matches: list[dict[str, Any]], requested_id: str
) -> dict[str, Any]:
    if not matches:
        raise Phase0Error(f"Notebook '{requested_id}' was not found by nlm")
    if len(matches) > 1:
        raise Phase0Error(f"Notebook '{requested_id}' resolved ambiguously")
    return matches[0]


def _notebook_target(payload: Any, subject: str) -> NotebookTarget:
    if not isinstance(payload, dict):
        raise Phase0Error("nlm notebook get returned an unexpected payload")
    notebook_id = str(payload.get("notebook_id") or payload.get("id") or "").strip()
    if not notebook_id:
        raise Phase0Error("nlm notebook get returned no notebook id")
    url = str(payload.get("url") or "").strip()
    if not url:
        url = f"https://notebooklm.google.com/notebook/{notebook_id}"
    return NotebookTarget(
        library_id=notebook_id,
        notebook_uuid=notebook_id,
        url=url,
        name=str(payload.get("title") or subject),
    )


def resolve_notebook(
    config: dict[str, Any], requested_id: str, subject: str
) -> NotebookTarget:
    entries = _notebook_entries(
        _run_nlm_json(config, ["notebook", "list"], 60, "nlm notebook list")
    )
    summary = _unique_notebook_summary(
        _matching_notebook_entries(entries, requested_id, subject), requested_id
    )
    notebook_id = str(summary.get("id", ""))
    payload = _run_nlm_json(
        config, ["notebook", "get", notebook_id], 60, "nlm notebook get"
    )
    return _notebook_target(payload, subject)


def resolve_notebooks(
    config: dict[str, Any], requested_ids: tuple[str, ...], subject: str
) -> tuple[NotebookTarget, ...]:
    if not requested_ids:
        raise Phase0Error("At least one NotebookLM project is required")
    resolved: list[NotebookTarget] = []
    for requested_id in requested_ids:
        notebook = resolve_notebook(config, requested_id, subject)
        if notebook.notebook_uuid not in {item.notebook_uuid for item in resolved}:
            resolved.append(notebook)
    return tuple(resolved)


def _dictionary_entries(payload: list[Any]) -> list[dict[str, Any]]:
    return [source_entry for source_entry in payload if isinstance(source_entry, dict)]


def _mapped_source_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mapped_entries = _dictionary_entries(list(payload.values()))
    title_keys = ("title", "name", "display_name", "source_name")
    if mapped_entries and all(
        any(name_key in mapped_entry for name_key in title_keys)
        for mapped_entry in mapped_entries
    ):
        return mapped_entries
    return []


def _source_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _dictionary_entries(payload)
    if not isinstance(payload, dict):
        return []
    for key in ("sources", "items", "data", "results"):
        nested_payload = payload.get(key)
        if isinstance(nested_payload, list):
            return _dictionary_entries(nested_payload)
        if isinstance(nested_payload, dict):
            nested = _source_items(nested_payload)
            if nested:
                return nested
    return _mapped_source_entries(payload)


def _remote_source_inventory(
    notebook_uuid: str, config: dict[str, Any] | None = None
) -> Any:
    cached = _read_cached_inventory(notebook_uuid)
    if cached is not None:
        return cached
    try:
        inventory = _run_nlm_json(
            config or {},
            ["source", "list", notebook_uuid],
            120,
            "nlm source list",
        )
    except NlmError as error:
        raise Phase0Error(str(error)) from error
    _store_cached_inventory(notebook_uuid, inventory)
    return inventory


def _remote_source_title(source_entry: dict[str, Any]) -> str:
    return str(
        source_entry.get("title")
        or source_entry.get("name")
        or source_entry.get("display_name")
        or source_entry.get("source_name")
        or ""
    ).strip()


def _normalize_remote_status(value: Any) -> str:
    """Normalize NotebookLM's named and numeric source states."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return str(value).casefold()
    if isinstance(value, (int, float)):
        numeric = int(value)
        return {
            1: "processing",
            2: "ready",
            3: "error",
            5: "preparing",
        }.get(numeric, str(numeric))
    return str(value).strip().casefold()


def list_remote_sources(
    notebook_uuid: str, config: dict[str, Any] | None = None
) -> list[RemoteSource]:
    remote_sources: list[RemoteSource] = []
    inventory = _remote_source_inventory(notebook_uuid, config)
    for source_entry in _source_items(inventory):
        title = _remote_source_title(source_entry)
        if not title:
            continue
        remote_sources.append(
            RemoteSource(
                source_id=str(
                    source_entry.get("id") or source_entry.get("source_id") or ""
                ),
                title=title,
                normalized_name=normalize_source_key(title),
                normalized_stem=normalize_source_stem(title),
                source_type=str(
                    source_entry.get("type") or source_entry.get("source_type") or ""
                ),
                notebook_uuid=notebook_uuid,
                content_hash=str(
                    source_entry.get("sha256")
                    or source_entry.get("hash")
                    or source_entry.get("checksum")
                    or ""
                ).strip().casefold(),
                status=_normalize_remote_status(
                    source_entry.get("status")
                    if source_entry.get("status") is not None
                    else source_entry.get("state")
                    if source_entry.get("state") is not None
                    else source_entry.get("processing_status")
                ),
            )
        )
    return remote_sources


__all__ = [
    "CONFIG_PATH",
    "DEFAULT_CONFIG",
    "INVENTORY_CACHE_TTL_SECONDS",
    "MUTATING_NLM_VERBS",
    "SCRIPT_DIR",
    "get_project_dir",
    "invalidate_inventory_cache",
    "list_remote_sources",
    "load_config",
    "resolve_notebook",
    "resolve_notebooks",
    "set_inventory_cache_root",
]
