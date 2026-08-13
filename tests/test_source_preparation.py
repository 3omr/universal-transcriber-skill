import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from universal_transcriber.source_preparation import prepare_manifest_sources


class SourcePreparationTests(unittest.TestCase):
    def test_concurrent_preparation_reuses_one_complete_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            text_file = source_root / "Questions" / "shared.txt"
            text_file.parent.mkdir()
            text_file.write_text("Shared question bank", encoding="utf-8")
            manifest = {
                "assessment_sources": [
                    {"path": "Questions/shared.txt", "type": "question_bank"}
                ]
            }

            with ThreadPoolExecutor(max_workers=2) as executor:
                reports = tuple(
                    executor.map(
                        lambda _: prepare_manifest_sources(
                            source_root, manifest, execute=True
                        ),
                        range(2),
                    )
                )

            prepared_sources = tuple(report.entries[0] for report in reports)

        self.assertTrue(all(source.status == "ready" for source in prepared_sources))
        self.assertEqual(
            {source.prepared_path for source in prepared_sources},
            {prepared_sources[0].prepared_path},
        )
        self.assertEqual(
            {source.prepared_sha256 for source in prepared_sources},
            {prepared_sources[0].prepared_sha256},
        )

    def test_legacy_slide_is_planned_without_modifying_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            slide = source_root / "Lecture" / "anatomy.ppt"
            slide.parent.mkdir()
            original = b"legacy slide bytes"
            slide.write_bytes(original)

            report = prepare_manifest_sources(
                source_root,
                {"slides": {"path": "Lecture/anatomy.ppt", "action": "auto"}},
                execute=False,
            )

            planned = report.entries[0]
            self.assertEqual(planned.action, "convert")
            self.assertEqual(planned.upload_extension, ".pdf")
            self.assertEqual(planned.status, "planned")
            self.assertEqual(slide.read_bytes(), original)
            self.assertFalse(Path(report.cache_root).exists())

    def test_remote_recording_reference_does_not_require_local_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = prepare_manifest_sources(
                temporary_directory,
                {"recording_sources": ["Corrosive 1.m4a"]},
                execute=False,
            )

        self.assertTrue(report.ready)
        self.assertEqual(report.entries[0].status, "remote-only")
        self.assertEqual(report.entries[0].action, "use_remote")

    def test_remote_equivalent_skips_local_ocr_for_existing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf = Path(temporary_directory) / "Lecture" / "book.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"not a valid PDF; remote copy is authoritative")

            report = prepare_manifest_sources(
                temporary_directory,
                {"references": [{"path": "Lecture/book.pdf", "action": "auto"}]},
                execute=True,
                remote_titles=("book.txt",),
            )

        self.assertTrue(report.ready)
        self.assertEqual(report.entries[0].action, "use_remote")
        self.assertEqual(report.mutation_count, 0)

    def test_uninspectable_pdf_is_planned_for_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf = Path(temporary_directory) / "Lecture" / "scan.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"not a valid PDF")

            report = prepare_manifest_sources(
                temporary_directory,
                {"references": [{"path": "Lecture/scan.pdf", "action": "auto"}]},
                execute=False,
            )

        self.assertTrue(report.ready)
        self.assertEqual(report.entries[0].action, "ocr")
        self.assertEqual(report.entries[0].upload_extension, ".pdf")

    def test_text_source_is_planned_for_uploadable_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            text_file = Path(temporary_directory) / "Questions" / "old.txt"
            text_file.parent.mkdir()
            text_file.write_text("question bank", encoding="utf-8")

            report = prepare_manifest_sources(
                temporary_directory,
                {"assessment_sources": [{"path": "Questions/old.txt", "type": "question_bank"}]},
                execute=False,
            )

        self.assertTrue(report.ready)
        self.assertEqual(report.entries[0].action, "convert")
        self.assertEqual(report.entries[0].upload_extension, ".pdf")

    def test_text_conversion_creates_cached_pdf_without_overwriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            text_file = Path(temporary_directory) / "Questions" / "old.txt"
            text_file.parent.mkdir()
            original = "Which drug?\nمعلومة"
            text_file.write_text(original, encoding="utf-8")

            report = prepare_manifest_sources(
                temporary_directory,
                {"assessment_sources": [{"path": "Questions/old.txt", "type": "question_bank"}]},
                execute=True,
            )

            prepared = Path(report.entries[0].prepared_path)
            self.assertTrue(prepared.is_file())
            self.assertEqual(text_file.read_text(encoding="utf-8"), original)

        self.assertTrue(report.ready)

    def test_cached_conversion_keeps_canonical_basename_and_tracks_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            text_file = source_root / "Questions" / "old.txt"
            text_file.parent.mkdir()
            text_file.write_text("first question", encoding="utf-8")
            first = prepare_manifest_sources(
                source_root,
                {"assessment_sources": [{"path": "Questions/old.txt", "type": "question_bank"}]},
                execute=True,
            )
            first_path = Path(first.entries[0].prepared_path)
            self.assertEqual(first_path.name, "old.pdf")

            text_file.write_text("second question", encoding="utf-8")
            second = prepare_manifest_sources(
                source_root,
                {"assessment_sources": [{"path": "Questions/old.txt", "type": "question_bank"}]},
                execute=True,
            )
            second_path = Path(second.entries[0].prepared_path)
            self.assertTrue(first_path.is_file())
            self.assertTrue(second_path.is_file())

            self.assertNotEqual(first_path.parent, second_path.parent)

    def test_relevant_reference_metadata_is_preserved_for_editorial_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            book = Path(temporary_directory) / "Lecture" / "book.pdf"
            book.parent.mkdir()
            book.write_bytes(b"book")

            report = prepare_manifest_sources(
                temporary_directory,
                {
                    "references": [
                        {
                            "path": "Lecture/book.pdf",
                            "type": "textbook",
                            "action": "use",
                            "relevance": "terminology for the taught mechanism",
                            "topics": ["mechanism"],
                            "allow_unspoken_additions": True,
                        }
                    ]
                },
                execute=False,
            )

        reference = report.entries[0]
        self.assertEqual(reference.role, "textbook")
        self.assertTrue(reference.allow_unspoken_additions)
        self.assertEqual(reference.topics, ("mechanism",))

    def test_agent_ignored_reference_is_not_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ignored = Path(temporary_directory) / "Lecture" / "unrelated.ppt"
            ignored.parent.mkdir()
            ignored.write_bytes(b"unrelated")

            report = prepare_manifest_sources(
                temporary_directory,
                {"assessment_sources": [{"path": "Lecture/unrelated.ppt", "type": "ignore"}]},
                execute=True,
            )

        self.assertTrue(report.ready)
        self.assertEqual(report.entries[0].status, "ignored")
        self.assertEqual(report.mutation_count, 0)

    def test_chunk_requires_agent_selected_pages_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf = Path(temporary_directory) / "Lecture" / "book.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"pdf")

            report = prepare_manifest_sources(
                temporary_directory,
                {"references": [{"path": "Lecture/book.pdf", "action": "chunk"}]},
                execute=False,
            )

        self.assertFalse(report.ready)
        self.assertIn("explicit relevant PDF pages", report.blocking_errors[0])

    def test_unsafe_manifest_path_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = prepare_manifest_sources(
                temporary_directory,
                {"references": [{"path": "../private.pdf", "action": "use"}]},
                execute=False,
            )

        self.assertFalse(report.ready)
        self.assertIn("escapes the module", report.blocking_errors[0])

    def test_manifest_cannot_select_one_source_in_two_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = prepare_manifest_sources(
                temporary_directory,
                {
                    "slides": "Lecture/topic.pdf",
                    "references": [{"path": "Lecture/topic.pdf", "action": "use"}],
                },
                execute=False,
            )

        self.assertFalse(report.ready)
        self.assertIn("more than once", report.blocking_errors[0])

    def test_bare_upload_name_resolves_unique_module_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            slide = Path(temporary_directory) / "Lecture" / "legacy.ppt"
            slide.parent.mkdir()
            slide.write_bytes(b"legacy")

            report = prepare_manifest_sources(
                temporary_directory,
                {"approved_uploads": ["legacy.ppt"]},
                execute=False,
            )

        self.assertTrue(report.ready)
        self.assertEqual(report.entries[0].relative_path, "Lecture/legacy.ppt")
        self.assertEqual(report.entries[0].upload_extension, ".pdf")


if __name__ == "__main__":
    unittest.main()
