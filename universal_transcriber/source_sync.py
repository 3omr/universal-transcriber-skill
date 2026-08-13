"""Agent-supervised, module-wide NotebookLM source synchronization.

The Agent supplies the decisions in a manifest. This module inventories,
prepares, deduplicates, uploads, and records those decisions without inferring
whether a source belongs in a particular lecture transcript.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

try:
    from .source_preparation import ACTION_NAMES, PreparedSource, prepare_manifest_sources
except ImportError:  # Loaded by the standalone launcher from the engine directory.
    from source_preparation import ACTION_NAMES, PreparedSource, prepare_manifest_sources


SYNC_VERSION = 1
STATE_RELATIVE_PATH = Path(".transcriber-cache/source-sync/state.json")
RUNS_RELATIVE_PATH = Path(".transcriber-cache/source-sync/runs")
SOURCE_ROOTS = ("Lecture", "Questions", "Exams")


class SourceSyncError(RuntimeError):
    """Raised when an Agent sync decision is invalid or cannot be executed."""


@dataclass(frozen=True)
class SyncDecision:
    path: str
    role: str
    action: str
    upload: bool
    raw: dict[str, Any]


@dataclass(frozen=True)
class SyncManifest:
    module: str
    notebook_targets: tuple[str, ...]
    sources: tuple[SyncDecision, ...]
    agent_approved: bool
    path: str


@dataclass
class NotebookSourceStatus:
    notebook_id: str
    notebook_name: str
    status: str
    remote_source_id: str = ""
    remote_title: str = ""
    error: str = ""


@dataclass
class SyncedSource:
    path: str
    role: str
    action: str
    upload: bool
    status: str
    original_sha256: str = ""
    prepared_sha256: str = ""
    prepared_path: str = ""
    upload_extension: str = ""
    notes: str = ""
    notebooks: list[NotebookSourceStatus] = field(default_factory=list)


@dataclass
class SourceSyncReport:
    module: str
    execute: bool
    sources: list[SyncedSource] = field(default_factory=list)
    unreviewed_local_sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    uploaded_count: int = 0
    prepared_count: int = 0

    @property
    def status(self) -> str:
        if self.errors and not self.sources:
            return "blocked"
        if self.errors or any(source.status in {"failed", "ambiguous", "changed"} for source in self.sources):
            return "partial"
        return "completed" if self.execute else "planned"


@dataclass(frozen=True)
class SourceSyncRequest:
    engine: ModuleType
    config: dict[str, Any]
    module_id: str
    source_root: Path
    notebooks: tuple[Any, ...]
    manifest_path: str | Path


@dataclass(frozen=True)
class NotebookSyncRequest:
    engine: ModuleType
    config: dict[str, Any]
    notebook: Any
    source: Any
    execute: bool
    allow_upload: bool


@dataclass(frozen=True)
class DecisionSyncRequest:
    runtime: SourceSyncRequest
    decision: SyncDecision
    prepared: PreparedSource
    source: Any
    notebooks: tuple[Any, ...]
    execute: bool


@dataclass(frozen=True)
class PreparedSyncContext:
    runtime: SourceSyncRequest
    notebooks: tuple[Any, ...]
    execute: bool
    prepared_by_path: dict[str, PreparedSource]
    selected_sources: dict[str, Any]


def _normalize_relative(path: str) -> str:
    return path.replace("\\", "/").strip(" ./").casefold()


def _manifest_path(item: dict[str, Any]) -> str:
    for key in ("path", "source", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\\", "/")
    return ""


def _manifest_json(path: str | Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceSyncError(f"Could not read source sync manifest: {error}") from error
    if not isinstance(payload, dict):
        raise SourceSyncError("Source sync manifest must contain a JSON object")
    return manifest_path, payload


def _notebook_targets(payload: dict[str, Any]) -> tuple[str, ...]:
    raw_targets = payload.get("notebook_targets", [])
    if not isinstance(raw_targets, list) or not all(
        isinstance(target, str) and target.strip() for target in raw_targets
    ):
        raise SourceSyncError("notebook_targets must be a non-empty string list")
    return tuple(dict.fromkeys(target.strip() for target in raw_targets))


def _sync_decision(raw_source: Any, seen: set[str]) -> SyncDecision:
    if not isinstance(raw_source, dict):
        raise SourceSyncError("Every sync source must be an object")
    relative_path = _manifest_path(raw_source)
    normalized = _normalize_relative(relative_path)
    allowed_roots = tuple(f"{root.casefold()}/" for root in SOURCE_ROOTS)
    if not relative_path or normalized.startswith("../") or "/../" in normalized:
        raise SourceSyncError(f"Invalid module source path: {relative_path or raw_source}")
    if not normalized.startswith(allowed_roots):
        raise SourceSyncError(
            f"Source must stay under Lecture/, Questions/, or Exams/: {relative_path}"
        )
    if normalized in seen:
        raise SourceSyncError(f"Source sync manifest repeats: {relative_path}")
    seen.add(normalized)
    action = str(raw_source.get("action", "auto")).strip().casefold()
    if action not in ACTION_NAMES:
        raise SourceSyncError(f"Unsupported action '{action}' for {relative_path}")
    upload = raw_source.get("upload", action != "ignore")
    if not isinstance(upload, bool):
        raise SourceSyncError(f"upload must be true or false: {relative_path}")
    if action == "ignore" and upload:
        raise SourceSyncError(f"Ignored source cannot be uploaded: {relative_path}")
    return SyncDecision(
        relative_path,
        str(raw_source.get("role") or raw_source.get("type") or "reference").strip(),
        action,
        upload,
        dict(raw_source),
    )


def _sync_decisions(payload: dict[str, Any]) -> tuple[SyncDecision, ...]:
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise SourceSyncError("sources must be a non-empty object list")
    seen: set[str] = set()
    return tuple(_sync_decision(raw_source, seen) for raw_source in raw_sources)


def load_sync_manifest(path: str | Path, module_id: str) -> SyncManifest:
    manifest_path, payload = _manifest_json(path)
    if payload.get("version") != SYNC_VERSION:
        raise SourceSyncError(f"Source sync manifest version must be {SYNC_VERSION}")
    declared_module = str(payload.get("module", "")).strip()
    if declared_module.casefold() != module_id.casefold():
        raise SourceSyncError(
            f"Source sync manifest targets module '{declared_module}', not '{module_id}'"
        )
    return SyncManifest(
        module=declared_module,
        notebook_targets=_notebook_targets(payload),
        sources=_sync_decisions(payload),
        agent_approved=payload.get("agent_approved") is True,
        path=str(manifest_path),
    )


def discover_local_sources(source_root: Path) -> list[str]:
    discovered: list[str] = []
    for root_name in SOURCE_ROOTS:
        directory = source_root / root_name
        if not directory.is_dir():
            continue
        discovered.extend(
            path.relative_to(source_root).as_posix()
            for path in directory.rglob("*")
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(directory).parts)
        )
    return sorted(discovered, key=str.casefold)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _manifest_payload(manifest: SyncManifest) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for decision in manifest.sources:
        item = dict(decision.raw)
        item["path"] = decision.path
        item["role"] = decision.role
        item["action"] = decision.action
        sources.append(item)
    return {"sources": sources}


def _selected_local_sources(
    engine: ModuleType,
    source_root: Path,
    prepared: dict[str, PreparedSource],
    manifest: SyncManifest,
) -> dict[str, Any]:
    local_sources = engine.scan_local_sources(
        str(source_root),
        assessment_sources=_assessment_sources(manifest),
        prepared_sources=prepared,
    )
    by_path = {
        _normalize_relative(source.relative_path): source for source in local_sources
    }
    selected: dict[str, Any] = {}
    for decision in manifest.sources:
        source = by_path.get(_normalize_relative(decision.path))
        if source is not None:
            selected[_normalize_relative(decision.path)] = source
    return selected


def _assessment_sources(manifest: SyncManifest) -> tuple[dict[str, Any], ...]:
    sources: list[dict[str, Any]] = []
    for decision in manifest.sources:
        if not _normalize_relative(decision.path).startswith("questions/"):
            continue
        source = dict(decision.raw)
        source["path"] = decision.path
        source["type"] = decision.role
        sources.append(source)
    return tuple(sources)


def _matching_remote(engine: ModuleType, source: Any, remote_sources: list[Any]) -> list[Any]:
    return [remote for remote in remote_sources if engine._remote_matches_local_name(source, remote)]


def _ready_exact_matches(engine: ModuleType, source: Any, remote_sources: list[Any]) -> list[Any]:
    return [
        remote
        for remote in remote_sources
        if engine._remote_source_is_ready(remote)
        and engine._source_exists_remotely(source, [remote])
    ]


def _notebook_status(request: NotebookSyncRequest) -> tuple[NotebookSourceStatus, bool]:
    engine = request.engine
    config = request.config
    notebook = request.notebook
    source = request.source
    remote_sources = engine.list_remote_sources(notebook.notebook_uuid, config)
    exact = _ready_exact_matches(engine, source, remote_sources)
    if len(exact) == 1:
        remote = exact[0]
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "unchanged", remote.source_id, remote.title), False
    if len(exact) > 1:
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "ambiguous", error="More than one ready remote source matches"), False
    same_name = _matching_remote(engine, source, remote_sources)
    ready_same_name = [remote for remote in same_name if engine._remote_source_is_ready(remote)]
    if source.preparation_action == "use_remote" and len(ready_same_name) == 1:
        remote = ready_same_name[0]
        return NotebookSourceStatus(
            notebook.notebook_uuid,
            notebook.name,
            "accepted-remote",
            remote.source_id,
            remote.title,
        ), False
    if source.preparation_action == "use_remote" and len(ready_same_name) > 1:
        return NotebookSourceStatus(
            notebook.notebook_uuid,
            notebook.name,
            "ambiguous",
            error="More than one remote source is available for use_remote",
        ), False
    if ready_same_name and source.prepared_sha256 and any(remote.content_hash for remote in ready_same_name):
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "changed", error="Remote title matches but its content hash differs; Agent replacement decision required"), False
    processing_status = _processing_remote_status(request, same_name)
    if processing_status:
        return processing_status
    return _new_remote_status(request)


def _processing_remote_status(
    request: NotebookSyncRequest, same_name: list[Any]
) -> tuple[NotebookSourceStatus, bool] | None:
    processing = [
        remote for remote in same_name if not request.engine._remote_source_is_ready(remote)
    ]
    if not processing:
        return None
    remote = processing[0]
    if not request.execute:
        return NotebookSourceStatus(
            request.notebook.notebook_uuid,
            request.notebook.name,
            "processing",
            remote.source_id,
            remote.title,
        ), False
    try:
        refreshed = request.engine._refreshed_inventory_with(
            request.config, request.notebook, request.source
        )
    except request.engine.TranscriberError as error:
        return NotebookSourceStatus(
            request.notebook.notebook_uuid,
            request.notebook.name,
            "failed",
            error=str(error),
        ), False
    exact = _ready_exact_matches(request.engine, request.source, refreshed)
    if not exact:
        return None
    ready = exact[0]
    return NotebookSourceStatus(
        request.notebook.notebook_uuid,
        request.notebook.name,
        "unchanged",
        ready.source_id,
        ready.title,
    ), False


def _new_remote_status(request: NotebookSyncRequest) -> tuple[NotebookSourceStatus, bool]:
    engine = request.engine
    notebook = request.notebook
    source = request.source
    if not request.allow_upload:
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "not-approved"), False
    if source.upload_extension not in engine.NLM_UPLOAD_EXTENSIONS:
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "failed", error=f"Unsupported upload extension: {source.upload_extension}"), False
    if not request.execute:
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "planned-upload"), False
    try:
        outcome = engine._upload_source_with_retries(request.config, notebook, source)
    except engine.TranscriberError as error:
        return NotebookSourceStatus(notebook.notebook_uuid, notebook.name, "failed", error=str(error)), False
    exact = _ready_exact_matches(engine, source, outcome.remote_sources)
    remote = exact[0] if exact else None
    return NotebookSourceStatus(
        notebook.notebook_uuid,
        notebook.name,
        "uploaded" if outcome.uploaded_by_run else "unchanged",
        remote.source_id if remote else "",
        remote.title if remote else "",
    ), outcome.uploaded_by_run


def _source_status(notebooks: list[NotebookSourceStatus], preparation_status: str) -> str:
    statuses = {item.status for item in notebooks}
    if "failed" in statuses:
        return "failed"
    if "ambiguous" in statuses:
        return "ambiguous"
    if "changed" in statuses:
        return "changed"
    if preparation_status == "planned" or "planned-upload" in statuses:
        return "planned"
    if "uploaded" in statuses:
        return "uploaded"
    if "accepted-remote" in statuses:
        return "accepted-remote"
    if statuses == {"not-approved"}:
        return "not-approved"
    return "unchanged"


def _state_payload(report: SourceSyncReport, manifest: SyncManifest) -> dict[str, Any]:
    return {
        "version": SYNC_VERSION,
        "module": report.module,
        "status": report.status,
        "synced_at": time.time(),
        "manifest_path": manifest.path,
        "manifest_sha256": hashlib.sha256(Path(manifest.path).read_bytes()).hexdigest(),
        "sources": [asdict(source) for source in report.sources],
        "unreviewed_local_sources": report.unreviewed_local_sources,
        "errors": report.errors,
    }


def _write_sync_state(source_root: Path, report: SourceSyncReport, manifest: SyncManifest) -> None:
    payload = _state_payload(report, manifest)
    _atomic_json(source_root / STATE_RELATIVE_PATH, payload)
    run_id = time.strftime("%Y%m%d%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    _atomic_json(source_root / RUNS_RELATIVE_PATH / f"{run_id}.json", payload)


def _selected_notebooks(request: SourceSyncRequest, manifest: SyncManifest) -> tuple[Any, ...]:
    configured = {notebook.notebook_uuid: notebook for notebook in request.notebooks}
    selected: list[Any] = []
    for requested_id in manifest.notebook_targets:
        notebook = configured.get(requested_id)
        if notebook is None:
            raise SourceSyncError(
                f"Manifest notebook target is not configured for this module: {requested_id}"
            )
        selected.append(notebook)
    return tuple(selected)


def _unreviewed_paths(source_root: Path, manifest: SyncManifest) -> list[str]:
    reviewed = {_normalize_relative(decision.path) for decision in manifest.sources}
    return [
        path
        for path in discover_local_sources(source_root)
        if _normalize_relative(path) not in reviewed
    ]


def _incomplete_source(
    decision: SyncDecision, prepared: PreparedSource | None
) -> SyncedSource | None:
    if prepared is None:
        return SyncedSource(
            decision.path,
            decision.role,
            decision.action,
            decision.upload,
            "failed",
            notes="Preparation did not produce a source",
        )
    common = (
        decision.path,
        decision.role,
        prepared.action,
        decision.upload,
        prepared.original_sha256,
        prepared.prepared_sha256,
        prepared.prepared_path,
        prepared.upload_extension,
        prepared.notes,
    )
    if decision.action == "ignore":
        return SyncedSource(common[0], common[1], common[2], False, "ignored", *common[4:])
    if prepared.status not in {"ready", "planned"}:
        return SyncedSource(*common[:4], "failed", *common[4:])
    return None


def _sync_ready_source(request: DecisionSyncRequest) -> tuple[SyncedSource, int]:
    notebook_statuses: list[NotebookSourceStatus] = []
    uploaded_count = 0
    for notebook in request.notebooks:
        status, uploaded = _notebook_status(
            NotebookSyncRequest(
                request.runtime.engine,
                request.runtime.config,
                notebook,
                request.source,
                request.execute,
                request.decision.upload,
            )
        )
        notebook_statuses.append(status)
        uploaded_count += int(uploaded)
    prepared = request.prepared
    return SyncedSource(
        request.decision.path,
        request.decision.role,
        prepared.action,
        request.decision.upload,
        _source_status(notebook_statuses, prepared.status),
        prepared.original_sha256,
        prepared.prepared_sha256,
        prepared.prepared_path,
        prepared.upload_extension,
        prepared.notes,
        notebook_statuses,
    ), uploaded_count


def _sync_one_decision(
    context: PreparedSyncContext, decision: SyncDecision
) -> tuple[SyncedSource, int]:
    key = _normalize_relative(decision.path)
    prepared = context.prepared_by_path.get(key)
    incomplete = _incomplete_source(decision, prepared)
    if incomplete:
        return incomplete, 0
    source = context.selected_sources.get(key)
    if source is None:
        return SyncedSource(
            decision.path,
            decision.role,
            prepared.action,
            decision.upload,
            "failed",
            notes="Prepared source was not found in the local inventory",
        ), 0
    return _sync_ready_source(
        DecisionSyncRequest(
            context.runtime,
            decision,
            prepared,
            source,
            context.notebooks,
            context.execute,
        )
    )


def _append_synced_sources(
    report: SourceSyncReport,
    context: PreparedSyncContext,
    decisions: tuple[SyncDecision, ...],
) -> None:
    for decision in decisions:
        synced, uploaded_count = _sync_one_decision(context, decision)
        report.sources.append(synced)
        report.uploaded_count += uploaded_count


def _run_source_sync(request: SourceSyncRequest, mode: str) -> SourceSyncReport:
    execute = mode == "apply"
    manifest = load_sync_manifest(request.manifest_path, request.module_id)
    if execute and not manifest.agent_approved:
        raise SourceSyncError("Apply requires agent_approved: true in the source sync manifest")
    report = SourceSyncReport(request.module_id, execute)
    report.unreviewed_local_sources = _unreviewed_paths(request.source_root, manifest)
    if report.unreviewed_local_sources:
        report.errors.append(
            "Agent manifest does not classify every local source: "
            + ", ".join(report.unreviewed_local_sources)
        )
    preparation = prepare_manifest_sources(
        request.source_root, _manifest_payload(manifest), execute=execute
    )
    report.prepared_count = preparation.mutation_count
    report.errors.extend(preparation.blocking_errors)
    selected = _selected_local_sources(
        request.engine, request.source_root, preparation.by_relative_path, manifest
    )
    context = PreparedSyncContext(
        request,
        _selected_notebooks(request, manifest),
        execute,
        preparation.by_relative_path,
        selected,
    )
    _append_synced_sources(report, context, manifest.sources)
    if execute:
        _write_sync_state(request.source_root, report, manifest)
    return report


def audit_source_sync(request: SourceSyncRequest) -> SourceSyncReport:
    return _run_source_sync(request, mode="audit")


def apply_source_sync(request: SourceSyncRequest) -> SourceSyncReport:
    return _run_source_sync(request, mode="apply")


def run_source_sync(
    request: SourceSyncRequest, *, execute: bool = False
) -> SourceSyncReport:
    """Compatibility entry point for Agent callers choosing audit or apply."""
    return apply_source_sync(request) if execute else audit_source_sync(request)


def render_source_sync_report(report: SourceSyncReport) -> str:
    lines = [
        "\n=== Module Source Sync ===",
        f"Module: {report.module}",
        f"Mode: {'apply' if report.execute else 'audit-only'}",
        f"Status: {report.status}",
        f"Sources reviewed: {len(report.sources)}",
        f"Preparation mutations: {report.prepared_count}",
        f"Uploaded now: {report.uploaded_count}",
    ]
    for source in report.sources:
        lines.append(f"[{source.status.upper()}] {source.path}: {source.action} -> {source.upload_extension or 'n/a'}")
        for notebook in source.notebooks:
            lines.append(f"  - {notebook.notebook_name}: {notebook.status}{f' ({notebook.error})' if notebook.error else ''}")
    lines.extend(f"[BLOCKING] {error}" for error in report.errors)
    lines.append("=== End Module Source Sync ===\n")
    return "\n".join(lines)


def source_sync_preflight(source_root: Path) -> list[str]:
    """Return local changes since the last successful Agent-supervised sync."""
    state_path = source_root / STATE_RELATIVE_PATH
    if not state_path.is_file():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["Source sync state is unreadable; run --sync-sources --audit-only"]
    issues: list[str] = []
    if state.get("status") != "completed":
        issues.append("last source sync did not complete; Agent review is required")
    recorded = {
        _normalize_relative(item.get("path", "")): item
        for item in state.get("sources", [])
        if isinstance(item, dict)
    }
    current_paths: set[str] = set()
    for relative_path in discover_local_sources(source_root):
        key = _normalize_relative(relative_path)
        current_paths.add(key)
        item = recorded.get(key)
        if item is None:
            issues.append(f"new source requires Agent review: {relative_path}")
            continue
        path = source_root / relative_path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("original_sha256"):
            issues.append(f"changed source requires Agent review: {relative_path}")
    for key, item in recorded.items():
        if key not in current_paths and item.get("status") != "ignored":
            issues.append(f"removed source requires Agent review: {item.get('path', key)}")
    return issues
