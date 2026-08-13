#!/usr/bin/env python3
"""Maintain resumable state for a multi-lecture sub-agent batch."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class BatchStateError(RuntimeError):
    """Raised when a batch ledger would become inconsistent."""


STATUSES = (
    "manifest_ready",
    "queued",
    "running",
    "draft_ready",
    "accepted",
    "finalized",
    "verified",
    "blocked",
    "failed",
)

ALLOWED_TRANSITIONS = {
    "manifest_ready": {"queued", "blocked"},
    "queued": {"running", "blocked", "failed"},
    "running": {"draft_ready", "blocked", "failed"},
    "draft_ready": {"running", "accepted", "blocked", "failed"},
    "accepted": {"finalized", "failed"},
    "finalized": {"verified", "failed"},
    "blocked": {"queued"},
    "failed": {"queued"},
    "verified": set(),
}


@dataclass(frozen=True)
class LectureUnit:
    title: str
    recording_sources: tuple[str, ...]
    manifest_path: str


@dataclass(frozen=True)
class LedgerUpdate:
    lecture_key: str
    status: str
    agent_id: str | None = None
    run_id: str | None = None
    artifact_path: str | None = None
    reason: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_identity(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    return normalized.replace("\\", "/")


def lecture_key(module_id: str, unit: LectureUnit) -> str:
    identity = "\n".join(
        _normalized_identity(part)
        for part in (module_id, unit.title, *unit.recording_sources)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _manifest_unit(manifest_path: Path) -> LectureUnit:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchStateError(f"Could not read manifest {manifest_path}: {error}") from error
    title = payload.get("title") if isinstance(payload, dict) else None
    recordings = payload.get("recording_sources") if isinstance(payload, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise BatchStateError(f"Manifest requires a non-empty title: {manifest_path}")
    if not isinstance(recordings, list) or not recordings:
        raise BatchStateError(f"Manifest requires recording_sources: {manifest_path}")
    names = tuple(_recording_name(entry) for entry in recordings)
    if not all(names):
        raise BatchStateError(f"Manifest has an invalid recording source: {manifest_path}")
    return LectureUnit(title.strip(), names, str(manifest_path.resolve()))


def _recording_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    for field_name in ("source", "path", "name"):
        field_value = entry.get(field_name)
        if isinstance(field_value, str) and field_value.strip():
            return field_value.strip()
    return ""


def _validate_ownership(units: tuple[LectureUnit, ...]) -> None:
    owners: dict[str, str] = {}
    for unit in units:
        for recording in unit.recording_sources:
            recording_identity = _normalized_identity(recording)
            previous_owner = owners.get(recording_identity)
            if previous_owner:
                raise BatchStateError(
                    f"Recording '{recording}' belongs to both "
                    f"'{previous_owner}' and '{unit.title}'"
                )
            owners[recording_identity] = unit.title


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False, indent=2, sort_keys=True)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


@contextmanager
def _ledger_lock(ledger_path: Path) -> Iterator[None]:
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def create_ledger(
    module_id: str, cache_root: Path, manifest_paths: tuple[Path, ...]
) -> Path:
    units = tuple(_manifest_unit(path) for path in manifest_paths)
    if len(units) < 2:
        raise BatchStateError("A sub-agent batch requires at least two lecture manifests")
    _validate_ownership(units)
    batch_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    ledger_path = cache_root / "batches" / batch_id / "batch.json"
    timestamp = _utc_now()
    lectures = {
        lecture_key(module_id, unit): _lecture_record(unit)
        for unit in units
    }
    _atomic_write_json(
        ledger_path,
        {
            "version": 1,
            "batch_id": batch_id,
            "module": module_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "lectures": lectures,
        },
    )
    return ledger_path


def _lecture_record(unit: LectureUnit) -> dict[str, Any]:
    return {
        "title": unit.title,
        "recording_sources": list(unit.recording_sources),
        "manifest_path": unit.manifest_path,
        "status": "manifest_ready",
        "agent_id": None,
        "run_id": None,
        "artifact_path": None,
        "reason": None,
        "attempts": 0,
    }


def read_ledger(ledger_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchStateError(f"Could not read batch ledger: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("lectures"), dict):
        raise BatchStateError("Batch ledger is malformed")
    return payload


def update_ledger(ledger_path: Path, update: LedgerUpdate) -> dict[str, Any]:
    if update.status not in STATUSES:
        raise BatchStateError(f"Unknown batch status: {update.status}")
    with _ledger_lock(ledger_path):
        payload = read_ledger(ledger_path)
        lectures = payload["lectures"]
        if update.lecture_key not in lectures:
            raise BatchStateError(f"Unknown lecture key: {update.lecture_key}")
        lecture = lectures[update.lecture_key]
        current_status = str(lecture.get("status"))
        _validate_transition(current_status, update.status)
        lecture["status"] = update.status
        if update.status == "running" and current_status != "running":
            lecture["attempts"] = int(lecture.get("attempts", 0)) + 1
        _apply_update_fields(lecture, update)
        payload["updated_at"] = _utc_now()
        _atomic_write_json(ledger_path, payload)
        return payload


def _validate_transition(current_status: str, next_status: str) -> None:
    if current_status == next_status:
        return
    if next_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
        raise BatchStateError(
            f"Invalid batch transition: {current_status} -> {next_status}"
        )


def _apply_update_fields(lecture: dict[str, Any], update: LedgerUpdate) -> None:
    supplied_fields = {
        "agent_id": update.agent_id,
        "run_id": update.run_id,
        "artifact_path": update.artifact_path,
        "reason": update.reason,
    }
    for field_name, field_value in supplied_fields.items():
        if field_value is not None:
            lecture[field_name] = field_value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--module", required=True)
    initialize.add_argument("--cache-root", required=True)
    initialize.add_argument("--manifest", action="append", required=True)
    update = commands.add_parser("update")
    update.add_argument("--ledger", required=True)
    update.add_argument("--lecture-key", required=True)
    update.add_argument("--status", choices=STATUSES, required=True)
    update.add_argument("--agent-id")
    update.add_argument("--run-id")
    update.add_argument("--artifact-path")
    update.add_argument("--reason")
    show = commands.add_parser("show")
    show.add_argument("--ledger", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "init":
            ledger_path = create_ledger(
                arguments.module,
                Path(arguments.cache_root).expanduser().resolve(),
                tuple(Path(path).expanduser().resolve() for path in arguments.manifest),
            )
            payload = read_ledger(ledger_path)
            print(json.dumps({"ledger": str(ledger_path), **payload}, ensure_ascii=False))
            return 0
        if arguments.command == "update":
            payload = update_ledger(
                Path(arguments.ledger).expanduser().resolve(),
                LedgerUpdate(
                    lecture_key=arguments.lecture_key,
                    status=arguments.status,
                    agent_id=arguments.agent_id,
                    run_id=arguments.run_id,
                    artifact_path=arguments.artifact_path,
                    reason=arguments.reason,
                ),
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        print(
            json.dumps(
                read_ledger(Path(arguments.ledger).expanduser().resolve()),
                ensure_ascii=False,
            )
        )
        return 0
    except BatchStateError as error:
        print(f"[Batch State Error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
