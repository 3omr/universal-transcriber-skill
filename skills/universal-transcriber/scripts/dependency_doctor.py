#!/usr/bin/env python3
"""Preflight check for the external tooling the transcription pipeline shells out to.

The pipeline depends on the `nlm` CLI plus a handful of document tools that are
not installable from PyPI. Without this check a missing tool surfaces only deep
inside a run, as a bare "ocrmypdf or pdfocr is required for scanned PDFs" after
the Agent has already built a manifest and uploaded sources.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Dependency:
    """One external tool or Python package the pipeline can call."""

    name: str
    # Any one of these executables satisfies the dependency.
    executables: tuple[str, ...]
    purpose: str
    install_hint: str
    required: bool
    python_module: str | None = None

    def resolve(self) -> str | None:
        if self.python_module:
            found = importlib.util.find_spec(self.python_module)
            return self.python_module if found else None
        for executable in self.executables:
            path = shutil.which(executable)
            if path:
                return path
        return None


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency(
        name="nlm",
        executables=("nlm",),
        purpose="Every NotebookLM query, upload, and source listing",
        install_hint="https://github.com/tmc/nlm -- then run `nlm auth`",
        required=True,
    ),
    Dependency(
        name="poppler-utils",
        executables=("pdftotext",),
        purpose="Reading a PDF's text layer to decide whether it needs OCR",
        install_hint="apt install poppler-utils / brew install poppler",
        required=True,
    ),
    Dependency(
        name="poppler-utils (pdfinfo)",
        executables=("pdfinfo",),
        purpose="Counting PDF pages for the source quality report",
        install_hint="apt install poppler-utils / brew install poppler",
        required=True,
    ),
    Dependency(
        name="ocrmypdf",
        executables=("ocrmypdf", "pdfocr"),
        purpose="OCR for scanned past-exam PDFs that carry no text layer",
        install_hint="apt install ocrmypdf / brew install ocrmypdf",
        required=False,
    ),
    Dependency(
        name="libreoffice",
        executables=("libreoffice", "soffice"),
        purpose="Converting PPTX/PPSX/DOCX slides to PDF before upload",
        install_hint="apt install libreoffice / brew install --cask libreoffice",
        required=False,
    ),
    Dependency(
        name="ghostscript",
        executables=("gs", "ghostscript"),
        purpose="Compressing PDFs that exceed the NotebookLM upload limit",
        install_hint="apt install ghostscript / brew install ghostscript",
        required=False,
    ),
    Dependency(
        name="ffmpeg",
        executables=("ffmpeg",),
        purpose="Normalizing recordings in formats NotebookLM will not accept",
        install_hint="apt install ffmpeg / brew install ffmpeg",
        required=False,
    ),
    Dependency(
        name="genanki",
        executables=(),
        purpose="Building native .apkg decks in transcriber-anki (.tsv works without it)",
        install_hint="pip install -r requirements.txt",
        required=False,
        python_module="genanki",
    ),
)


def report(stream=sys.stdout) -> int:
    """Print the dependency report. Returns 0 when nothing required is missing."""
    missing_required: list[Dependency] = []
    missing_optional: list[Dependency] = []

    print("Universal Transcriber dependency check\n", file=stream)
    for dependency in DEPENDENCIES:
        location = dependency.resolve()
        if location:
            mark = "✔"
            detail = location
        else:
            mark = "✖" if dependency.required else "○"
            detail = "not found"
            (missing_required if dependency.required else missing_optional).append(
                dependency
            )
        label = "required" if dependency.required else "optional"
        print(f"  {mark} {dependency.name:22} [{label}] {detail}", file=stream)
        print(f"      {dependency.purpose}", file=stream)

    if missing_optional:
        print("\nOptional tooling not installed:", file=stream)
        for dependency in missing_optional:
            print(
                f"  ○ {dependency.name}: needed only for "
                f"{dependency.purpose[0].lower()}{dependency.purpose[1:]}\n"
                f"    Install: {dependency.install_hint}",
                file=stream,
            )

    if missing_required:
        print("\nMissing required tooling -- transcription cannot run:", file=stream)
        for dependency in missing_required:
            print(
                f"  ✖ {dependency.name}\n    Install: {dependency.install_hint}",
                file=stream,
            )
        return 1

    print("\nAll required tooling is present.", file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(report())
