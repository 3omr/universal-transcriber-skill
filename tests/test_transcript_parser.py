import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "universal-transcriber" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from transcript_parser import (
    IMPORTANT,
    PAST_EXAMS,
    QUESTION_BANK,
    classify_block,
    field_value,
    parse_badges,
    parse_transcript,
    parse_transcript_file,
    split_sections,
)

TRANSCRIPT = """# 👁️ التفريغ الأكاديمي لمحاضرة: `Glaucoma` (Ophthalmology)

## 📖 Chronological Guide

### مقدمة ومفهوم الجلوكوما الحديث (Introduction)

الدكتور شرح التعريف الحديث.

## 🌟 IMP Points

- Optic disc cupping

## ❓ MCQs

### MCQ 1 **[IMP]**

**Question:** Glaucoma is primarily characterized as:-
**Options:**
- **a.** Elevated intraocular pressure
- **b.** A progressive optic neuropathy
- **c.** Complete loss of central vision
- **d.** Acute inflammation

**Correct Answer:** b. A progressive optic neuropathy
**Clinical Explanation:**
التعريف الحديث اتغير تماماً.

---

## ✍️ Written Questions

### Question 1 **[Past Exams - 2022, 2023, 2024]** **[Question Bank]**

**Question:** Define: Glaucoma
**Model Answer:**
- Progressive optic neuropathy characterized by:
  1- Optic disc cupping
  2- Visual field defects

**Clinical Explanation:**
الجلوكوما مش مجرد ارتفاع ضغط.

---

## 🩺 Clinical Cases

### Clinical Case 1 **[Past Exams - 2023]** **[IMP]**

**Scenario:** A 58-year-old female with severe eye pain.

**Questions:**
1. What is the most likely diagnosis?
2. Outline the treatment.

**Model Answer:**
1. **Diagnosis:**
- Acute primary angle-closure glaucoma
2. **Treatment (TTT):**
- Intravenous Mannitol 20%

**Clinical Explanation:**
الحالة دي طوارئ قصوى.

---
"""


class SectionTests(unittest.TestCase):
    def test_all_five_sections_are_found_by_their_english_name(self) -> None:
        sections = split_sections(TRANSCRIPT)

        self.assertEqual(
            sorted(sections), ["cases", "guide", "imp", "mcqs", "written"]
        )

    def test_a_section_keeps_its_own_heading_and_stops_at_the_next(self) -> None:
        sections = split_sections(TRANSCRIPT)

        self.assertTrue(sections["mcqs"].startswith("## ❓ MCQs"))
        self.assertNotIn("Written Questions", sections["mcqs"])

    def test_blocks_are_classified_by_their_own_heading(self) -> None:
        self.assertEqual(classify_block("### MCQ 4 **[IMP]**"), "mcqs")
        self.assertEqual(classify_block("### Question 12"), "written")
        self.assertEqual(classify_block("### Clinical Case 3"), "cases")
        # An Arabic guide heading belongs to no question section.
        self.assertEqual(classify_block("### مقدمة ومفهوم الجلوكوما"), "")


class BadgeTests(unittest.TestCase):
    def test_every_badge_on_a_heading_is_returned_not_just_the_first(self) -> None:
        # The regression this replaces: a heading carries several badges and
        # the old reader stopped at the first one.
        badges = parse_badges("### Question 1 **[Past Exams - 2022, 2023]** **[Question Bank]**")

        self.assertEqual([badge.kind for badge in badges], [PAST_EXAMS, QUESTION_BANK])

    def test_past_exam_years_are_extracted(self) -> None:
        badges = parse_badges("**[Past Exams - 2022, 2023, 2024]**")

        self.assertEqual(badges[0].years, (2022, 2023, 2024))

    def test_an_imp_badge_carries_no_years(self) -> None:
        badges = parse_badges("**[IMP]**")

        self.assertEqual(badges[0].kind, IMPORTANT)
        self.assertEqual(badges[0].years, ())

    def test_a_heading_with_no_badge_yields_none(self) -> None:
        self.assertEqual(parse_badges("### MCQ 7"), ())


