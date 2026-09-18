#!/usr/bin/env python3
"""Transcription backends, and the boundary they sit behind.

Import engines by name through ``get_phase_engine`` / ``get_transcription_engine``
rather than constructing them directly, so a caller never has to know which
module a backend lives in.
"""

from __future__ import annotations

from .base import (
    NOTEBOOKLM,
    NOTEBOOKLM_RAW,
    WHISPER,
    EngineError,
    EngineUnavailable,
    PhaseEngine,
    RawTranscription,
    TranscriptionEngine,
    TranscriptionSegment,
)

PHASE_ENGINES = (NOTEBOOKLM,)
# notebooklm-raw first: it is the default because it needs nothing installed
# and the recording is already uploaded by the time anyone asks.
TRANSCRIPTION_ENGINES = (NOTEBOOKLM_RAW, WHISPER)
ENGINE_NAMES = (NOTEBOOKLM, NOTEBOOKLM_RAW, WHISPER)


def get_phase_engine(name: str = NOTEBOOKLM, **options) -> PhaseEngine:
    """The engine that answers phase prompts. Only NotebookLM does this today."""
    if name != NOTEBOOKLM:
        raise EngineError(
            f"{name!r} does not answer phase prompts. The five-section pipeline "
            f"runs on {NOTEBOOKLM}; {NOTEBOOKLM_RAW} and {WHISPER} produce a "
            "verbatim transcript for the Agent to write from."
        )
    from .notebooklm import NotebookLMEngine

    return NotebookLMEngine(**options)


def get_transcription_engine(
    name: str = NOTEBOOKLM_RAW, **options
) -> TranscriptionEngine:
    """The engine that turns a recording into what was said."""
    if name == NOTEBOOKLM_RAW:
        from .notebooklm_raw import NotebookLMRawEngine

        return NotebookLMRawEngine(**options)
    if name == WHISPER:
        from .whisper import WhisperEngine

        return WhisperEngine(**options)
    raise EngineError(
        f"Unknown transcription engine: {name!r}. "
        f"Available: {', '.join(TRANSCRIPTION_ENGINES)}."
    )


__all__ = [
    "ENGINE_NAMES",
    "NOTEBOOKLM",
    "NOTEBOOKLM_RAW",
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
