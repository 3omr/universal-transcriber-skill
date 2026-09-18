#!/usr/bin/env python3
"""Preflight check for the external tooling the transcription pipeline shells out to.

The pipeline depends on the `nlm` CLI plus a handful of document tools that are
not installable from PyPI. Without this check a missing tool surfaces only deep
inside a run, as a bare "ocrmypdf or pdfocr is required for scanned PDFs" after
the Agent has already built a manifest and uploaded sources.

Presence is not health. A tool can sit on PATH and still be unusable -- `nlm` is
installed but was never given credentials, a Python package is on disk but its
compiled extension will not import. `report(live=True)` therefore runs each
dependency's declared probe and reports a third state between "found" and
"missing": found, but not working. The probes are opt-in because running them
costs real seconds (a cold `libreoffice --version` is not fast).
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass

MINIMUM_PYTHON_VERSION = (3, 10)

# A probe only has to prove the tool can start and do something trivial. Long
# timeouts defeat the point of a preflight check, and a tool that needs more
# than this to print its own version is broken in a way worth reporting.
PROBE_TIMEOUT_SECONDS = 20
# `nlm notebook list` is a network round trip to NotebookLM, and it is the only
# probe that proves authentication rather than mere installation.
NLM_PROBE_TIMEOUT_SECONDS = 45


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
    # Arguments appended to the resolved executable to prove it actually works.
    # None means presence is all that can be checked cheaply.
    probe: tuple[str, ...] | None = None
    probe_timeout: int = PROBE_TIMEOUT_SECONDS
    # What a failing probe means, in the user's terms. Without this a bare
    # non-zero exit code tells nobody what to do next.
    probe_failure_hint: str = ""

    def resolve(self) -> str | None:
        if self.python_module:
            found = importlib.util.find_spec(self.python_module)
            return self.python_module if found else None
        for executable in self.executables:
            path = shutil.which(executable)
            if path:
                return path
        return None

    def check(self, location: str) -> str | None:
        """Run the probe. Returns None when healthy, else why it is not.

        ``location`` is whatever ``resolve()`` returned -- an executable path,
        or a module name for the Python packages.
        """
        if self.python_module:
            try:
                importlib.import_module(self.python_module)
            except Exception as error:  # noqa: BLE001 - any import failure is a failure
                return f"{type(error).__name__}: {error}"
            return None

        if not self.probe:
            return None

        try:
            completed = subprocess.run(
                [location, *self.probe],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=self.probe_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"`{' '.join((self.name, *self.probe))}` timed out after {self.probe_timeout}s"
        except OSError as error:
            return f"could not be executed ({error})"

        if completed.returncode != 0:
            detail = (completed.stderr.strip() or completed.stdout.strip()).splitlines()
            first_line = detail[0][:200] if detail else f"exit code {completed.returncode}"
            return first_line
        return None


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency(
        name="nlm",
        executables=("nlm",),
        purpose="Every NotebookLM query, upload, and source listing",
        install_hint="https://github.com/tmc/nlm -- then run `nlm auth`",
        required=True,
        # Exactly the call the engine makes first on every run
        # (universal_transcribe.py: `nlm notebook list`), so a green probe here
        # means the real pipeline's first step will work too.
        probe=("notebook", "list"),
        probe_timeout=NLM_PROBE_TIMEOUT_SECONDS,
        probe_failure_hint=(
            "nlm is installed but could not list your notebooks -- it is most "
            "likely unauthenticated. Run `nlm auth`."
        ),
    ),
    Dependency(
        name="poppler-utils",
        executables=("pdftotext",),
        purpose="Reading a PDF's text layer to decide whether it needs OCR",
        install_hint="apt install poppler-utils / brew install poppler",
        required=True,
        probe=("-v",),
    ),
    Dependency(
        name="poppler-utils (pdfinfo)",
        executables=("pdfinfo",),
        purpose="Counting PDF pages for the source quality report",
        install_hint="apt install poppler-utils / brew install poppler",
        required=True,
        probe=("-v",),
    ),
    Dependency(
        name="poppler-utils (pdftoppm)",
        executables=("pdftoppm",),
        purpose="Rendering diagram slides as figures (--extract-figures)",
        install_hint="apt install poppler-utils / brew install poppler",
        required=False,
        probe=("-v",),
    ),
    Dependency(
        name="poppler-utils (pdfimages)",
        executables=("pdfimages",),
        purpose="Telling a diagram slide apart from a title-only divider",
        install_hint="apt install poppler-utils / brew install poppler",
        required=False,
        probe=("-v",),
    ),
    Dependency(
        name="ocrmypdf",
        executables=("ocrmypdf", "pdfocr"),
        purpose="OCR for scanned past-exam PDFs that carry no text layer",
        install_hint="apt install ocrmypdf / brew install ocrmypdf",
        required=False,
        probe=("--version",),
    ),
    Dependency(
        name="libreoffice",
        executables=("libreoffice", "soffice"),
        purpose="Converting PPTX/PPSX/DOCX slides to PDF, for upload and for figures",
        install_hint="apt install libreoffice / brew install --cask libreoffice",
        required=False,
        probe=("--version",),
    ),
    Dependency(
        name="ghostscript",
        executables=("gs", "ghostscript"),
        purpose="Compressing PDFs that exceed the NotebookLM upload limit",
        install_hint="apt install ghostscript / brew install ghostscript",
        required=False,
        probe=("--version",),
    ),
    Dependency(
        name="ffmpeg",
        executables=("ffmpeg",),
        purpose="Normalizing recordings in formats NotebookLM will not accept",
        install_hint="apt install ffmpeg / brew install ffmpeg",
        required=False,
        probe=("-version",),
    ),
    Dependency(
        name="genanki",
        executables=(),
        purpose="Building native .apkg decks in transcriber-anki (.tsv works without it)",
        install_hint="pip install -r requirements.txt",
        required=False,
        python_module="genanki",
    ),
    Dependency(
        name="faster-whisper",
        executables=(),
        purpose=(
            "Local verbatim transcription (--engine whisper), no account needed. "
            "Not needed for --engine notebooklm-raw, which reads the transcript "
            "NotebookLM already made"
        ),
        install_hint="pip install faster-whisper",
        required=False,
        python_module="faster_whisper",
    ),
    Dependency(
        name="openpyxl",
        executables=(),
        purpose="Excel export of the question bank (--format xlsx)",
        install_hint="pip install openpyxl",
        required=False,
        python_module="openpyxl",
    ),
    Dependency(
        name="python-docx",
        executables=(),
        purpose="Word export of an exam paper (--format docx)",
        install_hint="pip install python-docx",
        required=False,
        python_module="docx",
    ),
    Dependency(
        name="reportlab",
        executables=(),
        purpose="Rendering plain-text question banks to PDF before upload",
        install_hint="pip install -r requirements.txt",
        required=False,
        python_module="reportlab",
    ),
)


def _print_python_version(stream) -> bool:
    """Report the running interpreter. Returns False when it is too old."""
    version = sys.version_info
    running = ".".join(str(part) for part in version[:3])
    minimum = ".".join(str(part) for part in MINIMUM_PYTHON_VERSION)
    if version[:2] >= MINIMUM_PYTHON_VERSION:
        print(f"  ✔ {'python':22} [required] {running}", file=stream)
        return True
    print(f"  ✖ {'python':22} [required] {running} -- {minimum}+ is required", file=stream)
    return False


def report(stream=sys.stdout, *, live: bool = False) -> int:
    """Print the dependency report. Returns 0 when nothing required is missing.

    With ``live=True`` every dependency that declares a probe is actually run,
    so an installed-but-unusable tool is reported as a failure rather than a
    tick. That is the difference between this check passing and a run that dies
    half an hour later.
    """
    missing_required: list[Dependency] = []
    missing_optional: list[Dependency] = []
    unhealthy_required: list[tuple[Dependency, str]] = []
    unhealthy_optional: list[tuple[Dependency, str]] = []

    print("Universal Transcriber dependency check", file=stream)
    if live:
        print("(running liveness probes -- this takes a few seconds)", file=stream)
    print("", file=stream)

    python_is_supported = _print_python_version(stream)

    for dependency in DEPENDENCIES:
        location = dependency.resolve()
        label = "required" if dependency.required else "optional"
        if not location:
            mark = "✖" if dependency.required else "○"
            (missing_required if dependency.required else missing_optional).append(
                dependency
            )
            print(f"  {mark} {dependency.name:22} [{label}] not found", file=stream)
            print(f"      {dependency.purpose}", file=stream)
            continue

        failure = dependency.check(location) if live else None
        if failure:
            (unhealthy_required if dependency.required else unhealthy_optional).append(
                (dependency, failure)
            )
            print(
                f"  ⚠ {dependency.name:22} [{label}] {location} -- not working",
                file=stream,
            )
        else:
            print(f"  ✔ {dependency.name:22} [{label}] {location}", file=stream)
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

    if unhealthy_optional:
        print("\nOptional tooling installed but not working:", file=stream)
        for dependency, failure in unhealthy_optional:
            print(f"  ⚠ {dependency.name}: {failure}", file=stream)
            if dependency.probe_failure_hint:
                print(f"    {dependency.probe_failure_hint}", file=stream)

    if unhealthy_required:
        print(
            "\nRequired tooling is installed but not working -- transcription "
            "cannot run:",
            file=stream,
        )
        for dependency, failure in unhealthy_required:
            print(f"  ⚠ {dependency.name}: {failure}", file=stream)
            print(
                f"    {dependency.probe_failure_hint or 'Install: ' + dependency.install_hint}",
                file=stream,
            )

    if missing_required:
        print("\nMissing required tooling -- transcription cannot run:", file=stream)
        for dependency in missing_required:
            print(
                f"  ✖ {dependency.name}\n    Install: {dependency.install_hint}",
                file=stream,
            )

    if missing_required or unhealthy_required or not python_is_supported:
        return 1

    if live:
        print("\nAll required tooling is present and working.", file=stream)
    else:
        print(
            "\nAll required tooling is present. Run with --doctor-live to check "
            "that it actually works (including whether `nlm` is authenticated).",
            file=stream,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(report())
