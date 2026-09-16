import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = (
    Path(__file__).parents[1] / "skills" / "universal-transcriber" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "test_question_coverage_module", SCRIPTS_DIR / "question_coverage.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load question_coverage")
coverage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = coverage
SPEC.loader.exec_module(coverage)


PAPER = """
1- First Question (multiple choice):
1) Which of the following is derived from cannabis?
   a) Opium.
   b) Marijuana.
   c) Benzodiazepine.
   d) Atropine.
2) Atropine is used as an antidote in:
   a. Organophosphates.
   b. Opium.
   c. Iron.
   d. Lead.

2- Second Question (complete):
1) c/p of acute beta blocker toxicity are:
2) Immediate causes of death in organophosphate poisoning are:
3) The antidote of methanol is:
"""


class CountQuestionsTests(unittest.TestCase):
    def test_options_separate_mcqs_from_written_items(self) -> None:
        mcqs, written = coverage.count_questions(PAPER)

        self.assertEqual(mcqs, 2)
        self.assertEqual(written, 3)

    def test_numbering_restarts_do_not_lose_stems(self) -> None:
        # Both sections number from 1; positional counting must still see five.
        mcqs, written = coverage.count_questions(PAPER)

        self.assertEqual(mcqs + written, 5)

    def test_extracted_counts_read_the_transcript_headings(self) -> None:
        transcript = (
            "### MCQ 1 **[IMP]**\n\n"
            "### MCQ 2 **[Past Exams - 2022]**\n\n"
            "### Question 1 **[IMP]**\n"
        )

        self.assertEqual(coverage.count_extracted(transcript), (2, 1))


class CoverageFloorTests(unittest.TestCase):
    def _report(self, extracted_mcqs: int, extracted_written: int):
        report = coverage.CoverageReport(
            papers=[coverage.PaperCount(Path("End 2022.pdf"), mcqs=100, written=500)],
            extracted_mcqs=extracted_mcqs,
            extracted_written=extracted_written,
        )
        return report

    def test_a_healthy_extraction_clears_both_floors(self) -> None:
        # Calibrated on the Addiction transcript: 20% MCQ, 4% written.
        self.assertEqual(self._report(20, 20).below_floor, [])

    def test_a_narrow_but_normal_extraction_is_not_flagged(self) -> None:
        # Food poisoning: 5.9% MCQ, 1.6% written -- thin but real.
        self.assertEqual(self._report(6, 8).below_floor, [])

    def test_a_thin_extraction_is_flagged_on_both_axes(self) -> None:
        # The OPs run: 4 MCQs and 2 written questions out of 100/500.
        messages = self._report(4, 2).below_floor

        self.assertEqual(len(messages), 2)
        self.assertIn("MCQ coverage", messages[0])
        self.assertIn("Written coverage", messages[1])

    def test_nothing_is_claimed_when_no_papers_are_readable(self) -> None:
        report = coverage.CoverageReport(
            papers=[
                coverage.PaperCount(
                    Path("scan.pdf"), readable=False, reason="no text layer"
                )
            ]
        )

        self.assertEqual(report.below_floor, [])


class BuildReportTests(unittest.TestCase):
    def test_unreadable_papers_are_reported_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            questions = Path(root) / "Questions"
            questions.mkdir()
            (questions / "scan.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
            with patch.object(
                coverage, "pdf_text", side_effect=RuntimeError("pdftotext failed")
            ):
                report = coverage.build_report(questions, None)

            self.assertEqual(len(report.papers), 1)
            self.assertFalse(report.papers[0].readable)
            self.assertEqual(report.available_mcqs, 0)


if __name__ == "__main__":
    unittest.main()
