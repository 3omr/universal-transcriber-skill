#!/usr/bin/env python3
"""Isolated, non-cascading Checkpoint and Recovery Manager for Universal Medical Transcriber."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from models import (
    PHASE_LABELS,
    PHASE_ORDER,
    PHASE_SUCCESS_STATUSES,
    PhaseCheckpointUpdate,
    PhaseValidationError,
    PipelineContext,
    QueryResult,
    RecoveryBundle,
    RunRequest,
    TranscriberError,
)
from prompts import repair_instructions


class CheckpointError(TranscriberError):
    """Raised when checkpoint state or recovery is invalid."""


def _phase_slug(phase: str) -> str:
    return phase.replace("_", "-")


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        delete=False,
    )
    try:
        temp.write(text)
        temp.flush()
        os.fsync(temp.fileno())
        temp.close()
        os.replace(temp.name, target)
    finally:
        if os.path.exists(temp.name):
            os.unlink(temp.name)


def _atomic_write_json(target: Path, payload: Any) -> None:
    _atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _source_quarantine_payload(source_quarantine: tuple[str, ...]) -> list[str]:
    return list(source_quarantine)


def save_phase_checkpoint(update: PhaseCheckpointUpdate) -> None:
    """Save the outcome of a phase without touching or invalidating other phases."""
    checkpoint = update.checkpoint
    checkpoint.setdefault("phases", {})[update.phase] = update.status
    checkpoint.setdefault("phase_errors", {})[update.phase] = list(update.errors)
    checkpoint.setdefault("source_quarantine", {})[update.phase] = (
        _source_quarantine_payload(update.source_quarantine)
    )
    if update.answer:
        suffix = (
            "repaired"
            if update.status == "repaired"
            else "validated"
            if update.status == "validated"
            else "failed"
        )
        file_name = f"phase-{_phase_slug(update.phase)}.{suffix}.md"
        _atomic_write_text(update.run_dir / file_name, update.answer)
        checkpoint.setdefault("phase_files", {})[update.phase] = file_name
    checkpoint["resume_from"] = update.phase
    _atomic_write_json(update.run_dir / "checkpoint.json", checkpoint)


def write_recovery_bundle(bundle: RecoveryBundle) -> None:
    """Create agent-side recovery files for a failed phase."""
    prefix = f"phase-{_phase_slug(bundle.phase)}"
    if bundle.answer:
        _atomic_write_text(bundle.run_dir / f"{prefix}-response.failed.md", bundle.answer)
    _atomic_write_json(
        bundle.run_dir / f"{prefix}-sources.json",
        {
            "source_names": list(bundle.source_names),
            "source_quarantine": _source_quarantine_payload(
                bundle.source_quarantine
            ),
        },
    )
    _atomic_write_json(bundle.run_dir / f"{prefix}-errors.json", {
        "phase": bundle.phase,
        "errors": list(bundle.errors),
    })
    recovery_prompt = (
        f"# Agent recovery: {PHASE_LABELS[bundle.phase]}\n\n"
        "Read the failed response, errors, evidence catalog, and manifest snapshot "
        "in this run directory. Repair only this phase, preserve verified source "
        "names and years. Save the repaired section to "
        f"`{prefix}-agent-response.md` in this directory and apply it with "
        "`--recovery-phase` plus `--recovery-response`.\n\n"
        + repair_instructions(list(bundle.errors))
        + "\n"
    )
    _atomic_write_text(bundle.run_dir / f"{prefix}-recovery.md", recovery_prompt)
    _atomic_write_json(bundle.run_dir / "checkpoint.json", bundle.checkpoint)
    print(f"[Recovery] Bundle saved in {bundle.run_dir} for {PHASE_LABELS[bundle.phase]}")


def apply_agent_recovery(
    request: RunRequest,
    context: PipelineContext,
    run_dir: Path,
    checkpoint: dict[str, Any],
    validator: Callable[[str], list[str]],
) -> None:
    """Accept an Agent in-flight repair for a specific phase without wiping subsequent phases."""
    phase = request.recovery_phase
    if not phase or phase not in PHASE_ORDER:
        raise CheckpointError(f"Invalid recovery phase: {phase}")

    if not request.recovery_response:
        raise CheckpointError("No Agent recovery response path supplied")
    raw_path = Path(request.recovery_response).expanduser()
    response_path = raw_path if raw_path.is_absolute() else (run_dir / raw_path).resolve()
    if not response_path.is_file():
        raise CheckpointError(f"Agent recovery response not found: {response_path}")

    answer = response_path.read_text(encoding="utf-8").strip()
    if not answer:
        raise CheckpointError("Agent recovery response is empty")

    errors = validator(answer)
    if errors:
        prefix = f"phase-{_phase_slug(phase)}"
        _atomic_write_text(run_dir / f"{prefix}.agent-response.md", answer)
        _atomic_write_json(
            run_dir / f"{prefix}.agent-errors.json",
            {"phase": phase, "errors": errors, "source": "agent-recovery"},
        )
        checkpoint.setdefault("phases", {})[phase] = "failed"
        checkpoint.setdefault("phase_errors", {})[phase] = errors
        _atomic_write_json(run_dir / "checkpoint.json", checkpoint)
        raise PhaseValidationError(phase, errors, answer)

    repaired_name = f"phase-{_phase_slug(phase)}.repaired.md"
    _atomic_write_text(run_dir / repaired_name, answer)
    checkpoint.setdefault("phases", {})[phase] = "repaired"
    checkpoint.setdefault("phase_files", {})[phase] = repaired_name
    checkpoint.setdefault("phase_errors", {})[phase] = []
    checkpoint["resume_from"] = phase
    checkpoint["status"] = "running"
    checkpoint.setdefault("agent_recoveries", []).append(
        {"phase": phase, "response_file": response_path.name, "status": "repaired"}
    )
    _atomic_write_json(run_dir / "checkpoint.json", checkpoint)
    print(f"[Recovery] Agent repair accepted for {PHASE_LABELS[phase]}")
