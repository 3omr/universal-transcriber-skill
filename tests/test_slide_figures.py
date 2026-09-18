import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = (
    Path(__file__).parents[1]
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import slide_figures
from slide_figures import (
    TEXT_CHARACTER_FLOOR,
    Figure,
    FigureExtractionError,
    FigureSet,
    extract_figures,
    page_image_counts,
    page_text_lengths,
    render_reference_markdown,
    render_report,
    write_manifest,
)

PDFIMAGES_LISTING = """page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image     128   128  rgb     3   8  jpeg   no         5  0    75    75 4356B 8.9%
   1     1 image     128   128  rgb     3   8  jpeg   no         5  0    75    75 4356B 8.9%
   3     2 image     900   600  rgb     3   8  jpeg   no        11  0   150   150  180kB 3.1%
"""


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["tool"], returncode, stdout, "")


class PageSignalTests(unittest.TestCase):
    def test_pages_are_split_on_the_form_feed_pdftotext_emits(self) -> None:
        with patch.object(slide_figures.shutil, "which", return_value="/usr/bin/pdftotext"), \
             patch.object(
                 slide_figures.subprocess,
                 "run",
                 return_value=_completed("Long page of prose\fxy\f"),
             ):
            lengths = page_text_lengths(Path("deck.pdf"))

        self.assertEqual(lengths, [len("Longpageofprose"), 2])

    def test_image_counts_are_read_from_one_pdfimages_call(self) -> None:
        with patch.object(slide_figures.shutil, "which", return_value="/usr/bin/pdfimages"), \
             patch.object(
                 slide_figures.subprocess, "run", return_value=_completed(PDFIMAGES_LISTING)
             ):
            counts = page_image_counts(Path("deck.pdf"), 4)

        self.assertEqual(counts, [2, 0, 1, 0])

    def test_without_pdfimages_every_page_is_assumed_to_have_one(self) -> None:
        # Failing this way round admits the odd title-only slide rather than
        # losing every figure.
        with patch.object(slide_figures.shutil, "which", return_value=None):
            self.assertEqual(page_image_counts(Path("deck.pdf"), 3), [1, 1, 1])


class FigureSelectionTests(unittest.TestCase):
    """A page is a figure only when it is both text-poor and image-bearing."""

    def test_a_text_poor_page_with_an_image_is_a_figure(self) -> None:
        self.assertTrue(Figure(3, Path("p.png"), text_characters=2, embedded_images=1).is_diagram)

    def test_a_title_only_divider_is_not_a_figure(self) -> None:
        # The real regression: a slide reading just "Warfarin" has eight
        # characters and no picture, and renders to a blank slide with a title.
        divider = Figure(66, Path("p.png"), text_characters=8, embedded_images=0)

        self.assertFalse(divider.is_diagram)

    def test_a_dense_prose_slide_is_not_a_figure_even_with_an_image(self) -> None:
        prose = Figure(4, Path("p.png"), text_characters=TEXT_CHARACTER_FLOOR + 1, embedded_images=2)

        self.assertFalse(prose.is_diagram)


