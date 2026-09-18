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
# "auto" means: ask the device what it can actually do. Naming a compute type
# here instead was a real bug -- the default was int8, which every CPU build
# supports, on a device resolved as "auto", which picks CUDA whenever a GPU is
# present. On a machine whose CUDA build offers only float32 the two defaults
# contradicted each other and the engine died before transcribing a second of
# audio:
#
#   ValueError: Requested int8 compute type, but the target device or backend
#   do not support efficient int8 computation.
DEFAULT_COMPUTE_TYPE = "auto"
# Best first. int8 variants are much faster and are what make the medium model
# practical; float32 is the universal fallback that always exists.
COMPUTE_TYPE_PREFERENCE = (
    "int8_float16",
    "int8_float32",
    "int8",
    "float16",
    "float32",
)
INSTALL_HINT = (
    "faster-whisper is required for the local engine. Install it with "
    "`pip install faster-whisper`, or use --engine notebooklm."
)


GPU_FAILURE_MARKERS = ("libcublas", "libcudnn", "cuda", "cudart", "no kernel image")


def _is_gpu_runtime_failure(error: Exception) -> bool:
    """Whether this error means the GPU cannot be used, rather than bad input."""
    message = str(error).casefold()
    return any(marker in message for marker in GPU_FAILURE_MARKERS)


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
        self._runtime: tuple[str, str] | None = None

    def is_available(self) -> bool:
        if self._model_factory is not None:
            return True
        return importlib.util.find_spec("faster_whisper") is not None

    def resolve_runtime(self, device: str | None = None) -> tuple[str, str]:
        """Pick a device and a compute type that device can actually run.

        faster-whisper resolves device="auto" to CUDA whenever a GPU exists,
        and a CUDA build does not necessarily support the same compute types a
        CPU build does. Asking ctranslate2 what the resolved device supports is
        the only way to avoid naming a combination that cannot run.
        """
        device = device or self.device
        compute_type = self.compute_type
        try:
            import ctranslate2
        except ImportError:
            # Without ctranslate2 there is no faster-whisper either; let
            # _load_model raise the install hint rather than guessing here.
            return device, (compute_type if compute_type != "auto" else "default")

        if device == "auto":
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        try:
            supported = set(ctranslate2.get_supported_compute_types(device))
        except Exception:
            return device, (compute_type if compute_type != "auto" else "default")

        if compute_type != "auto":
            if compute_type in supported:
                return device, compute_type
            print(
                f"[Whisper] {device} does not support {compute_type}; "
                f"falling back to what it does support",
            )
        for candidate in COMPUTE_TYPE_PREFERENCE:
            if candidate in supported:
                return device, candidate
        return device, "default"

    def _load_model(self, force_cpu: bool = False):
        if self._model is not None and not force_cpu:
            return self._model
        if force_cpu:
            device, compute_type = self.resolve_runtime(device="cpu")
        else:
            device, compute_type = self.resolve_runtime()
        self._runtime = (device, compute_type)
        if self._model_factory is not None:
            self._model = self._model_factory(
                self.model_size, device=device, compute_type=compute_type
            )
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise EngineUnavailable(INSTALL_HINT) from error
        print(f"[Whisper] {self.model_size} model on {device} ({compute_type})")
        self._model = WhisperModel(
            self.model_size, device=device, compute_type=compute_type
        )
        return self._model

    def transcribe(self, recording: Path | str, *, language: str = "") -> RawTranscription:
        path = Path(recording).expanduser()
        if not path.is_file():
            raise EngineError(f"Recording not found: {path}")

        model = self._load_model()
        try:
            segments, info = self._run(model, path, language)
        except RuntimeError as error:
            # A GPU that ctranslate2 can see is not a GPU it can use. The CUDA
            # runtime libraries are a separate install, and when they are
            # missing the model constructs happily on "cuda" and only fails
            # here, on the first real computation:
            #
            #   RuntimeError: Library libcublas.so.12 is not found
            #
            # Falling back to CPU is slower, which is a far better outcome than
            # refusing to transcribe on a machine that has a working CPU.
            if not _is_gpu_runtime_failure(error) or (self._runtime or ("cpu",))[0] == "cpu":
                raise
            print(f"[Whisper] GPU unusable ({error}); retrying on CPU")
            self._model = None
            model = self._load_model(force_cpu=True)
            segments, info = self._run(model, path, language)

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

    def _run(self, model, path: Path, language: str):
        return model.transcribe(
            str(path),
            language=language or None,
            # The lectures switch between Arabic and English mid-sentence, so
            # the recogniser is left to follow rather than pinned to one.
            task="transcribe",
            vad_filter=True,
        )


__all__ = [
    "COMPUTE_TYPE_PREFERENCE",
    "GPU_FAILURE_MARKERS",
    "DEFAULT_MODEL",
    "INSTALL_HINT",
    "WhisperEngine",
]
