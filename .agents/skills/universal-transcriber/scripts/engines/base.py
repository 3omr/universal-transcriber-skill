#!/usr/bin/env python3
"""What an engine is, and what the rest of the pipeline may assume about one.

Every transcription in this project has gone through NotebookLM, driven by the
`nlm` CLI -- a reverse-engineered client for a service with no public API. When
that breaks, the whole tool stops. Nothing in the pipeline actually required
that to be true: the retry, repair and checkpoint machinery in run_phase_query
never cared where an answer came from, only that it came back as a QueryResult.

This module names that boundary so a second backend can sit behind it.

Two kinds of engine, because they answer different questions:

``PhaseEngine``
    Given a phase prompt and a set of approved sources, return the answer.
    This is what NotebookLM does, and what the 5-section pipeline is built on.

``TranscriptionEngine``
    Given a recording, return what was said -- verbatim, with no restructuring
    and no summarising. This is what a local Whisper does. It deliberately does
    not try to imitate the phase pipeline: the Agent takes the raw text and
    writes the five sections from it, which is a different division of labour,
    not a worse one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from transcriber_models import PhaseQuery, QueryResult

NOTEBOOKLM = "notebooklm"
WHISPER = "whisper"


class EngineError(RuntimeError):
    """Raised when an engine cannot do its job."""


class EngineUnavailable(EngineError):
    """Raised when an engine's dependency is not installed.

    Distinct from EngineError on purpose: a missing package is a sentence
    telling the user what to install, not a failed run to debug.
    """


@dataclass(frozen=True)
class TranscriptionSegment:
    """One span of speech, as the recogniser heard it."""

    start: float
    end: float
    text: str

    @property
    def timestamp(self) -> str:
        minutes, seconds = divmod(int(self.start), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class RawTranscription:
    """A recording, transcribed verbatim.

    ``text`` is what was said, unedited. Nothing here is summarised, reordered
    or cleaned up -- that is the Agent's job, and doing it here would quietly
    throw away the doctor's exact wording, which is the one thing the exam-style
    prompts treat as authoritative.
    """

    recording: Path
    text: str
    language: str = ""
    duration: float = 0.0
    model: str = ""
    segments: tuple[TranscriptionSegment, ...] = field(default=())

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def with_timestamps(self) -> str:
        """The transcript with a timestamp per segment, for navigating audio."""
        if not self.segments:
            return self.text
        return "\n".join(
            f"[{segment.timestamp}] {segment.text.strip()}" for segment in self.segments
        )


@runtime_checkable
class PhaseEngine(Protocol):
    """Answers one phase prompt against a set of approved sources."""

    name: str

    def run_phase(self, query: PhaseQuery) -> QueryResult:
        """Return the answer for this phase.

        Implementations do the single call only. Retry, repair, validation and
        checkpointing belong to run_phase_query, which is engine-neutral and
        must stay that way.
        """
        ...


@runtime_checkable
class TranscriptionEngine(Protocol):
    """Turns a recording into what was said, verbatim."""

    name: str

    def is_available(self) -> bool:
        """Whether this engine can run here, without raising if it cannot."""
        ...

    def transcribe(self, recording: Path, *, language: str = "") -> RawTranscription:
        ...


__all__ = [
    "NOTEBOOKLM",
    "WHISPER",
    "EngineError",
    "EngineUnavailable",
    "PhaseEngine",
    "RawTranscription",
    "TranscriptionEngine",
    "TranscriptionSegment",
]
