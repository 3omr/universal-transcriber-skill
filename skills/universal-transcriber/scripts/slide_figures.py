#!/usr/bin/env python3
"""Rasterize the diagram-heavy pages of a lecture's slides.

Slides reach NotebookLM as a PPTX or a PDF, and NotebookLM answers in text.
Everything that was a picture -- the anatomy of the anterior chamber, a
gonioscopy view, the timeline of a toxidrome -- is simply gone by the time a
transcript is written. In ophthalmology and toxicology that is most of the
teaching.

This module takes the pages that carry a diagram and writes them next to the
transcript as PNGs, plus a figures.json describing each one, so the Agent can
reference them from the transcript with a relative image link.

Why "the pages that carry a diagram" can be decided mechanically, in two parts:

1. The page yields almost no extractable text -- so it is not prose that the
   transcript already covers. source_preparation computes that signal in
   aggregate as PdfInspection.sparse_page_ratio; here it is per page.
2. The page actually embeds an image. Text alone is not enough: a section
   divider reading just "Warfarin" has eight characters and no picture at all,
   and rendering it produces a blank slide with a title on it.

Both are required. On a real 76-slide toxicology deck the text test alone
selected ten pages, one of which was such a divider; requiring an embedded
image removed exactly that one and kept the other nine.

This is deliberately *not* a preparation action. A preparation action replaces
a source with one artifact, and a slide deck needs to be uploaded *and*
illustrated -- those are not alternatives. Figures are also an output-side
concern: they land beside the transcript, not in the upload set.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SLIDE_EXTENSIONS = {".ppt", ".pptx", ".pps", ".ppsx"}
FIGURES_DIR_NAME = "Figures"
MANIFEST_NAME = "figures.json"

# A page with fewer alphanumeric characters than this is picture, not prose.
# source_preparation.inspect_pdf uses the same threshold for the aggregate
# sparse_page_ratio; keeping them equal means the two agree about what "this
# page has no text" means.
TEXT_CHARACTER_FLOOR = 20
# 150 DPI is poppler's own default and renders a slide at roughly 1650x1275 --
# legible for a diagram without producing multi-megabyte files for a 60-slide
# deck.
DEFAULT_RESOLUTION = 150
SLIDE_CONVERSION_TIMEOUT = 300
RENDER_TIMEOUT = 300
TEXT_TIMEOUT = 180
IMAGE_LIST_TIMEOUT = 180


class FigureExtractionError(RuntimeError):
    """Raised when figures cannot be produced from a slide source."""


@dataclass(frozen=True)
class Figure:
    """One rendered slide page."""

    page: int
    image_path: Path
    text_characters: int
    embedded_images: int = 0

    @property
    def is_diagram(self) -> bool:
        return self.text_characters < TEXT_CHARACTER_FLOOR and self.embedded_images > 0


@dataclass(frozen=True)
class FigureSet:
    lecture: str
    source_name: str
    output_dir: Path
    figures: tuple[Figure, ...] = ()
    total_pages: int = 0
    skipped_text_pages: int = 0

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / MANIFEST_NAME


def _run(command: list[str], timeout: int, description: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise FigureExtractionError(
            f"Required tool for {description} was not found: {command[0]}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FigureExtractionError(f"{description} timed out") from error
    if completed.returncode != 0:
        detail = (completed.stderr.strip() or completed.stdout.strip())[:400]
        raise FigureExtractionError(f"{description} failed: {detail}")
    return completed


def page_text_lengths(pdf_path: Path) -> list[int]:
    """Alphanumeric characters per page, in page order.

    pdftotext separates pages with a form feed, which is the same split
    source_preparation.inspect_pdf relies on.
    """
    if not shutil.which("pdftotext"):
        raise FigureExtractionError("pdftotext (poppler-utils) is required to find diagrams")
    extracted = _run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        TEXT_TIMEOUT,
        "slide text extraction",
    ).stdout
    pages = extracted.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return [sum(character.isalnum() for character in page) for page in pages]


def page_image_counts(pdf_path: Path, page_count: int) -> list[int]:
    """Embedded raster images per page, in page order.

    `pdfimages -list` prints one row per image with the page in column one, so
    a single call covers the whole document -- cheaper and far less fragile
    than invoking it once per page.
    """
    counts = [0] * page_count
    if not shutil.which("pdfimages"):
        # Without pdfimages the text test stands alone. That admits the odd
        # title-only slide rather than losing every figure, which is the right
        # way round to fail.
        return [1] * page_count
    listing = _run(
        ["pdfimages", "-list", str(pdf_path)],
        IMAGE_LIST_TIMEOUT,
        "slide image listing",
    ).stdout
    for line in listing.splitlines()[2:]:
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        page = int(fields[0])
        if 1 <= page <= page_count:
            counts[page - 1] += 1
    return counts


def slides_to_pdf(source: Path, output_dir: Path) -> Path:
    """Convert a slide deck to PDF. Returns the source unchanged if it is one.

    Note that .pptx reaches NotebookLM as a .pptx -- source_preparation only
    auto-converts the legacy formats -- so for the common case the PDF this
    needs does not exist yet and has to be made here.
    """
    if source.suffix.casefold() == ".pdf":
        return source
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise FigureExtractionError(
            "LibreOffice/soffice is required to render slides as figures"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [executable, "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(source)],
        SLIDE_CONVERSION_TIMEOUT,
        "slide conversion",
    )
    generated = output_dir / f"{source.stem}.pdf"
    if not generated.is_file() or generated.stat().st_size == 0:
        raise FigureExtractionError("slide conversion did not produce a usable PDF")
    return generated


def _render_pages(
    pdf_path: Path, pages: list[int], destination: Path, resolution: int
) -> dict[int, Path]:
    """Render the given 1-based pages to PNG. Returns {page: written file}."""
    if not shutil.which("pdftoppm"):
        raise FigureExtractionError("pdftoppm (poppler-utils) is required to render figures")
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[int, Path] = {}
    for page in pages:
        target = destination / f"page-{page:03d}"
        _run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(resolution),
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                str(pdf_path),
                str(target),
            ],
            RENDER_TIMEOUT,
            f"figure render for page {page}",
        )
        produced = target.with_suffix(".png")
        if produced.is_file() and produced.stat().st_size:
            written[page] = produced
    return written


def _safe_name(name: str) -> str:
    """A directory name that survives every filesystem this runs on."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return cleaned or "lecture"


