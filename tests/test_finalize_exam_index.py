"""Finalize must accept what --validate-draft and --verify-provenance accept.

Two gaps let a draft through review and then fail at the last step: option
labels stopped at d, so a paper's option e was unreadable; and finalize held
badges against the NotebookLM catalog only, never the exam index, so a module
whose papers were not uploaded failed every indexed Past Exams badge.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/universal-transcriber/scripts")
)

import universal_transcribe as engine  # noqa: E402

PROFILE = {"mcq": {"options": {"count": 4}}}
STEM = "This class of drugs does not cause hyperprolactinemia"
FIVE_OPTIONS = (
    "- **a.** Prokinetic drugs\n"
    "- **b.** Antipsychotics\n"
    "- **c.** Anti-depressants\n"
    "- **d.** Proton pump inhibitors\n"
    "- **e.** Pioglitazone\n"
)
EXPLANATION = "**Clinical Explanation:** البيوجليتازون مش بيعمل hyperprolactinemia خالص.\n"


def mcq(badge: str, options: str = FIVE_OPTIONS, answer: str = "e. Pioglitazone") -> str:
    return (
        f"### MCQ 1 {badge}\n\n"
        f"**Question:** {STEM}\n"
        f"**Options:**\n{options}"
        f"**Correct Answer:** {answer}\n"
        + EXPLANATION
    )


def draft(mcqs: str) -> str:
    return (
        "# 📚 Draft\n\n"
        "---\n\n## 📖 Chronological Guide\n\n"
        + "a" * 320
        + "\n\n---\n\n## 🌟 IMP Points\n\n"
        + "\n".join(engine.IMP_HEADINGS)
        + "\n> [!WARNING]\n> None\n> [!CAUTION]\n> None\n\n"
        + "---\n\n## ❓ MCQs\n\n"
        + mcqs
        + "\n---\n\n## ✍️ Written Questions\n\n> [!NOTE]\n> none\n\n"
        + "---\n\n## 🩺 Clinical Cases\n\n"
        + "> [!TIP]\n> **🩺 Clinical Case 1:** **[IMP]**\n"
        + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n\n"
        + "> [!TIP]\n> **🩺 Clinical Case 2:** **[IMP]**\n"
        + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n"
    )


def index(years: list[int]) -> dict:
    return {
        "schema_version": 1,
        "module": "endo",
        "sources": [],
        "questions": {
            "endo-0203": {
                "kind": "mcq",
                "stem": STEM,
                "options": {},
                "answer": "e",
                "years": years,
                "sources": ["End_2024.txt"],
            }
        },
    }


# What phase 0 builds when only the lecture was uploaded: the catalog is real
# and non-empty, it just holds no exam papers.
LECTURE_ONLY_CATALOG = [
    {
        "canonical_name": "1st lecture.pdf",
        "normalized_name": engine.normalize_source_key("1st lecture.pdf"),
        "aliases": ["1st lecture.pdf"],
        "role": "lecture",
        "verified_years": [],
        "content_status": "available",
    }
]


class OptionELabelTests(unittest.TestCase):
    def test_correct_answer_e_is_an_option_label(self) -> None:
        errors = engine.validate_editorial_quality(
            mcq("**[Past Exams - 2024]**"), PROFILE
        )

        self.assertFalse(any("option label" in error for error in errors), errors)
        self.assertFalse(any("missing option" in error for error in errors), errors)
        self.assertFalse(any("must be separate" in error for error in errors), errors)

    def test_option_e_is_parsed_as_its_own_entry(self) -> None:
        entries = engine._option_entries(FIVE_OPTIONS)

        self.assertEqual(list(entries), ["a", "b", "c", "d", "e"])
        self.assertEqual(entries["d"], "Proton pump inhibitors")
        self.assertEqual(entries["e"], "Pioglitazone")

    def test_answer_e_text_is_checked_against_option_e(self) -> None:
        errors = engine.validate_editorial_quality(
            mcq("**[Past Exams - 2024]**", answer="e. Antipsychotics"), PROFILE
        )

        self.assertTrue(any("text differs" in error for error in errors), errors)

    def test_answer_e_without_an_option_e_points_to_a_missing_option(self) -> None:
        four = FIVE_OPTIONS.rsplit("- **e.**", 1)[0]
        errors = engine.validate_editorial_quality(
            mcq("**[Past Exams - 2024]**", options=four), PROFILE
        )

        self.assertTrue(any("missing option" in error for error in errors), errors)

    def test_imp_question_must_still_match_the_profile_count(self) -> None:
        errors = engine.validate_editorial_quality(mcq("**[IMP]**"), PROFILE)

        self.assertTrue(any("must be separate a, b, c, d" in error for error in errors))

    def test_sourced_question_with_too_few_options_is_still_rejected(self) -> None:
        three = "- **a.** One\n- **b.** Two\n- **c.** Three\n"
        errors = engine.validate_editorial_quality(
            mcq("**[Past Exams - 2024]**", options=three, answer="a. One"), PROFILE
        )

        self.assertTrue(any("must be separate" in error for error in errors))

    def test_abbreviations_and_letters_in_option_text_are_not_labels(self) -> None:
        options = (
            "- **a.** Vitamin D. deficiency\n"
            "- **b.** Hepatitis E. infection\n"
            "- **c.** Steroids, i.e. prednisolone\n"
            "- **d.** Other drugs, e.g. lithium\n"
        )

        entries = engine._option_entries(options)

        self.assertEqual(list(entries), ["a", "b", "c", "d"])
        self.assertEqual(entries["d"], "Other drugs, e.g. lithium")
        self.assertEqual(entries["c"], "Steroids, i.e. prednisolone")


class FinalizeAgainstExamIndexTests(unittest.TestCase):
    def test_indexed_past_exam_badge_finalizes_without_uploaded_papers(self) -> None:
        document = engine.finalize_student_document(
            draft(mcq("**[Past Exams - 2024]**")),
            set(),
            PROFILE,
            LECTURE_ONLY_CATALOG,
            index([2024]),
        )

        self.assertRegex(document, r"### MCQ 1 \*\*\[Past Exams \(2024\)")

    def test_without_the_index_the_same_badge_is_rejected(self) -> None:
        with self.assertRaises(engine.ValidationError) as raised:
            engine.finalize_student_document(
                draft(mcq("**[Past Exams - 2024]**")),
                set(),
                PROFILE,
                LECTURE_ONLY_CATALOG,
            )

        self.assertIn("missing_source", str(raised.exception))

    def test_index_still_rejects_a_year_it_does_not_record(self) -> None:
        with self.assertRaises(engine.ValidationError) as raised:
            engine.finalize_student_document(
                draft(mcq("**[Past Exams - 2024 / 2025]**")),
                set(),
                PROFILE,
                LECTURE_ONLY_CATALOG,
                index([2024, 2023]),
            )

        self.assertIn("source_year_mismatch", str(raised.exception))

    def test_unindexed_past_exam_question_still_needs_a_source(self) -> None:
        other = index([2024])
        other["questions"]["endo-0203"]["stem"] = "An entirely different question stem here"
        with self.assertRaises(engine.ValidationError) as raised:
            engine.finalize_student_document(
                draft(mcq("**[Past Exams - 2024]**")),
                set(),
                PROFILE,
                LECTURE_ONLY_CATALOG,
                other,
            )

        self.assertIn("missing_source", str(raised.exception))

    def test_module_exam_index_is_loaded_from_questions(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(engine.load_module_exam_index(root), {})
            questions = Path(root) / "Questions"
            questions.mkdir()
            (questions / "exam-index.json").write_text(
                json.dumps(index([2024])), encoding="utf-8"
            )

            loaded = engine.load_module_exam_index(root)

        self.assertIn("endo-0203", loaded["questions"])


if __name__ == "__main__":
    unittest.main()
