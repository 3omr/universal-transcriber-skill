"""The exam index is what a badge's honesty now rests on."""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Imported after the path setup above, which is what makes them importable.
import exam_index  # noqa: E402
import provenance_audit  # noqa: E402

COMPILED_BANK = """--- End 2022 ---
1. As regard cardiac ultrasound, it can assess
A.
Valvular cardiac pathology.
+ B.
Coronary stenosis.

--- Page 12 ---
2. Mention the contraindications for MRI.
Model answer:
Pacemaker, cochlear implant, metallic foreign body.
"""

SINGLE_PAPER = """--- Page 1 ---
1. Hydrocephalus means
A. Dilated cerebral ventricles
B. Skull fracture
"""


class ExamIndexTests(unittest.TestCase):
    def test_year_comes_from_the_section_not_the_filename(self) -> None:
        """A bank compiled in 2026 does not make its contents 2026 questions.

        This is the whole point of the index: `Radiology_Exams_2026.txt` holds
        questions from 2021 through 2025, and a question sitting under an
        unlabelled `Page 12` may claim no year at all -- however the file that
        carries it happens to be named.
        """
        questions = exam_index.parse_source("Radiology_Exams_2026.txt", COMPILED_BANK)
        by_stem = {q.stem[:20]: q for q in questions}
        self.assertEqual(by_stem["As regard cardiac ul"].years, [2022])
        self.assertEqual(by_stem["Mention the contrain"].years, [])

    def test_pagination_is_not_provenance(self) -> None:
        """`--- Page 1 ---` is what OCR writes, not what a paper claims.

        A single scanned paper is marked up page by page. Treating those as
        provenance sections would strip its filename year and leave every
        question in it unclaimable.
        """
        questions = exam_index.parse_source("final_exam_2022.txt", SINGLE_PAPER)
        self.assertEqual(questions[0].years, [2022])

    def test_marked_option_is_captured_as_the_answer(self) -> None:
        questions = exam_index.parse_source("bank.txt", COMPILED_BANK)
        self.assertEqual(questions[0].answer, "b")

    def test_model_answer_promotes_the_entry_to_written(self) -> None:
        questions = exam_index.parse_source("Radiology_Exams_2026.txt", COMPILED_BANK)
        written = [q for q in questions if q.kind == "written"]
        self.assertEqual(len(written), 1)
        self.assertIn("Pacemaker", written[0].model_answer)

    def test_same_question_in_two_papers_becomes_one_entry_with_both_years(self) -> None:
        """Asked in 2022 and again in 2023 is one question carrying two years.

        Two entries would let a writer badge either year alone and call it
        sourced, which is how a transcript ends up under-claiming provenance
        that the papers actually support.
        """
        paper_2022 = "1. Central multiple fluid levels in erect x-ray abdomen is diagnostic for\nA. Large bowel\n+ B. Small bowel\n"
        paper_2023 = "7. Central multiple fluid levels in erect x-ray abdomen is diagnostic for\nA. Large bowel\n+ B. Small bowel\n"
        merged = exam_index.merge(
            exam_index.parse_source("final_exam_2022.txt", paper_2022)
            + exam_index.parse_source("final_exam_2023.txt", paper_2023)
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].years, [2022, 2023])
        self.assertEqual(merged[0].sources, ["final_exam_2022.txt", "final_exam_2023.txt"])

    def test_ocr_wreckage_is_flagged_not_dropped(self) -> None:
        """A question the scan destroyed still exists, and a human must see it.

        Dropping it silently would teach the writer that the paper never asked
        it, which is the error the index exists to prevent -- in the other
        direction.
        """
        wrecked = "1. The Most Ré 7~5 ~ 2026 Q) . igital ime a =A ©) #\n"
        questions = exam_index.parse_source("End_2026.txt", wrecked)
        self.assertFalse(questions[0].legible)

    def test_index_round_trips_through_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            questions_dir = Path(directory)
            (questions_dir / "final_exam_2022.txt").write_text(
                SINGLE_PAPER, encoding="utf-8"
            )
            built = exam_index.build_index(questions_dir, "radio")
            exam_index.write_index(built, questions_dir)
            loaded = exam_index.load_index(questions_dir)
        self.assertEqual(loaded["module"], "radio")
        self.assertEqual(len(loaded["questions"]), 1)
        self.assertTrue(next(iter(loaded["questions"])).startswith("radio-"))

    def test_missing_index_says_how_to_build_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(exam_index.ExamIndexError) as raised:
                exam_index.load_index(Path(directory))
        self.assertIn("--build-exam-index", str(raised.exception))


class ProvenanceAuditTests(unittest.TestCase):
    def test_a_question_on_no_paper_supports_no_year(self) -> None:
        """The failure this was written for.

        Six clinical cases were badged `[Past Exams - 2022, 2023]` on scenarios
        that appeared in no paper; the phase validators passed them because the
        files were real and the years were known.
        """
        sources = {"final_exam_2022.txt": COMPILED_BANK}
        invented = (
            "A 45-year-old woman presents to the emergency department with "
            "severe right upper abdominal pain radiating to the right shoulder."
        )
        self.assertEqual(provenance_audit.supported_years(invented, sources), ())

    def test_a_question_that_is_there_supports_its_section_year(self) -> None:
        sources = {"Radiology_Exams_2026.txt": COMPILED_BANK}
        stem = "As regard cardiac ultrasound, it can assess"
        self.assertEqual(provenance_audit.supported_years(stem, sources), (2022,))

    def test_shared_english_does_not_place_a_question_in_a_paper(self) -> None:
        """Ratio alone rewards two texts for both being medical English.

        The run of consecutive words is what separates "this question is here"
        from "these sentences rhyme", and this is the case that proved it: an
        invented vignette scored 29% against a paper it was never on.
        """
        sources = {"paper.txt": COMPILED_BANK}
        rhyming = "As regard the patient with pathology, it can present with pain."
        self.assertEqual(provenance_audit.supported_years(rhyming, sources), ())

    def test_a_short_image_stem_is_located_through_its_options(self) -> None:
        """"The arrows indicate" carries no signal; its options do."""
        paper = (
            "--- End 2022 ---\n20. The arrows indicate\nA.\nPneumonia\n+ D.\n"
            "Bilateral air under diaphragm (crescent sign)\n"
        )
        located = provenance_audit.locate(
            "The arrows indicate",
            "bank.txt",
            paper,
            context="Pneumonia Pleural effusion Bilateral air under diaphragm crescent sign",
        )
        self.assertTrue(located.found)


if __name__ == "__main__":
    unittest.main()
