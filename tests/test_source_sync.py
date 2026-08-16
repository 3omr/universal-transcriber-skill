import json
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

import source_sync
import universal_transcribe as engine
from source_sync import (
    SourceSyncRequest,
    SourceSyncError,
    apply_source_sync,
    audit_source_sync,
    load_sync_manifest,
    source_sync_preflight,
)


class SourceSyncTests(unittest.TestCase):
    def test_apply_replaces_one_conflicting_converted_remote_source(self) -> None:
        source = engine.LocalSource(
            path="/module/.transcriber-cache/converted/slides.pdf",
            relative_path="Lecture/slides.ppt",
            name="slides.ppt",
            normalized_name=engine.normalize_source_key("slides.ppt"),
            normalized_stem=engine.normalize_source_stem("slides.ppt"),
            extension=".ppt",
            size=10,
            role="slides",
            preparation_action="convert",
            preparation_status="ready",
            prepared_extension=".pdf",
            prepared_sha256="new-hash",
        )
        notebook = engine.NotebookTarget("library", "notebook-id", "url", "Toxo")
        old_remote = engine.RemoteSource(
            "old-id",
            "slides.pdf",
            engine.normalize_source_key("slides.pdf"),
            engine.normalize_source_stem("slides.pdf"),
            notebook_uuid="notebook-id",
            content_hash="old-hash",
            status="ready",
        )
        new_remote = engine.RemoteSource(
            "new-id",
            "slides.pdf",
            engine.normalize_source_key("slides.pdf"),
            engine.normalize_source_stem("slides.pdf"),
            notebook_uuid="notebook-id",
            content_hash="new-hash",
            status="ready",
        )
        request = source_sync.NotebookSyncRequest(
            engine=engine,
            config={},
            notebook=notebook,
            source=source,
            execute=True,
            allow_upload=True,
        )

        with patch.object(engine, "list_remote_sources", return_value=[old_remote]), patch.object(
            engine, "_delete_remote_source"
        ) as deleted, patch.object(
            engine, "_wait_for_remote_source_absent", return_value=[]
        ) as waited, patch.object(
            engine,
            "_upload_source_with_retries",
            return_value=engine.UploadOutcome([new_remote], True),
        ) as uploaded:
            status, uploaded_by_run = source_sync._notebook_status(request)

        self.assertEqual(status.status, "uploaded")
        self.assertEqual(status.replaced_source_id, "old-id")
        self.assertEqual(status.replaced_source_title, "slides.pdf")
        self.assertTrue(uploaded_by_run)
        deleted.assert_called_once_with({}, notebook, "old-id")
        waited.assert_called_once_with({}, notebook, "old-id")
        uploaded.assert_called_once_with({}, notebook, source)

    def _manifest(
        self,
        root: Path,
        sources: list[dict],
        *,
        approved: bool = False,
    ) -> Path:
        path = root / "sync.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "module": "toxo",
                    "notebook_targets": ["notebook-id"],
                    "agent_approved": approved,
                    "sources": sources,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _notebook(self) -> engine.NotebookTarget:
        return engine.NotebookTarget("library", "notebook-id", "url", "Toxo")

    def _request(self, root: Path, manifest: Path) -> SourceSyncRequest:
        return SourceSyncRequest(
            engine, {}, "toxo", root, (self._notebook(),), manifest
        )

    def test_apply_requires_explicit_agent_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Lecture").mkdir()
            (root / "Lecture" / "lecture.mp3").write_bytes(b"audio")
            manifest = self._manifest(
                root,
                [{"path": "Lecture/lecture.mp3", "role": "recording", "action": "use"}],
            )

            with self.assertRaisesRegex(SourceSyncError, "agent_approved"):
                apply_source_sync(self._request(root, manifest))

    def test_audit_plans_ppsx_conversion_without_writing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Lecture").mkdir()
            slide = root / "Lecture" / "psychotropic.ppsx"
            slide.write_bytes(b"slides")
            manifest = self._manifest(
                root,
                [{"path": "Lecture/psychotropic.ppsx", "role": "slides", "action": "auto"}],
            )

            with patch.object(engine, "list_remote_sources", return_value=[]):
                report = audit_source_sync(self._request(root, manifest))

            self.assertEqual(report.status, "planned")
            self.assertEqual(report.sources[0].action, "convert")
            self.assertEqual(report.sources[0].upload_extension, ".pdf")
            self.assertFalse((root / ".transcriber-cache").exists())
            self.assertEqual(slide.read_bytes(), b"slides")

    def test_question_classification_reaches_engine_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Questions").mkdir()
            exam = root / "Questions" / "Final.pdf"
            exam.write_bytes(b"exam")
            manifest = self._manifest(
                root,
                [
                    {
                        "path": "Questions/Final.pdf",
                        "role": "past_exam",
                        "year": 2025,
                        "action": "use_remote",
                    }
                ],
            )
            remote = engine.RemoteSource(
                "remote-id",
                "Final.pdf",
                engine.normalize_source_key("Final.pdf"),
                engine.normalize_source_stem("Final.pdf"),
                notebook_uuid="notebook-id",
                status="ready",
            )

            with patch.object(engine, "list_remote_sources", return_value=[remote]):
                report = audit_source_sync(self._request(root, manifest))

            self.assertEqual(report.status, "planned")
            self.assertEqual(report.sources[0].role, "past_exam")
            self.assertEqual(report.sources[0].status, "unchanged")

    def test_apply_uploads_ready_source_and_writes_atomic_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Lecture").mkdir()
            (root / "Lecture" / "lecture.mp3").write_bytes(b"audio")
            manifest = self._manifest(
                root,
                [{"path": "Lecture/lecture.mp3", "role": "recording", "action": "use"}],
                approved=True,
            )

            def upload(_config, _notebook, source):
                remote = engine.RemoteSource(
                    "remote-id",
                    "lecture.mp3",
                    engine.normalize_source_key("lecture.mp3"),
                    engine.normalize_source_stem("lecture.mp3"),
                    notebook_uuid="notebook-id",
                    content_hash=source.prepared_sha256,
                    status="ready",
                )
                return engine.UploadOutcome([remote], True)

            with patch.object(engine, "list_remote_sources", return_value=[]), patch.object(
                engine, "_upload_source_with_retries", side_effect=upload
            ):
                report = apply_source_sync(self._request(root, manifest))

            state_path = root / ".transcriber-cache" / "source-sync" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(report.status, "completed")
            self.assertEqual(report.uploaded_count, 1)
            self.assertEqual(state["sources"][0]["notebooks"][0]["remote_source_id"], "remote-id")

    def test_complete_manifest_is_required_for_module_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Lecture").mkdir()
            (root / "Lecture" / "first.mp3").write_bytes(b"first")
            (root / "Lecture" / "second.mp3").write_bytes(b"second")
            manifest = self._manifest(
                root,
                [{"path": "Lecture/first.mp3", "role": "recording", "action": "use"}],
            )

            with patch.object(engine, "list_remote_sources", return_value=[]):
                report = audit_source_sync(self._request(root, manifest))

            self.assertEqual(report.status, "partial")
            self.assertEqual(report.unreviewed_local_sources, ["Lecture/second.mp3"])

    def test_agent_can_accept_one_changed_remote_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Lecture").mkdir()
            (root / "Lecture" / "lecture.mp3").write_bytes(b"local")
            manifest = self._manifest(
                root,
                [
                    {
                        "path": "Lecture/lecture.mp3",
                        "role": "recording",
                        "action": "use_remote",
                    }
                ],
            )
            remote = engine.RemoteSource(
                "remote-id",
                "lecture.mp3",
                engine.normalize_source_key("lecture.mp3"),
                engine.normalize_source_stem("lecture.mp3"),
                notebook_uuid="notebook-id",
                content_hash="different-hash",
                status="ready",
            )

            with patch.object(engine, "list_remote_sources", return_value=[remote]):
                report = audit_source_sync(self._request(root, manifest))

            self.assertEqual(report.sources[0].status, "accepted-remote")
            self.assertEqual(report.sources[0].notebooks[0].remote_source_id, "remote-id")

    def test_preflight_detects_new_and_changed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Lecture").mkdir()
            source = root / "Lecture" / "lecture.mp3"
            source.write_bytes(b"first")
            manifest = self._manifest(
                root,
                [{"path": "Lecture/lecture.mp3", "role": "recording", "action": "use", "upload": False}],
                approved=True,
            )
            with patch.object(engine, "list_remote_sources", return_value=[]):
                apply_source_sync(self._request(root, manifest))

            source.write_bytes(b"changed")
            (root / "Lecture" / "new.mp3").write_bytes(b"new")
            issues = source_sync_preflight(root)

            self.assertTrue(any("changed source" in issue for issue in issues))
            self.assertTrue(any("new source" in issue for issue in issues))

            source.unlink()
            issues = source_sync_preflight(root)

            self.assertTrue(any("removed source" in issue for issue in issues))

    def test_manifest_rejects_source_outside_module_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = self._manifest(root, [{"path": "../secret.pdf"}])

            with self.assertRaises(SourceSyncError):
                load_sync_manifest(manifest, "toxo")


if __name__ == "__main__":
    unittest.main()