def extract_figures(
    slide_source: Path | str,
    transcripts_dir: Path | str,
    lecture: str,
    *,
    resolution: int = DEFAULT_RESOLUTION,
    include_text_pages: bool = False,
) -> FigureSet:
    """Render a lecture's diagram pages into ``Transcripts/Figures/<lecture>/``.

    Only pages with almost no extractable text are rendered by default: a slide
    that is mostly prose is already in the transcript, and rendering all of them
    would bury the few that carry a diagram.
    """
    source = Path(slide_source).expanduser()
    if not source.is_file():
        raise FigureExtractionError(f"Slide source not found: {source}")
    if source.suffix.casefold() not in SLIDE_EXTENSIONS | {".pdf"}:
        raise FigureExtractionError(f"Not a slide source: {source.name}")

    output_dir = Path(transcripts_dir) / FIGURES_DIR_NAME / _safe_name(lecture)

    with tempfile.TemporaryDirectory(prefix="transcriber-figures-") as work_dir:
        pdf_path = slides_to_pdf(source, Path(work_dir))
        lengths = page_text_lengths(pdf_path)
        if not lengths:
            raise FigureExtractionError(f"No readable pages in {source.name}")
        images = page_image_counts(pdf_path, len(lengths))
        wanted = [
            index + 1
            for index, count in enumerate(lengths)
            if include_text_pages
            or (count < TEXT_CHARACTER_FLOOR and images[index] > 0)
        ]
        rendered = _render_pages(pdf_path, wanted, output_dir, resolution)

    figures = tuple(
        Figure(
            page=page,
            image_path=path,
            text_characters=lengths[page - 1],
            embedded_images=images[page - 1],
        )
        for page, path in sorted(rendered.items())
    )
    figure_set = FigureSet(
        lecture=lecture,
        source_name=source.name,
        output_dir=output_dir,
        figures=figures,
        total_pages=len(lengths),
        skipped_text_pages=len(lengths) - len(wanted),
    )
    write_manifest(figure_set)
    return figure_set


def write_manifest(figure_set: FigureSet) -> Path:
    """Record what was rendered, so a rerun and the Agent agree on the set."""
    figure_set.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "lecture": figure_set.lecture,
        "source": figure_set.source_name,
        "total_pages": figure_set.total_pages,
        "skipped_text_pages": figure_set.skipped_text_pages,
        "figures": [
            {
                "page": figure.page,
                "file": figure.image_path.name,
                "text_characters": figure.text_characters,
                "embedded_images": figure.embedded_images,
            }
            for figure in figure_set.figures
        ],
    }
    path = figure_set.manifest_path
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def render_reference_markdown(figure_set: FigureSet) -> str:
    """The markdown the Agent pastes into the transcript, relative to it.

    Paths are relative to the Transcripts directory, which is where the
    transcript lives, so the links resolve in Obsidian and on GitHub alike.
    """
    if not figure_set.figures:
        return ""
    lines = [f"<!-- figures from {figure_set.source_name} -->"]
    for figure in figure_set.figures:
        relative = (
            Path(FIGURES_DIR_NAME) / _safe_name(figure_set.lecture) / figure.image_path.name
        )
        lines.append(f"![{figure_set.lecture} — slide {figure.page}](./{relative.as_posix()})")
    return "\n".join(lines) + "\n"


def render_report(figure_set: FigureSet) -> str:
    """One-paragraph summary for the CLI."""
    if not figure_set.figures:
        return (
            f"No diagram pages found in {figure_set.source_name} "
            f"({figure_set.total_pages} pages, all carry text)."
        )
    return (
        f"{len(figure_set.figures)} figure(s) from {figure_set.source_name} "
        f"-> {figure_set.output_dir}\n"
        f"  {figure_set.skipped_text_pages} of {figure_set.total_pages} pages skipped as text"
    )


__all__ = [
    "DEFAULT_RESOLUTION",
    "FIGURES_DIR_NAME",
    "Figure",
    "FigureExtractionError",
    "FigureSet",
    "TEXT_CHARACTER_FLOOR",
    "extract_figures",
    "page_image_counts",
    "page_text_lengths",
    "render_reference_markdown",
    "render_report",
    "slides_to_pdf",
    "write_manifest",
]