class ExtractionTests(unittest.TestCase):
    def _fake_tools(self, text_pages: list[str], image_listing: str):
        """Stand in for pdftotext, pdfimages and pdftoppm."""

        def run(command, **_kwargs):
            tool = Path(command[0]).name
            if tool == "pdftotext":
                return _completed("\f".join(text_pages) + "\f")
            if tool == "pdfimages":
                return _completed(image_listing)
            if tool == "pdftoppm":
                target = Path(command[-1]).with_suffix(".png")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"\x89PNG rendered")
                return _completed()
            raise AssertionError(f"unexpected tool {tool}")

        return run

    def test_only_diagram_pages_are_rendered_and_recorded(self) -> None:
        listing = (
            "page num type\n"
            "-----\n"
            "   1  0 image\n"
            "   3  1 image\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            deck = root / "deck.pdf"
            deck.write_bytes(b"%PDF-1.4 fake")
            transcripts = root / "Transcripts"

            with patch.object(slide_figures.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}"), \
                 patch.object(
                     slide_figures.subprocess,
                     "run",
                     side_effect=self._fake_tools(["x", "A slide full of real prose text", "y"], listing),
                 ):
                figure_set = extract_figures(deck, transcripts, "Organophosphates")

            # Page 1 and 3 are text-poor with an image; page 2 is prose.
            self.assertEqual([figure.page for figure in figure_set.figures], [1, 3])
            self.assertEqual(figure_set.total_pages, 3)

            manifest = json.loads(figure_set.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["lecture"], "Organophosphates")
            self.assertEqual([item["page"] for item in manifest["figures"]], [1, 3])
            for figure in figure_set.figures:
                self.assertTrue(figure.image_path.is_file())

    def test_all_slide_pages_overrides_the_diagram_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            deck = root / "deck.pdf"
            deck.write_bytes(b"%PDF-1.4 fake")

            with patch.object(slide_figures.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}"), \
                 patch.object(
                     slide_figures.subprocess,
                     "run",
                     side_effect=self._fake_tools(["lots of prose here", "more prose"], "page\n--\n"),
                 ):
                figure_set = extract_figures(
                    deck, root / "Transcripts", "Deck", include_text_pages=True
                )

            self.assertEqual(len(figure_set.figures), 2)

    def test_a_missing_source_is_reported_rather_than_traced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(FigureExtractionError, "not found"):
                extract_figures(
                    Path(temporary_directory) / "absent.pptx",
                    Path(temporary_directory),
                    "Absent",
                )

    def test_a_non_slide_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recording = Path(temporary_directory) / "lecture.m4a"
            recording.write_bytes(b"audio")

            with self.assertRaisesRegex(FigureExtractionError, "Not a slide source"):
                extract_figures(recording, Path(temporary_directory), "Lecture")


class RenderingTests(unittest.TestCase):
    def _figure_set(self, **overrides) -> FigureSet:
        defaults: dict[str, object] = {
            "lecture": "Organophosphates",
            "source_name": "OPs.pptx",
            "output_dir": Path("/m/Transcripts/Figures/Organophosphates"),
            "figures": (
                Figure(10, Path("page-010.png"), text_characters=0, embedded_images=1),
            ),
            "total_pages": 76,
            "skipped_text_pages": 67,
        }
        defaults.update(overrides)
        return FigureSet(**defaults)

    def test_markdown_links_are_relative_to_the_transcripts_directory(self) -> None:
        # The transcript sits in Transcripts/, so these have to resolve from
        # there -- in Obsidian and on GitHub alike.
        markdown = render_reference_markdown(self._figure_set())

        self.assertIn(
            "![Organophosphates — slide 10](<./Figures/Organophosphates/page-010.png>)",
            markdown,
        )

    def test_a_lecture_whose_name_has_a_space_still_links(self) -> None:
        """A bare markdown link stops at the space in "Corrosive 1".

        Obsidian read the href as "./Figures/Corrosive" and offered to create
        it, so every figure in that lecture rendered as a broken link. Angle
        brackets are what make the rest of the path part of the href.
        """
        markdown = render_reference_markdown(
            self._figure_set(
                lecture="Corrosive 1",
                output_dir=Path("/m/Transcripts/Figures/Corrosive 1"),
            )
        )

        self.assertIn("(<./Figures/Corrosive 1/page-010.png>)", markdown)

    def test_a_lecture_with_no_figures_produces_no_markdown(self) -> None:
        self.assertEqual(render_reference_markdown(self._figure_set(figures=())), "")

    def test_the_report_says_how_many_pages_were_skipped(self) -> None:
        report = render_report(self._figure_set())

        self.assertIn("67 of 76 pages skipped", report)

    def test_a_lecture_name_with_path_characters_cannot_escape_the_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            figure_set = FigureSet(
                lecture="../../etc",
                source_name="deck.pptx",
                output_dir=root / "Figures" / slide_figures._safe_name("../../etc"),
            )
            write_manifest(figure_set)

            self.assertTrue(figure_set.manifest_path.is_file())
            self.assertTrue(
                figure_set.manifest_path.resolve().is_relative_to(root),
                figure_set.manifest_path,
            )


if __name__ == "__main__":
    unittest.main()
