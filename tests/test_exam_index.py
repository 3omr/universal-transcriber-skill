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

    def test_a_hand_repair_survives_the_next_rebuild(self) -> None:
        """The repair replaces what the parser produced, not the other way round.

        Repairing a question rewrites its stem, so the rebuilt copy stops
        looking like the repaired one -- and a rule that kept repairs only when
        the question was *missing* therefore discarded every one of them. The
        entry has to be matched by what the repair could not change: what its
        options say.
        """
        paper = (
            "1. The following are considered in the treatment of corrosive:-\n"
            "a. Neutralizing agent.\n"
            "b. Charcoal.\n"
            "Ce Pilutional therapy.\n"
            ". Gastric lavage.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            questions_dir = Path(directory)
            (questions_dir / "End Toxico 2023.txt").write_text(paper, encoding="utf-8")
            first = exam_index.build_index(questions_dir, "toxo")
            key = next(iter(first["questions"]))
            first["questions"][key]["stem"] = (
                "The following are considered in the treatment of corrosive:"
            )
            first["questions"][key]["options"]["c"] = "Dilutional therapy."
            first["questions"][key]["answer"] = "c"
            first["questions"][key]["repaired_by_hand"] = "read off the scan"
            exam_index.write_index(first, questions_dir)

            rebuilt = exam_index.carry_over_repairs(
                exam_index.build_index(questions_dir, "toxo"), questions_dir
            )

        surviving = [
            q for q in rebuilt["questions"].values() if q.get("repaired_by_hand")
        ]
        self.assertEqual(len(surviving), 1)
        self.assertEqual(surviving[0]["answer"], "c")
        self.assertEqual(surviving[0]["options"]["c"], "Dilutional therapy.")
        # And it replaced the parsed copy rather than sitting beside it.
        self.assertEqual(len(rebuilt["questions"]), 1)

    def test_all_four_options_survive_one_line_and_a_ruined_label(self) -> None:
        """Two things these scans do constantly, which cost three options each.

        The papers print all four options on one line, and the examiner's pen
        ring destroys the letter it is drawn on. Reading only a label at the
        start of a line kept the first option and lost the rest -- and worse,
        the unparsed lines fell through into the *next* question, which ended
        up indexed holding another question's options.
        """
        paper = (
            "1. Cause of death in corrosive within 24 hours from:-\n"
            "a) Septic peritonitis. b) Collapse and dehydration from vomiting.\n"
            "#@)Asphyxia from laryngeal or glottic edema.\n"
            "d) None of the above.\n"
        )
        question = exam_index.parse_source("End 2025.txt", paper)[0]
        self.assertEqual(sorted(question.options), ["a", "b", "c", "d"])
        self.assertIn("Collapse", question.options["b"])
        self.assertIn("Asphyxia", question.options["c"])
        self.assertEqual(question.answer, "c")

    def test_a_wrapped_option_is_not_read_as_a_new_one(self) -> None:
        """The guard on the rule above: damage is what marks a lost label.

        An option long enough to wrap onto a second line begins with a plain
        word. Treating that as the next option would invent an option the paper
        never printed, which is the same lie as dropping one.
        """
        paper = (
            "1. Indications for mechanical ventilation:-\n"
            "a. Respiratory arrest (apnea), severe hypoxemia not responding\n"
            "to a high flow rate of oxygen by mask.\n"
            "b. Shock.\n"
        )
        question = exam_index.parse_source("End 2018.txt", paper)[0]
        self.assertEqual(sorted(question.options), ["a", "b"])
        self.assertIn("high flow rate", question.options["a"])

    def test_a_starred_fifth_option_is_the_answer_not_a_new_question(self) -> None:
        """The spreadsheet exports print five options and star the right one.

        Capping labels at d read "*e) Bleeding" as a bulleted question of its
        own, and the question it belonged to lost its answer.
        """
        paper = (
            "8. Which of the following is NOT a complication of an inguinal hernia?\n"
            "a) Obstruction\n"
            "b) Inflammation\n"
            "c) Strangulation\n"
            "d) Irreducibility\n"
            "*e) Bleeding\n"
            "\n"
            "9. Which of the following does NOT transilluminate?\n"
            "a) Epididymal cyst\n"
            "*b) Hematocele\n"
        )
        questions = exam_index.parse_source("End 2026.txt", paper)
        self.assertEqual(len(questions), 2)
        first = questions[0]
        self.assertEqual(sorted(first.options), ["a", "b", "c", "d", "e"])
        self.assertEqual(first.options["e"], "Bleeding")
        self.assertEqual(first.options["d"], "Irreducibility")
        self.assertEqual(first.answer, "e")
        self.assertEqual(questions[1].answer, "b")

    def test_an_unstarred_fifth_option_is_not_folded_into_d(self) -> None:
        paper = (
            "8. Which of the following is NOT a complication of an inguinal hernia?\n"
            "a) Obstruction\n"
            "*b) Inflammation\n"
            "c) Strangulation\n"
            "d) Irreducibility\n"
            "e) Bleeding\n"
        )
        question = exam_index.parse_source("End 2026.txt", paper)[0]
        self.assertEqual(question.options["d"], "Irreducibility")
        self.assertEqual(question.options["e"], "Bleeding")
        self.assertEqual(question.answer, "b")

    def test_a_sixth_option_follows_the_fifth(self) -> None:
        paper = (
            "3. Features of carcinoid syndrome include\n"
            "a) Flushing\nb) Diarrhoea\nc) Bronchospasm\nd) Tricuspid lesions\n"
            "e) Raised urinary 5-HIAA\n*f) All of the above\n"
        )
        question = exam_index.parse_source("End 2026.txt", paper)[0]
        self.assertEqual(sorted(question.options), list("abcdef"))
        self.assertEqual(question.answer, "f")

    def test_bulleted_questions_with_a_starred_fifth_option(self) -> None:
        """Bullet-opened stems: "*e)" must close the options, "* stem" open one."""
        paper = (
            "* The commonest site of a pressure sore is\n"
            "a) Occiput\nb) Heel\nc) Elbow\nd) Scapula\n*e) Sacrum\n"
            "* E. coli is the commonest organism in\n"
            "*a) Urinary tract infection\nb) Cellulitis\n"
        )
        questions = exam_index.parse_source("End 2026.txt", paper)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].answer, "e")
        self.assertEqual(questions[0].options["e"], "Sacrum")
        self.assertTrue(questions[1].stem.startswith("E. coli"))
        self.assertEqual(questions[1].answer, "a")

    def test_a_merged_answer_follows_its_option_not_its_letter(self) -> None:
        """2021 printed the answer as a) of four; 2023 as e) of five.

        Keeping the five options and the 2021 letter pointed the answer at
        "Herpes simplex".
        """
        stem = "18. A 65-year-old farmer presents with an ulcerated lesion of the lip\n"
        older = stem + (
            "*a) Squamous cell carcinoma\nb) Herpes simplex\n"
            "c) Keratoacanthoma\nd) Lichen planus\n"
        )
        newer = stem + (
            "a) Herpes simplex\nb) Melanoma\nc) Lichen planus\n"
            "d) Keratoacanthoma\n*e) Squamous cell carcinoma\n"
        )
        papers = [("End 2021.txt", older), ("Final 2023.txt", newer)]
        for order in (papers, papers[::-1]):
            merged = exam_index.merge(
                [q for name, text in order for q in exam_index.parse_source(name, text)]
            )
            self.assertEqual(len(merged), 1)
            self.assertEqual(len(merged[0].options), 5)
            self.assertEqual(
                merged[0].options[merged[0].answer or ""], "Squamous cell carcinoma"
            )

    def test_e_and_f_are_only_labels_after_the_letter_before_them(self) -> None:
        """A lost label is only recovered inside a run of four, and "E." or
        "e.g." only reads as an option where option d already stands."""
        paper = (
            "1. The antidote of opium poisoning is\n"
            "E. coli sepsis may complicate it\n"
            "a. Naloxone b) Physostigmine\n"
            "c. Neostigmine\n"
            "d. Charcoal, e.g. activated\n"
            ". Gastric lavage\n"
        )
        question = exam_index.parse_source("End 2025.txt", paper)[0]
        self.assertIn("E. coli", question.stem)
        self.assertEqual(sorted(question.options), ["a", "b", "c", "d"])
        self.assertIn("e.g. activated", question.options["d"])

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
