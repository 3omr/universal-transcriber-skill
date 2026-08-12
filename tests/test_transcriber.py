import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_PATH = (
    Path(__file__).parents[1] / "universal_transcriber" / "universal_transcribe.py"
)
SPEC = importlib.util.spec_from_file_location("test_transcriber_engine", ENGINE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the transcriber engine")
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)


def local_source(name: str, role: str, ocr_status: str | None = None):
    report = (
        engine.OCRReport(name, ocr_status, "no text") if ocr_status else None
    )
    return engine.LocalSource(
        path=f"/module/{name}",
        relative_path=name,
        name=Path(name).name,
        normalized_name=engine.normalize_source_key(name),
        normalized_stem=engine.normalize_source_stem(name),
        extension=Path(name).suffix,
        size=1,
        role=role,
        ocr=report,
    )


def remote_source(source_id: str, title: str, notebook_uuid: str = ""):
    return engine.RemoteSource(
        source_id=source_id,
        title=title,
        normalized_name=engine.normalize_source_key(title),
        normalized_stem=engine.normalize_source_stem(title),
        notebook_uuid=notebook_uuid,
    )


def phase0_report(local_sources, remote_sources, missing=None):
    return engine.Phase0Report(
        notebook=engine.NotebookTarget("id", "id", "url", "Module"),
        local_sources=local_sources,
        remote_sources=remote_sources,
        missing_before_upload=list(missing or ()),
        recording_source="topic.mp3",
        slide_source="topic.pdf",
    )