class FieldTests(unittest.TestCase):
    def test_a_field_stops_at_the_next_field(self) -> None:
        block = "**Question:** Define glaucoma\n**Model Answer:**\n- Something"

        self.assertEqual(field_value(block, "Question"), "Define glaucoma")

    def test_a_verbatim_field_is_read_under_its_plain_name(self) -> None:
        # The exam-style prompts ask for verbatim past-exam wording and the
        # engine labels those fields accordingly.
        block = "**Question (verbatim):** Mention 5 signs\n**Model Answer:**\n- x"

        self.assertEqual(field_value(block, "Question"), "Mention 5 signs")

    def test_a_missing_field_is_empty_rather_than_an_error(self) -> None:
        self.assertEqual(field_value("### MCQ 1", "Correct Answer"), "")

    def test_a_heading_inside_an_answer_does_not_end_it(self) -> None:
        """`**كلمة:**` is ordinary writing, not a field.

        A Model Answer opening on `**Small bowel:**` parsed as empty, because
        the reader ended a field at the next line starting with a bold label of
        any kind. Only the names in FIELD_NAMES end a field.
        """
        block = (
            "**Model Answer:**\n"
            "**Small bowel:** adhesions, hernia, intussusception\n"
            "**Large bowel:** carcinoma, volvulus\n\n"
            "**Clinical Explanation:** الشرح\n"
        )

        answer = field_value(block, "Model Answer")

        self.assertIn("Small bowel", answer)
        self.assertIn("volvulus", answer)
        self.assertNotIn("الشرح", answer)

    def test_a_heading_mid_answer_does_not_truncate_it(self) -> None:
        """The dangerous half: this one used to pass validation.

        A field truncated mid-answer still holds text, so nothing reported it
        missing -- the answer simply lost its last points and was called clean.
        """
        block = (
            "**Model Answer:**\n"
            "1- ABC\n"
            "**ملحوظة:** الغسيل ممنوع\n"
            "2- Antibiotics\n"
            "3- Steroids\n\n"
            "**Clinical Explanation:** الشرح\n"
        )

        answer = field_value(block, "Model Answer")

        self.assertIn("Steroids", answer)
        self.assertIn("ملحوظة", answer)

    def test_a_parenthesised_field_name_still_closes_the_field_before_it(self) -> None:
        """Why the boundary alternation is ordered longest name first.

        Shortest-first, "Model Answer" matches and then demands a colon, finds
        " (Short):" instead, and the boundary never fires -- so the field above
        swallows the one below it.
        """
        block = (
            "**Model Answer:** full prose answer\n"
            "**Model Answer (Short):** keywords only\n"
        )

        self.assertEqual(field_value(block, "Model Answer"), "full prose answer")


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_transcript(TRANSCRIPT, lecture_title="Glaucoma")

    def test_an_mcq_is_parsed_into_stem_options_and_answer(self) -> None:
        mcq = self.parsed.mcqs[0]

        self.assertEqual(mcq.number, 1)
        self.assertEqual(mcq.stem, "Glaucoma is primarily characterized as:-")
        self.assertEqual(sorted(mcq.options), ["a", "b", "c", "d"])
        self.assertEqual(mcq.options["b"], "A progressive optic neuropathy")
        self.assertEqual(mcq.correct_option, "b")
        self.assertTrue(mcq.is_complete)

    def test_a_written_model_answer_loses_its_list_markers(self) -> None:
        written = self.parsed.written[0]

        self.assertEqual(written.stem, "Define: Glaucoma")
        self.assertEqual(
            written.model_answer,
            (
                "Progressive optic neuropathy characterized by:",
                "Optic disc cupping",
                "Visual field defects",
            ),
        )

    def test_a_case_keeps_its_numbering_because_it_is_structure(self) -> None:
        # "1. **Diagnosis:**" heads the group the bullets under it answer.
        # Stripping it would lose which finding answers which sub-question.
        case = self.parsed.cases[0]

        self.assertEqual(case.model_answer[0], "1. **Diagnosis:**")
        self.assertEqual(case.questions[0], "1. What is the most likely diagnosis?")
        self.assertTrue(case.scenario.startswith("A 58-year-old female"))

    def test_the_arabic_explanation_survives_parsing(self) -> None:
        self.assertIn("طوارئ قصوى", self.parsed.cases[0].explanation)

    def test_every_past_exam_year_in_the_document_is_collected(self) -> None:
        self.assertEqual(self.parsed.years, (2022, 2023, 2024))

    def test_blocks_without_an_h2_section_are_still_found(self) -> None:
        # A fragment handed to a coverage count has no section headings, and
        # scoping to a "## ❓ MCQs" that is not there would report zero.
        fragment = "### MCQ 1 **[IMP]**\n\n### MCQ 2\n\n### Question 1\n"
        parsed = parse_transcript(fragment)

        self.assertEqual((len(parsed.mcqs), len(parsed.written)), (2, 1))

    def test_a_block_missing_a_field_is_kept_but_marked_incomplete(self) -> None:
        # A malformed question is an extraction that happened. Dropping it here
        # would flatter every coverage ratio computed downstream.
        parsed = parse_transcript("### MCQ 1\n\n**Question:** No answer given\n")

        self.assertEqual(len(parsed.mcqs), 1)
        self.assertFalse(parsed.mcqs[0].is_complete)

    def test_an_empty_document_parses_to_nothing_rather_than_raising(self) -> None:
        parsed = parse_transcript("")

        self.assertEqual((parsed.mcqs, parsed.written, parsed.cases), ((), (), ()))


REAL_TRANSCRIPTS = sorted(
    path
    for path in (REPO_ROOT / "modules" / "ophtha" / "Transcripts").glob("*.md")
    if path.name != "Index.md"
) if (REPO_ROOT / "modules" / "ophtha" / "Transcripts").is_dir() else []


@unittest.skipUnless(
    REAL_TRANSCRIPTS,
    "modules/ is gitignored local course data; this runs only where it exists",
)
class RealTranscriptTests(unittest.TestCase):
    """Parse the finalized transcripts the engine actually produced."""

    def test_every_real_transcript_has_all_five_sections(self) -> None:
        for path in REAL_TRANSCRIPTS:
            with self.subTest(path.name):
                parsed = parse_transcript_file(path)
                self.assertEqual(len(parsed.sections), 5)

    def test_every_parsed_question_is_complete(self) -> None:
        for path in REAL_TRANSCRIPTS:
            parsed = parse_transcript_file(path)
            for item in (*parsed.mcqs, *parsed.written, *parsed.cases):
                with self.subTest(f"{path.name} {type(item).__name__} {item.number}"):
                    self.assertTrue(item.is_complete)

    def test_the_emoji_is_stripped_from_the_lecture_title(self) -> None:
        titles = [parse_transcript_file(path).lecture_title for path in REAL_TRANSCRIPTS]

        for title in titles:
            self.assertEqual(title, title.strip())
            self.assertNotIn("👁", title)


if __name__ == "__main__":
    unittest.main()
