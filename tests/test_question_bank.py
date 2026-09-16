import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "universal-transcriber" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from bank_export import (
    COLUMNS,
    render_bank_markdown,
    render_exam_html,
    render_exam_markdown,
    write_csv,
    write_json,
)
from question_bank import (
    CASE,
    MCQ,
    WRITTEN,
    QuestionBankError,
    build_bank,
    filter_bank,
    mark_duplicates,
    questions_from_transcript,
    sample_exam,
    similarity,
    stem_tokens,
)
from transcript_parser import parse_transcript


def _transcript(lecture_questions: str) -> str:
    return "## ❓ MCQs\n\n" + lecture_questions


LECTURE_A = """## ❓ MCQs

### MCQ 1 **[Past Exams - 2023]**

**Question:** Glaucoma is characterized by:-
**Options:**
- **a.** Raised pressure only
- **b.** Progressive optic neuropathy
- **c.** Corneal oedema
- **d.** Lens opacity

**Correct Answer:** b. Progressive optic neuropathy
**Clinical Explanation:** شرح.

---

## ✍️ Written Questions

### Question 1 **[Past Exams - 2022, 2023]**

**Question:** Give a short account on: Closed angle glaucoma
**Model Answer:**
- Shallow anterior chamber
- Mid-dilated pupil

**Clinical Explanation:** شرح.

---

## 🩺 Clinical Cases

### Clinical Case 1 **[IMP]**

**Scenario:** A 58-year-old female with sudden severe eye pain and haloes.

**Questions:**
1. What is the most likely diagnosis?
2. Outline the treatment.

**Model Answer:**
1. **Diagnosis:**
- Acute angle-closure glaucoma

**Clinical Explanation:** شرح.

---
"""

LECTURE_B = """## ✍️ Written Questions

### Question 1 **[Past Exams - 2024]**

**Question:** Write a short account on closed-angle glaucoma
**Model Answer:**
- Shallow anterior chamber

**Clinical Explanation:** شرح.

---

## 🩺 Clinical Cases

### Clinical Case 1

**Scenario:** A 30-year-old man with a red painful eye after trauma.

**Questions:**
1. What is the most likely diagnosis?
2. Outline the treatment.

**Model Answer:**
1. **Diagnosis:**
- Corneal abrasion

**Clinical Explanation:** شرح.

---
"""


def _module(root: Path) -> Path:
    transcripts = root / "Transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)
    (transcripts / "Glaucoma 👁️.md").write_text(LECTURE_A, encoding="utf-8")
    (transcripts / "Cornea 👁️.md").write_text(LECTURE_B, encoding="utf-8")
    (transcripts / "Index.md").write_text("# index\n", encoding="utf-8")
    (transcripts / "Draft 👁️.draft.md").write_text(LECTURE_A, encoding="utf-8")
    return transcripts


class SimilarityTests(unittest.TestCase):
    def test_the_same_question_phrased_two_ways_matches(self) -> None:
        # Verbatim from the ophtha module: the same past-exam question appears
        # under both wordings.
        self.assertEqual(
            similarity(
                "Give a short account on: Closed angle glaucoma (clinical picture and treatment)",
                "Write a short account on closed-angle glaucoma (clinical picture and treatment)",
            ),
            1.0,
        )

    def test_two_questions_on_one_topic_stay_distinct(self) -> None:
        score = similarity("Define: Glaucoma", "Mention 5 signs of glaucoma")

        self.assertLess(score, 0.85)

    def test_instruction_words_do_not_identify_a_question(self) -> None:
        self.assertNotIn("define", stem_tokens("Define: Glaucoma"))
        self.assertIn("glaucoma", stem_tokens("Define: Glaucoma"))


class DuplicateTests(unittest.TestCase):
    def test_a_repeat_is_marked_and_kept_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            transcripts = _module(Path(temporary_directory).resolve())
            bank = build_bank(transcripts, "ophtha")

        duplicates = [question for question in bank.questions if question.is_duplicate]
        self.assertEqual(len(duplicates), 1)
        # Kept: the same question in several lectures is a signal about what
        # the examiners care about.
        self.assertEqual(len(bank.questions), len(bank.unique) + 1)

    def test_the_canonical_copy_absorbs_the_years_its_repeat_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            transcripts = _module(Path(temporary_directory).resolve())
            bank = build_bank(transcripts, "ophtha")

        duplicate = next(q for q in bank.questions if q.is_duplicate)
        canonical = next(q for q in bank.questions if q.question_id == duplicate.duplicate_of)
        self.assertEqual(canonical.years, (2022, 2023, 2024))

    def test_clinical_cases_are_compared_on_the_scenario_not_the_sub_questions(self) -> None:
        # Every case asks "What is the most likely diagnosis?" and friends.
        # Keying on those collapsed thirteen distinct vignettes into two.
        with tempfile.TemporaryDirectory() as temporary_directory:
            transcripts = _module(Path(temporary_directory).resolve())
            bank = build_bank(transcripts, "ophtha")

        cases = bank.by_kind(CASE)
        self.assertEqual(len(cases), 2)
        self.assertFalse(any(case.is_duplicate for case in cases))

    def test_an_mcq_and_a_written_question_never_deduplicate_against_each_other(self) -> None:
        parsed = parse_transcript(
            "### MCQ 1\n\n**Question:** Closed angle glaucoma treatment\n"
            "**Correct Answer:** a\n\n"
            "### Question 1\n\n**Question:** Closed angle glaucoma treatment\n"
            "**Model Answer:**\n- Pilocarpine\n"
        )
        marked = mark_duplicates(questions_from_transcript(parsed))

        self.assertFalse(any(question.is_duplicate for question in marked))


class BankTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.transcripts = _module(Path(self._dir.name).resolve())
        self.bank = build_bank(self.transcripts, "ophtha")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_index_and_drafts_are_not_collected(self) -> None:
        self.assertEqual(sorted(self.bank.lectures), ["Cornea", "Glaucoma"])

    def test_questions_carry_their_lecture_and_kind(self) -> None:
        mcq = self.bank.by_kind(MCQ)[0]

        self.assertEqual(mcq.lecture, "Glaucoma")
        self.assertEqual(mcq.correct_option, "b")
        self.assertTrue(mcq.is_past_exam)

    def test_filtering_by_year_keeps_only_matching_questions(self) -> None:
        selected = filter_bank(self.bank, years=(2024,))

        self.assertTrue(selected)
        for question in selected:
            self.assertIn(2024, question.years)

    def test_an_empty_module_says_so_rather_than_returning_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(QuestionBankError, "No finalized transcripts"):
                build_bank(Path(empty), "ophtha")


class ExamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.transcripts = _module(Path(self._dir.name).resolve())
        self.bank = build_bank(self.transcripts, "ophtha")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_sampling_rotates_across_lectures(self) -> None:
        # A paper must not come mostly from whichever lecture happened to have
        # the most questions extracted.
        # The unique written+case pool is three: Glaucoma contributes a written
        # question and a case, Cornea a case, and Cornea's written question is
        # the marked duplicate.
        drawn = sample_exam(self.bank, 3, kinds=(WRITTEN, CASE), seed=3)

        self.assertEqual(len(drawn), 3)
        self.assertEqual(len({question.lecture for question in drawn}), 2)
        # First pick from each lecture before a second from any.
        self.assertNotEqual(drawn[0].lecture, drawn[1].lecture)

    def test_the_same_seed_draws_the_same_paper(self) -> None:
        first = sample_exam(self.bank, 3, kinds=(WRITTEN, CASE), seed=11)
        second = sample_exam(self.bank, 3, kinds=(WRITTEN, CASE), seed=11)

        self.assertEqual(
            [q.question_id for q in first], [q.question_id for q in second]
        )

    def test_asking_for_more_than_exists_returns_what_exists(self) -> None:
        drawn = sample_exam(self.bank, 500, kinds=(MCQ,), seed=1)

        self.assertEqual(len(drawn), len(self.bank.by_kind(MCQ)))

    def test_the_answer_key_is_a_separate_document(self) -> None:
        # A paper with the answers printed under each question cannot be sat.
        drawn = sample_exam(self.bank, 2, kinds=(MCQ, WRITTEN), seed=5)
        paper, key = render_exam_markdown(drawn, title="Mock")

        self.assertNotIn("Progressive optic neuropathy\n**Answer", paper)
        self.assertIn("answer key", key)
        for question in drawn:
            if question.answer:
                self.assertIn(question.answer.split(";")[0][:20], key)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name).resolve()
        self.bank = build_bank(_module(self.root), "ophtha")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_csv_carries_every_declared_column(self) -> None:
        result = write_csv(self.bank, self.root / "bank.csv")

        with result.path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), len(self.bank.questions))
        self.assertEqual(list(rows[0]), list(COLUMNS))

    def test_csv_is_written_with_a_bom_so_excel_reads_arabic(self) -> None:
        result = write_csv(self.bank, self.root / "bank.csv")

        self.assertTrue(result.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_json_export_records_the_bank_shape(self) -> None:
        result = write_json(self.bank, self.root / "bank.json")
        payload = json.loads(result.path.read_text(encoding="utf-8"))

        self.assertEqual(payload["module"], "ophtha")
        self.assertEqual(payload["total"], len(self.bank.questions))

    def test_markdown_marks_a_repeated_question(self) -> None:
        markdown = render_bank_markdown(self.bank)

        self.assertIn("asked 2×", markdown)

    def test_the_html_paper_embeds_its_own_key_and_needs_no_network(self) -> None:
        drawn = sample_exam(self.bank, 1, kinds=(MCQ,), seed=1)
        page = render_exam_html(drawn, title="Mock")

        self.assertIn("<!doctype html>", page)
        self.assertIn('"correct":', page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)

    def test_arabic_explanations_survive_the_html_export(self) -> None:
        drawn = sample_exam(self.bank, 2, kinds=(WRITTEN,), seed=1)
        page = render_exam_html(drawn, title="Mock")

        self.assertIn("Shallow anterior chamber", page)

    def test_excel_export_explains_itself_when_openpyxl_is_absent(self) -> None:
        from unittest.mock import patch

        import bank_export

        with patch.dict(sys.modules, {"openpyxl": None}):
            with self.assertRaisesRegex(bank_export.ExportError, "pip install openpyxl"):
                bank_export.write_xlsx(self.bank, self.root / "bank.xlsx")


if __name__ == "__main__":
    unittest.main()
