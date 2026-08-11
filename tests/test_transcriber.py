import importlib.util
import sys
import unittest
from pathlib import Path


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


def remote_source(source_id: str, title: str):
    return engine.RemoteSource(
        source_id=source_id,
        title=title,
        normalized_name=engine.normalize_source_key(title),
        normalized_stem=engine.normalize_source_stem(title),
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


if __name__ == "__main__":
    unittest.main()