class TranscriberTests(unittest.TestCase):
    def test_nlm_0_9_8_unsupported_files_are_not_upload_candidates(self) -> None:
        for extension in (".ppsx", ".mkv"):
            with self.subTest(extension=extension):
                self.assertNotIn(extension, engine.NLM_UPLOAD_EXTENSIONS)

    def test_assessment_scope_adds_exam_sources_without_polluting_guide_scope(self) -> None:
        textbook = local_source("Lecture/Book.pdf", "textbook")
        exam = local_source("Exams/Final 2024.pdf", "past_exam")
        report = phase0_report(
            [textbook, exam],
            [
                remote_source("recording", "topic.mp3"),
                remote_source("slides", "topic.pdf"),
                remote_source("book", "Book.pdf"),
                remote_source("exam", "Final 2024.txt"),
            ],
        )

        guide_scope = engine._query_scope(report, {"textbook"})
        assessment_scope = engine._query_scope(
            report, {"textbook", "past_exam"}
        )

        self.assertEqual(guide_scope.source_ids, ("recording", "slides", "book"))
        self.assertEqual(
            assessment_scope.source_ids,
            ("recording", "slides", "book", "exam"),
        )

    def test_unreadable_remote_duplicate_does_not_block_missing_source_uploads(self) -> None:
        duplicate = local_source("Lecture/Book.pdf", "textbook", "fail")
        missing = local_source("Exams/Final 2024.pdf", "past_exam", "pass")
        report = phase0_report([duplicate, missing], [], [missing])

        engine._append_ocr_failures(report)

        self.assertEqual(report.blocking_errors, [])

    def test_source_scoped_query_uses_supported_nlm_argv(self) -> None:
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("id", "notebook-id", "url", "Module"),
            query_text="Create the section",
            source_ids=("audio-id", "slide-id"),
            source_names=("audio.mp3", "slides.pdf"),
        )

        self.assertEqual(
            engine._nlm_query_arguments(request),
            [
                "notebook",
                "query",
                "notebook-id",
                "Create the section",
                "--timeout",
                "180",
                "--source-ids",
                "audio-id,slide-id",
            ],
        )

    def test_multiple_recordings_are_included_in_the_guide_scope_in_order(self) -> None:
        report = phase0_report(
            [local_source("Questions/Final 2024.pdf", "past_exam")],
            [
                remote_source("part-one", "Corrosive 1.m4a"),
                remote_source("part-two", "Corrosive 2.m4a"),
                remote_source("slides", "corrosives.pptx"),
            ],
        )
        report.recording_sources = ("Corrosive 1.m4a", "Corrosive 2.m4a")
        report.slide_source = "corrosives.pptx"

        scope = engine._query_scope(report, set())

        self.assertEqual(scope.source_ids, ("part-one", "part-two", "slides"))

    def test_query_scope_keeps_sources_separated_by_notebook_project(self) -> None:
        report = phase0_report(
            [local_source("Questions/Final 2024.pdf", "past_exam")],
            [
                remote_source("recording", "topic.mp3", "project-one"),
                remote_source("exam", "Final 2024.txt", "project-two"),
            ],
        )
        report.recording_sources = ("topic.mp3",)

        scope = engine._query_scope(report, {"past_exam"})

        self.assertEqual(
            scope.project_scopes,
            (
                engine.ProjectQueryScope("project-one", ("recording",), ("topic.mp3",)),
                engine.ProjectQueryScope(
                    "project-two", ("exam",), ("Final 2024.txt", "Final 2024.pdf")
                ),
            ),
        )

    def test_project_inventory_replacement_retains_secondary_project_sources(self) -> None:
        original = [
            remote_source("old", "old.pdf", "one"),
            remote_source("bank", "bank.pdf", "two"),
        ]
        refreshed = [
            remote_source("old", "old.pdf", "one"),
            remote_source("new", "new.pdf", "one"),
        ]

        merged = engine._replace_project_inventory(original, "one", refreshed)

        self.assertEqual(
            {source.title for source in merged},
            {"old.pdf", "new.pdf", "bank.pdf"},
        )

    def test_imp_only_mcq_does_not_require_a_source_field(self) -> None:
        result = engine.QueryResult(
            answer=(
                "### MCQ 1 **[IMP]**\n\n**Question:** What did the doctor emphasize?\n"
                "**Options (verbatim):**\n- A. One\n- B. Two\n"
                "**Correct Answer:** A. One\n"
                "**Clinical Explanation (Egyptian Arabic):** الدكتور أكد النقطة دي "
                "لأنها مهمة جداً في الامتحان والعلاج."
            )
        )

        self.assertEqual(engine.validate_mcqs(result, {}, []), [])

    def test_imp_only_written_question_does_not_require_a_source_field(self) -> None:
        result = engine.QueryResult(
            answer=(
                "### Question 1 **[IMP]**\n\n"
                "**Question:** Give reason for the doctor's warning.\n"
                "**Model Answer (Short):** Because the error can cause a lethal complication."
            )
        )

        self.assertEqual(engine.validate_written(result, {}, []), [])

    def test_exam_style_profile_is_injected_as_format_only_guidance(self) -> None:
        profile = {
            "mcq": {"stem_patterns": ["The following ...:-"], "options": {"count": 4}},
            "written": {"command_patterns": ["Causes of ...: 1.... 2...."]},
        }

        mcq_prompt = engine.build_mcq_prompt("Corrosives", "evidence", "badges", profile)
        written_prompt = engine.build_written_prompt(
            "Corrosives", "evidence", "badges", profile
        )

        self.assertIn("AGENT-SUPPLIED EXAM STYLE PROFILE", mcq_prompt)
        self.assertIn("format guidance only", mcq_prompt)
        self.assertIn('"stem_patterns"', mcq_prompt)
        self.assertIn("AGENT-SUPPLIED EXAM STYLE PROFILE", written_prompt)
        self.assertIn('"command_patterns"', written_prompt)

    def test_questions_manifest_classifies_past_exam_by_agent_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions = Path(temporary_directory) / "Questions"
            questions.mkdir()
            exam = questions / "semester paper.pdf"
            bank = questions / "question bank.pdf"
            exam.write_bytes(b"exam")
            bank.write_bytes(b"bank")

            sources = engine.scan_local_sources(
                temporary_directory,
                (
                    {"path": "Questions/semester paper.pdf", "type": "past_exam", "year": 2023},
                    {"path": "Questions/question bank.pdf", "type": "question_bank"},
                ),
            )

        by_name = {source.name: source for source in sources}
        self.assertEqual(by_name["semester paper.pdf"].role, "past_exam")
        self.assertEqual(by_name["semester paper.pdf"].years, (2023,))
        self.assertEqual(by_name["question bank.pdf"].role, "question_bank")

    def test_filename_years_are_limited_to_supported_exam_years(self) -> None:
        self.assertEqual(engine.extract_filename_exam_years("End 2025.pdf"), ())
        self.assertEqual(engine.extract_filename_exam_years("End 2024.pdf"), (2024,))

    def test_questions_manifest_must_reference_an_existing_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            (Path(temporary_directory) / "Questions").mkdir()
            with self.assertRaises(engine.Phase0Error):
                engine.scan_local_sources(
                    temporary_directory,
                    ({"path": "Questions/missing.pdf", "type": "question_bank"},),
                )

    def test_questions_manifest_classifies_every_file_and_supports_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions = Path(temporary_directory) / "Questions"
            questions.mkdir()
            (questions / "Final.pdf").write_bytes(b"exam")
            (questions / "Bank.pdf").write_bytes(b"bank")
            (questions / "Ignore.pdf").write_bytes(b"ignore")

            sources = engine.scan_local_sources(
                temporary_directory,
                (
                    {"path": "Questions/Final.pdf", "type": "past_exam", "year": 2024},
                    {"path": "Questions/Bank.pdf", "type": "question_bank"},
                    {"path": "Questions/Ignore.pdf", "type": "ignore"},
                ),
                require_assessment_manifest=True,
            )

        self.assertEqual(
            {source.name: source.role for source in sources},
            {"Final.pdf": "past_exam", "Bank.pdf": "question_bank", "Ignore.pdf": "ignore"},
        )

    def test_past_exam_manifest_requires_explicit_verified_year(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions = Path(temporary_directory) / "Questions"
            questions.mkdir()
            (questions / "Final 2024.pdf").write_bytes(b"exam")

            with self.assertRaises(engine.Phase0Error):
                engine.scan_local_sources(
                    temporary_directory,
                    ({"path": "Questions/Final 2024.pdf", "type": "past_exam"},),
                    require_assessment_manifest=True,
                )

    def test_agent_approved_basename_is_rejected_when_two_missing_files_share_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            lecture_source = local_source("Lecture/shared.pdf", "lecture_material")
            question_source = local_source("Questions/shared.pdf", "question_bank")
            report = phase0_report(
                [lecture_source, question_source],
                [],
                missing=[lecture_source, question_source],
            )
            request = engine.Phase0Request(
                config={},
                requested_notebook_ids=("one",),
                subject="Module",
                sources_root=temporary_directory,
                lecture_name="topic",
                recording_sources=(),
                slides_path=None,
                approved_uploads=("shared.pdf",),
                agent_reviewed=True,
            )

            with self.assertRaises(engine.Phase0Error):
                engine._upload_phase0_sources(request, report)

    def test_unrelated_ambiguous_inventory_does_not_block_selected_lecture(self) -> None:
        unrelated = local_source("Lecture/Other lecture.pptx", "lecture_material")
        report = phase0_report([unrelated], [], missing=[])
        report.ambiguous = [unrelated]
        request = engine.Phase0Request(
            config={},
            requested_notebook_ids=("one",),
            subject="Module",
            sources_root="/module",
            lecture_name="Animal poisoning.m4a",
            recording_sources=("Animal poisoning.m4a",),
            slides_path="Lecture/animal poisoning.pptx",
            agent_reviewed=True,
        )

        engine._append_ambiguous_matches(request, report)

        self.assertEqual(report.blocking_errors, [])

    def test_ambiguous_assessment_source_blocks_selected_scope(self) -> None:
        assessment = local_source("Questions/Final.pdf", "past_exam")
        report = phase0_report([assessment], [], missing=[])
        report.ambiguous = [assessment]
        request = engine.Phase0Request(
            config={},
            requested_notebook_ids=("one",),
            subject="Module",
            sources_root="/module",
            lecture_name="Animal poisoning.m4a",
            recording_sources=("Animal poisoning.m4a",),
            slides_path="Lecture/animal poisoning.pptx",
            agent_reviewed=True,
        )

        engine._append_ambiguous_matches(request, report)

        self.assertEqual(len(report.blocking_errors), 1)
        self.assertIn("Final.pdf", report.blocking_errors[0])

    def test_multiple_notebooks_merge_answers_from_each_project(self) -> None:
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=(),
            source_names=(),
            notebook_ids=("one", "two"),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope("one", ("one-source",), ("exam-one",)),
                engine.ProjectQueryScope("two", ("two-source",), ("exam-two",)),
            ),
        )

        with patch.object(
            engine,
            "_run_nlm_json",
            side_effect=[
                {"answer": "### MCQ 1\nfirst"},
                {"answer": "### MCQ 1\nsecond"},
            ],
        ):
            merged = engine._run_nlm_cli_query(request)

        self.assertIn("### MCQ 1", merged.answer)
        self.assertIn("### MCQ 2", merged.answer)

    def test_notebook_list_wrapper_is_normalized(self) -> None:
        self.assertEqual(
            engine._notebook_entries(
                {
                    "notebooks": [
                        {"id": "one", "title": "One"},
                        {"id": "two", "title": "Two"},
                    ],
                    "count": 2,
                }
            ),
            [{"id": "one", "title": "One"}, {"id": "two", "title": "Two"}],
        )

    def test_multi_notebook_imp_answers_keep_one_canonical_heading_set(self) -> None:
        first = engine.QueryResult(
            answer="\n".join(f"{heading}\ncontent" for heading in engine.IMP_HEADINGS)
        )
        second = engine.QueryResult(answer=first.answer.replace("content", "extra"))

        merged = engine._merge_notebook_query_results([first, second], "IMP Points")

        self.assertEqual(
            tuple(
                line
                for line in merged.answer.splitlines()
                if line.startswith("#### ")
            ),
            engine.IMP_HEADINGS,
        )
        self.assertIn("extra", merged.answer)

    def test_multi_notebook_question_blocks_are_renumbered(self) -> None:
        results = [
            engine.QueryResult(answer="### MCQ 1\nfirst"),
            engine.QueryResult(answer="### MCQ 1\nsecond"),
        ]

        merged = engine._merge_notebook_query_results(results, "MCQs")

        self.assertIn("### MCQ 1", merged.answer)
        self.assertIn("### MCQ 2", merged.answer)

    def test_multi_notebook_query_uses_each_project_source_scope(self) -> None:
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=(),
            source_names=(),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope("one", ("source-one",), ("one.pdf",)),
                engine.ProjectQueryScope("two", ("source-two",), ("two.pdf",)),
            ),
        )

        def query_cli(_config, arguments, _timeout, _operation):
            if "source-one" in arguments:
                return {"answer": "### MCQ 1\nfrom one"}
            if "source-two" in arguments:
                return {"answer": "### MCQ 1\nfrom two"}
            return {"answer": "### MCQ 1\nunscoped"}

        with patch.object(engine, "_run_nlm_json", side_effect=query_cli):
            merged = engine._run_nlm_cli_query(request)

        self.assertIn("from one", merged.answer)
        self.assertIn("from two", merged.answer)
        self.assertNotIn("unscoped", merged.answer)

    def test_finalized_document_removes_evidence_only_source_fields(self) -> None:
        draft = (
            "# 📚 Draft\n\n"
            "---\n\n## 📖 Chronological Guide\n\n"
            + "a" * 320
            + "\n\n---\n\n## 🌟 IMP Points\n\n"
            + "\n".join(engine.IMP_HEADINGS)
            + "\n> [!WARNING]\n> None\n> [!CAUTION]\n> None\n\n"
            + "---\n\n## ❓ MCQs\n\nNO_GROUNDED_MCQS\n\n"
            + "---\n\n## ✍️ Written Questions\n\nNO_GROUNDED_WRITTEN_QUESTIONS\n\n"
            + "---\n\n## 🩺 Clinical Cases\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 1:** **[IMP]**\n> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 2:** **[IMP]**\n> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n"
        )
        draft = draft.replace("NO_GROUNDED_MCQS", "> [!NOTE]\n> none")
        draft = draft.replace("NO_GROUNDED_WRITTEN_QUESTIONS", "> [!NOTE]\n> none")
        document = engine.finalize_student_document(
            draft.replace("## ❓ MCQs\n\n", "## ❓ MCQs\n\n**Source:** private.pdf\n\n"),
            {2024},
        )

        self.assertNotIn("**Source:**", document)

    def test_finalized_document_rejects_hidden_source_filenames_and_ids(self) -> None:
        draft = (
            "# 📚 Draft\n\n"
            "---\n\n## 📖 Chronological Guide\n\n"
            + "a" * 320
            + "\n\n---\n\n## 🌟 IMP Points\n\n"
            + "\n".join(engine.IMP_HEADINGS)
            + "\n> [!WARNING]\n> None\n> [!CAUTION]\n> None\n\n"
            + "---\n\n## ❓ MCQs\n\n> [!NOTE]\n> private.pdf\n\n"
            + "---\n\n## ✍️ Written Questions\n\n> [!NOTE]\n> none\n\n"
            + "---\n\n## 🩺 Clinical Cases\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 1:** **[IMP]**\n"
            + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 2:** **[IMP]**\n"
            + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n"
        )

        with self.assertRaises(engine.ValidationError):
            engine.finalize_student_document(draft, {2024})


if __name__ == "__main__":
    unittest.main()
