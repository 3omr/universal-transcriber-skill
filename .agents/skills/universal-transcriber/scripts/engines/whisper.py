#!/usr/bin/env python3
"""Transcribe a recording locally, with faster-whisper.

This exists to answer one risk: every transcription in this project goes
through a reverse-engineered client for a service with no public API, and there
was no second path at all.

What it deliberately does not do is imitate the NotebookLM pipeline. It returns
what the doctor said, verbatim, and stops. The Agent writes the five sections
from that text. Restructuring here would mean paraphrasing the recording before
anyone had read it, and the doctor's exact wording is the one thing the
exam-style prompts treat as authoritative -- "الدكتور قال نصاً" is a claim the
transcript makes, and it has to remain true.

faster-whisper is an optional dependency. A missing install is reported as a
sentence naming what to install, never a traceback.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .base import (
    WHISPER,
    EngineError,
    EngineUnavailable,
    RawTranscription,
    TranscriptionSegment,
)

DEFAULT_MODEL = "medium"
# int8 runs the medium model on a laptop CPU without a GPU. The lectures are
# Egyptian Arabic mixed with English medical terms, which the smaller models
# handle noticeably worse, so the default trades speed for getting the drug
# names right.
DEFAULT_COMPUTE_TYPE = "int8"
INSTALL_HINT = (
    "faster-whisper is required for the local engine. Install it with "
    "`pip install faster-whisper`, or use --engine notebooklm."
)


class WhisperEngine:
    """Verbatim local transcription. No restructuring, no summarising."""

    name = WHISPER

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        *,
        device: str = "auto",
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        model_factory=None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        # Injected in tests so the contract can be checked without a 1.5GB
        # model download.
        self._model_factory = model_factory
        self._model = None

    def is_available(self) -> bool:
        if self._model_factory is not None:
            return True
        return importlib.util.find_spec("faster_whisper") is not None

    def _load_model(self):
        if self._model is not None:
            return self._model
        if self._model_factory is not None:
            self._model = self._model_factory(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise EngineUnavailable(INSTALL_HINT) from error
        self._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )
        return self._model

    def transcribe(self, recording: Path | str, *, language: str = "") -> RawTranscription:
        path = Path(recording).expanduser()
        if not path.is_file():
            raise EngineError(f"Recording not found: {path}")

        model = self._load_model()
        segments, info = model.transcribe(
            str(path),
            language=language or None,
            # The lectures switch between Arabic and English mid-sentence, so
            # the recogniser is left to follow rather than pinned to one.
            task="transcribe",
            vad_filter=True,
        )
        collected = tuple(
            TranscriptionSegment(
                start=float(getattr(segment, "start", 0.0)),
                end=float(getattr(segment, "end", 0.0)),
                text=str(getattr(segment, "text", "")),
            )
            for segment in segments
        )
        text = " ".join(segment.text.strip() for segment in collected if segment.text.strip())
        return RawTranscription(
            recording=path,
            text=text,
            language=str(getattr(info, "language", "") or language),
            duration=float(getattr(info, "duration", 0.0) or 0.0),
            model=self.model_size,
            segments=collected,
        )


__all__ = ["DEFAULT_MODEL", "INSTALL_HINT", "WhisperEngine"]
