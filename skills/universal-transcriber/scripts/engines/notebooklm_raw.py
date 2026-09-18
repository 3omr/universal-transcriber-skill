#!/usr/bin/env python3
"""Fetch a recording's verbatim transcript from NotebookLM, and stop there.

NotebookLM already transcribes every audio source it ingests. Until now the
tool never read that transcript: it only ever asked NotebookLM to *answer
questions about* the recording, which meant the five sections were written by a
model that had read the audio, behind a prompt, out of reach.

This engine takes the other half of the deal. It asks NotebookLM for the raw
transcript -- `nlm content source`, which is explicitly "no AI processing" --
and hands that text back unedited. The Agent then writes the five sections from
it, in the open, where every claim can be checked against a line of the
transcript sitting in the repo.

Why this and not local Whisper:

* It needs nothing new installed. `nlm` is already a required dependency, so
  there is no optional package, no model download, no CUDA, no 20-minute run.
  Whisper's medium model on CPU took longer than the lecture it was
  transcribing.
* The recording is already uploaded. Every module that runs this pipeline has
  pushed its audio to a notebook -- the transcript is sitting there, finished.
* It is the same recognition NotebookLM itself reasons over, so the raw text
  and the phase answers cannot disagree about what was said.

What it gives up is timestamps: `nlm content source` returns prose, not
segments. RawTranscription.with_timestamps() degrades to the plain text, which
is why that method was written to tolerate an empty segment tuple.

This is a TranscriptionEngine, not a PhaseEngine. It answers "what was said",
never "what should the MCQ section contain".
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from source_naming import normalize_source_stem
from transcriber_models import RemoteSource

from .base import (
    NOTEBOOKLM_RAW,
    EngineError,
    RawTranscription,
)

DEFAULT_TIMEOUT_SECONDS = 180
AUDIO_SOURCE_TYPES = frozenset({"audio", "video"})
CONTENT_KEYS = ("content", "text", "body")


def _content_body(stdout: str) -> str:
    """The transcript out of whichever shape the CLI chose to print."""
    stripped = stdout.strip()
    if not stripped.startswith(("{", "[")):
        return stdout
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stdout
    if isinstance(payload, dict):
        for key in CONTENT_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return stdout

SourceLister = Callable[[str, dict[str, Any] | None], list[RemoteSource]]
ContentFetcher = Callable[[str, dict[str, Any] | None, int], str]


def _fetch_content(
    source_id: str, config: dict[str, Any] | None, timeout_seconds: int
) -> str:
    """Run `nlm content source <id>` and return the transcript body.

    Deliberately not routed through nlm_client._run_nlm_json, which appends
    --json to every call: this command already emits a JSON object on stdout
    without being asked, and emits plain text when writing to a file, so the
    payload shape is decided by the CLI rather than by us. Both are accepted
    here -- a body that does not parse as JSON is the transcript itself.
    """
    from nlm_client import _nlm_command  # noqa: PLC0415 -- avoids an import cycle

    command = _nlm_command(config or {}, ["content", "source", source_id])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise EngineError(
            f"NotebookLM did not return the transcript for {source_id} within "
            f"{timeout_seconds}s"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise EngineError(f"nlm content source failed: {detail[:500]}")
    return _content_body(completed.stdout)


class NotebookLMRawEngine:
    """Returns the verbatim transcript NotebookLM holds for a recording."""

    name = NOTEBOOKLM_RAW

    def __init__(
        self,
        *,
        notebook_uuid: str = "",
        config: dict[str, Any] | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        source_lister: SourceLister | None = None,
        content_fetcher: ContentFetcher | None = None,
    ) -> None:
        self.notebook_uuid = notebook_uuid
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._source_lister = source_lister
        self._content_fetcher = content_fetcher or _fetch_content

    def _list_sources(self) -> list[RemoteSource]:
        if self._source_lister is not None:
            return self._source_lister(self.notebook_uuid, self.config)
        from nlm_client import list_remote_sources  # noqa: PLC0415

        return list_remote_sources(self.notebook_uuid, self.config)

    def is_available(self) -> bool:
        """Whether the nlm CLI is reachable and a notebook has been named.

        Never raises. A missing notebook id is as disqualifying as a missing
        executable -- this engine has nowhere to read from without one.
        """
        if not self.notebook_uuid:
            return False
        try:
            from nlm_client import _find_nlm_executable  # noqa: PLC0415

            _find_nlm_executable(self.config)
        except Exception:
            return False
        return True

    def _match(self, recording: Path) -> RemoteSource:
        wanted = normalize_source_stem(recording.name)
        sources = self._list_sources()
        audio = [s for s in sources if s.source_type.casefold() in AUDIO_SOURCE_TYPES]
        # Audio first: a module may hold both "lecture.m4a" and a "lecture.txt"
        # of notes, and only one of them has a spoken transcript behind it.
        for pool in (audio, sources):
            for source in pool:
                if source.normalized_stem == wanted:
                    return source
        known = ", ".join(sorted(s.title for s in audio)) or "none"
        raise EngineError(
            f"No audio source in the notebook matches {recording.name!r}. "
            f"Audio sources present: {known}. Upload the recording first, or "
            "run the sync-sources workflow."
        )

    def transcribe(
        self, recording: Path | str, *, language: str = ""
    ) -> RawTranscription:
        path = Path(recording).expanduser()
        source = self._match(path)
        text = self._content_fetcher(
            source.source_id, self.config, self.timeout_seconds
        ).strip()
        if not text:
            raise EngineError(
                f"NotebookLM returned an empty transcript for {source.title!r}. "
                "A freshly uploaded recording is still being processed -- check "
                "`nlm list sources` for its status and retry."
            )
        return RawTranscription(
            recording=path,
            text=text,
            language=language,
            model=f"{NOTEBOOKLM_RAW}:{source.source_id}",
            segments=(),
        )


__all__ = ["NotebookLMRawEngine", "DEFAULT_TIMEOUT_SECONDS"]
