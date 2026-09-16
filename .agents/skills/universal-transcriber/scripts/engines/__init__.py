#!/usr/bin/env python3
"""Transcription backends, and the boundary they sit behind.

Import engines by name through ``get_phase_engine`` / ``get_transcription_engine``
rather than constructing them directly, so a caller never has to know which
module a backend lives in.
"""

from __future__ import annotations

from .base import (
    NOTEBOOKLM,
    WHISPER,
    EngineError,
    EngineUnavailable,
    PhaseEngine,
    RawTranscription,
    TranscriptionEngine,
    TranscriptionSegment,
)

PHASE_ENGINES = (NOTEBOOKLM,)
TRANSCRIPTION_ENGINES = (WHISPER,)
ENGINE_NAMES = (NOTEBOOKLM, WHISPER)


def get_phase_engine(name: str = NOTEBOOKLM, **options) -> PhaseEngine:
    """The engine that answers phase prompts. Only NotebookLM does this today."""
    if name != NOTEBOOKLM:
        raise EngineError(
            f"{name!r} does not answer phase prompts. The five-section pipeline "
            f"runs on {NOTEBOOKLM}; {WHISPER} produces a verbatim transcript for "
            "the Agent to write from."
        )
    from .notebooklm import NotebookLMEngine

    return NotebookLMEngine(**options)


def get_transcription_engine(name: str = WHISPER, **options) -> TranscriptionEngine:
    """The engine that turns a recording into what was said."""
    if name != WHISPER:
        raise EngineError(f"Unknown transcription engine: {name!r}")
    from .whisper import WhisperEngine

    return WhisperEngine(**options)


__all__ = [
    "ENGINE_NAMES",
    "NOTEBOOKLM",
    "PHASE_ENGINES",
    "TRANSCRIPTION_ENGINES",
    "WHISPER",
    "EngineError",
    "EngineUnavailable",
    "PhaseEngine",
    "RawTranscription",
    "TranscriptionEngine",
    "TranscriptionSegment",
    "get_phase_engine",
    "get_transcription_engine",
]
