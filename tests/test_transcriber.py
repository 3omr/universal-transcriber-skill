import importlib.util
import json
import multiprocessing
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = (
    Path(__file__).parents[1]
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
ENGINE_PATH = SCRIPTS_DIR / "universal_transcribe.py"
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


def commit_transcript_worker(
    transcripts_dir: str, title: str, file_name: str
) -> None:
    target = engine.OutputTarget(
        transcripts_dir,
        file_name,
        str(Path(transcripts_dir) / file_name),
    )
    identity = engine.TranscriptIdentity("Toxicology", title, "🧪", title)
    engine.commit_managed_transcript(identity, target, f"# {title}\n")


class TranscriberTests(unittest.TestCase):
    def test_concurrent_transcript_commits_keep_both_index_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            context = multiprocessing.get_context("fork")
            workers = (
                context.Process(
                    target=commit_transcript_worker,
                    args=(temporary_directory, "First", "first.md"),
                ),
                context.Process(
                    target=commit_transcript_worker,
                    args=(temporary_directory, "Second", "second.md"),
                ),
            )
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)

            index_content = (Path(temporary_directory) / "Index.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual([worker.exitcode for worker in workers], [0, 0])
        self.assertEqual(index_content.count("first.md"), 1)
        self.assertEqual(index_content.count("second.md"), 1)

    def test_shared_notebook_upload_is_rechecked_under_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = local_source("Questions/Final.pdf", "question_bank")
            reports = (
                phase0_report([source], [], [source]),
                phase0_report([source], [], [source]),
            )
            request = engine.Phase0Request(
                config={},
                requested_notebook_ids=("id",),
                subject="Toxicology",
                sources_root=temporary_directory,
                lecture_name="lecture.m4a",
                recording_sources=("lecture.m4a",),
                slides_path=None,
            )
            remote_inventory: list[engine.RemoteSource] = []
            inventory_lock = threading.Lock()

            def list_remote_sources(notebook_uuid, config):
                with inventory_lock:
                    return list(remote_inventory)

            def upload_missing_sources(config, notebook, missing, remote_sources):
                uploaded = []
                with inventory_lock:
                    if missing and not remote_inventory:
                        remote_inventory.append(
                            engine.RemoteSource(
                                "exam",
                                "Final.pdf",
                                source.normalized_name,
                                source.normalized_stem,
                                notebook_uuid="id",
                                status="ready",
                            )
                        )
                        uploaded = list(missing)
                    refreshed = list(remote_inventory)
                return uploaded, refreshed

            with patch.object(
                engine, "list_remote_sources", side_effect=list_remote_sources
            ), patch.object(
                engine,
                "upload_missing_sources",
                side_effect=upload_missing_sources,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    tuple(
                        executor.map(
                            lambda report: engine._upload_phase0_sources(
                                request, report
                            ),
                            reports,
                        )
                    )

        self.assertEqual(len(remote_inventory), 1)
        self.assertEqual(sum(len(report.uploaded) for report in reports), 1)

    def test_numeric_notebooklm_source_statuses_are_normalized(self) -> None:
        with patch.object(
            engine,
            "_remote_source_inventory",
            return_value=[
                {"id": "ready", "title": "Ready.pdf", "status": 2},
                {"id": "processing", "title": "Processing.pdf", "status": 1},
            ],
        ):
            sources = engine.list_remote_sources("notebook")

        self.assertEqual(
            [(source.title, source.status) for source in sources],
            [("Ready.pdf", "ready"), ("Processing.pdf", "processing")],
        )

    def test_nlm_0_9_8_unsupported_files_are_not_upload_candidates(self) -> None:
        for extension in (".ppsx", ".mkv", ".txt", ".md"):
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

    def test_assessment_source_scope_excludes_recording_and_slides(self) -> None:
        exam = local_source("Questions/Final 2024.pdf", "past_exam")
        report = phase0_report(
            [exam],
            [
                remote_source("recording", "topic.mp3"),
                remote_source("slides", "topic.pdf"),
                remote_source("exam", "Final 2024.txt"),
            ],
        )

        scope = engine._assessment_source_scope(report)

        self.assertEqual(scope.source_ids, ("exam",))
        self.assertEqual(scope.project_scopes[0].source_names_by_id, (("exam", "Final 2024.txt"),))

    def test_unreadable_remote_duplicate_does_not_block_missing_source_uploads(self) -> None:
        duplicate = local_source("Lecture/Book.pdf", "textbook", "fail")
        missing = local_source("Exams/Final 2024.pdf", "past_exam", "pass")
        report = phase0_report([duplicate, missing], [], [missing])

        engine._append_ocr_failures(report)

        self.assertEqual(report.blocking_errors, [])

    def test_converted_source_matches_remote_prepared_hash(self) -> None:
        source = local_source("Lecture/slides.ppt", "slides")
        source.source_sha256 = "original-hash"
        source.prepared_sha256 = "prepared-hash"
        remote = engine.RemoteSource(
            source_id="remote-slides",
            title="slides.pdf",
            normalized_name=engine.normalize_source_key("slides.pdf"),
            normalized_stem=engine.normalize_source_stem("slides.pdf"),
            content_hash="prepared-hash",
        )

        self.assertTrue(engine._source_exists_remotely(source, [remote]))

    def test_legacy_cache_basename_matches_original_source(self) -> None:
        source = local_source("Lecture/slides.ppt", "slides")
        source.path = "/module/slides-a1b2c3d4e5f6.pdf"
        source.prepared_extension = ".pdf"
        remote = remote_source("remote-slides", "slides-a1b2c3d4e5f6.pdf")

        self.assertTrue(engine._source_exists_remotely(source, [remote]))

    def test_agent_use_remote_source_is_not_uploaded_again(self) -> None:
        source = local_source("Lecture/book.pdf", "textbook")
        source.preparation_action = "use_remote"
        duplicates, ambiguous, missing = engine.build_deduplication_plan(
            [source],
            [remote_source("remote-book", "book.txt")],
        )

        self.assertEqual(duplicates, [source])
        self.assertEqual(ambiguous, [])
        self.assertEqual(missing, [])

    def test_processing_remote_source_is_not_evidence_until_ready(self) -> None:
        source = local_source("Questions/Final 2025.pdf", "past_exam")
        source.years = (2025,)
        report = phase0_report(
            [source],
            [
                engine.RemoteSource(
                    "exam", "Final 2025.pdf", source.normalized_name,
                    source.normalized_stem, status="processing"
                )
            ],
        )

        engine._refresh_evidence_metadata(report)

        self.assertEqual(report.year_map, {})
        self.assertEqual(report.evidence_catalog[0]["content_status"], "local_only")
        self.assertEqual(
            engine.build_deduplication_plan([source], report.remote_sources)[2],
            [source],
        )

    def test_processing_remote_source_is_waited_on_instead_of_reuploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = local_source("Questions/Final 2025.pdf", "past_exam")
            report = phase0_report([source], [], missing=[source])
            report.remote_sources = [
                engine.RemoteSource(
                    "exam", "Final 2025.pdf", source.normalized_name,
                    source.normalized_stem, status="processing"
                )
            ]
            request = engine.Phase0Request(
                config={},
                requested_notebook_ids=("notebook",),
                subject="Toxicology",
                sources_root=temporary_directory,
                lecture_name="lecture.m4a",
                recording_sources=("lecture.m4a",),
                slides_path=None,
            )

            with patch.object(
                engine, "_refreshed_inventory_with",
                side_effect=engine.NlmError("still processing"),
            ), patch.object(
                engine, "_refresh_primary_inventory",
            ), patch.object(
                engine, "_upload_source_with_retries",
                side_effect=AssertionError("must not upload a processing duplicate"),
            ):
                engine._upload_phase0_sources(request, report)

            self.assertTrue(
                any(
                    "did not finish processing" in error
                    for error in report.blocking_errors
                )
            )

    def test_required_missing_assessment_must_be_explicitly_approved(self) -> None:
        source = local_source("Questions/Final 2025.pdf", "past_exam")
        report = phase0_report([source], [], [source])
        request = engine.Phase0Request(
            config={},
            requested_notebook_ids=("notebook",),
            subject="Toxicology",
            sources_root="/module",
            lecture_name="lecture.m4a",
            recording_sources=("lecture.m4a",),
            slides_path=None,
            agent_reviewed=True,
            assessment_sources=(),
        )

        with patch.object(engine, "_refresh_primary_inventory"):
            engine._upload_phase0_sources(request, report)

        self.assertIn("not approved for upload", report.blocking_errors[0])

    def test_only_agent_selected_references_enter_query_scope(self) -> None:
        selected = local_source("Lecture/selected book.pdf", "textbook")
        unrelated = local_source("Lecture/unrelated book.pdf", "textbook")
        report = phase0_report(
            [selected, unrelated],
            [
                remote_source("selected", "selected book.pdf"),
                remote_source("unrelated", "unrelated book.pdf"),
            ],
        )
        report.preparation = engine.PreparationReport()
        report.reference_guidance = [{"relative_path": "Lecture/selected book.pdf"}]

        scope = engine._query_scope(report, {"textbook"})

        self.assertEqual(scope.source_ids, ("selected",))

    def test_source_content_change_updates_checkpoint_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "Lecture" / "recording.m4a"
            path.parent.mkdir()
            path.write_bytes(b"first")
            source = engine.LocalSource(
                path=str(path),
                relative_path="Lecture/recording.m4a",
                name="recording.m4a",
                normalized_name=engine.normalize_source_key("recording.m4a"),
                normalized_stem=engine.normalize_source_stem("recording.m4a"),
                extension=".m4a",
                size=5,
                role="recording",
            )
            report = phase0_report([source], [])
            context = engine.PipelineContext(
                config={},
                report=report,
                identity=engine.TranscriptIdentity("T", "L", "🎧", "recording.m4a"),
                source_manifest="",
                badge_instructions="",
                verified_years=set(),
                evidence_sources=[],
                guide_scope=engine.QueryScope((), ()),
                assessment_scope=engine.QueryScope((), ()),
            )
            request = engine.RunRequest(
                subject="T",
                notebook_ids=("n",),
                lecture_name="recording.m4a",
                recording_sources=("recording.m4a",),
                slides_path=None,
                sources_root=temporary_directory,
                title="L",
                emoji="🎧",
                target=engine.OutputTarget(temporary_directory, "L.md", str(Path(temporary_directory) / "L.md")),
                audit_only=False,
            )
            before = engine._phase_fingerprints(request, context)
            path.write_bytes(b"second")
            source.size = 6
            after = engine._phase_fingerprints(request, context)

        self.assertNotEqual(before["guide"], after["guide"])

    def _checkpoint_test_context(self, root: Path):
        notebook = engine.NotebookTarget("library", "notebook", "url", "Notebook")
        report = engine.Phase0Report(
            notebook=notebook,
            local_sources=[],
            remote_sources=[],
            notebooks=(notebook,),
            recording_source="recording.m4a",
            recording_sources=("recording.m4a",),
            year_map={2025: ["Final 2025.pdf"]},
            evidence_catalog=[
                {
                    "canonical_name": "Final 2025.pdf",
                    "normalized_name": engine.normalize_source_key("Final 2025.pdf"),
                    "aliases": ["Final 2025.pdf"],
                    "role": "past_exam",
                    "verified_years": [2025],
                }
            ],
        )
        identity = engine.TranscriptIdentity("Toxicology", "Checkpoint lecture", "🧪", "recording.m4a")
        context = engine._pipeline_context({}, report, identity, {})
        target = engine.OutputTarget(
            str(root / "Transcripts"),
            "Checkpoint lecture 🧪.md",
            str(root / "Transcripts" / "Checkpoint lecture 🧪.md"),
        )
        request = engine.RunRequest(
            subject="Toxicology",
            notebook_ids=("notebook",),
            lecture_name="recording.m4a",
            recording_sources=("recording.m4a",),
            slides_path=None,
            sources_root=str(root),
            title="Checkpoint lecture",
            emoji="🧪",
            target=target,
            audit_only=False,
        )
        return request, context

    def test_checkpoint_reuses_validated_phases_after_written_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            with patch.object(engine, "_query_guide", return_value=engine.QueryResult("guide")), patch.object(
                engine, "_query_imp", return_value=engine.QueryResult("imp")
            ), patch.object(
                engine, "_query_mcqs", return_value=engine.QueryResult("mcqs")
            ), patch.object(
                engine,
                "_query_written",
                side_effect=engine.PhaseValidationError(
                    "Written Questions", ["Question 3 [duplicate_question]"], "failed written"
                ),
            ):
                with self.assertRaises(engine.PhaseValidationError):
                    engine._run_checkpointed_phases(request, context)

            run_dir = next((root / ".transcriber-cache" / "runs").iterdir())
            checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["phases"]["guide"], "validated")
            self.assertEqual(checkpoint["phases"]["imp"], "validated")
            self.assertEqual(checkpoint["phases"]["mcqs"], "validated")
            self.assertEqual(checkpoint["phases"]["written"], "failed")
            self.assertTrue((run_dir / "phase-written-errors.json").exists())
            self.assertTrue((run_dir / "phase-written-recovery.md").exists())

            resumed = request
            with patch.object(engine, "_query_guide", side_effect=AssertionError("guide rerun")), patch.object(
                engine, "_query_imp", side_effect=AssertionError("imp rerun")
            ), patch.object(
                engine, "_query_mcqs", side_effect=AssertionError("mcqs rerun")
            ), patch.object(
                engine, "_query_written", return_value=engine.QueryResult("written repaired")
            ), patch.object(
                engine, "_query_cases", return_value=engine.QueryResult("cases")
            ):
                sections = engine._run_checkpointed_phases(resumed, context)

        self.assertEqual(sections.guide, "guide")
        self.assertEqual(sections.written, "written repaired")
        self.assertEqual(sections.cases, "cases")

    def test_checkpoint_fingerprint_invalidates_only_changed_assessment_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            with patch.object(engine, "_query_guide", return_value=engine.QueryResult("guide")), patch.object(
                engine, "_query_imp", return_value=engine.QueryResult("imp")
            ), patch.object(
                engine, "_query_mcqs", return_value=engine.QueryResult("mcqs")
            ), patch.object(
                engine, "_query_written", return_value=engine.QueryResult("written")
            ), patch.object(
                engine, "_query_cases", return_value=engine.QueryResult("cases")
            ):
                engine._run_checkpointed_phases(request, context)

            run_dir = next((root / ".transcriber-cache" / "runs").iterdir())
            checkpoint_path = run_dir / "checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["status"] = "running"
            checkpoint["phases"]["mcqs"] = "pending"
            checkpoint["phases"]["written"] = "pending"
            checkpoint["phases"]["cases"] = "pending"
            checkpoint["phase_files"].pop("mcqs", None)
            checkpoint["phase_files"].pop("written", None)
            checkpoint["phase_files"].pop("cases", None)
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

            changed_report = engine.replace(
                context.report,
                year_map={2025: ["Final 2025.pdf"], 2026: ["Final 2026.pdf"]},
            )
            changed_context = engine._pipeline_context(
                {}, changed_report, context.identity, {}
            )
            resumed = engine.replace(request, resume_latest=True)
            with patch.object(engine, "_query_guide", side_effect=AssertionError("guide rerun")), patch.object(
                engine, "_query_imp", side_effect=AssertionError("imp rerun")
            ), patch.object(
                engine, "_query_mcqs", return_value=engine.QueryResult("mcqs refreshed")
            ), patch.object(
                engine, "_query_written", return_value=engine.QueryResult("written refreshed")
            ), patch.object(
                engine, "_query_cases", return_value=engine.QueryResult("cases refreshed")
            ):
                sections = engine._run_checkpointed_phases(resumed, changed_context)

        self.assertEqual(sections.guide, "guide")
        self.assertEqual(sections.imp, "imp")
        self.assertEqual(sections.mcqs, "mcqs refreshed")

    def test_checkpoint_fingerprint_invalidates_changed_repaired_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            run_dir, checkpoint = engine._run_directory_for_request(request, context)
            checkpoint["phases"]["mcqs"] = "repaired"
            checkpoint["phase_files"]["mcqs"] = "phase-mcqs.repaired.md"
            (run_dir / "phase-mcqs.repaired.md").write_text("repaired", encoding="utf-8")
            engine._atomic_write_json(run_dir / "checkpoint.json", checkpoint)

            changed_report = engine.replace(
                context.report,
                year_map={2025: ["Final 2025.pdf"], 2026: ["Final 2026.pdf"]},
            )
            changed_context = engine._pipeline_context(
                {}, changed_report, context.identity, {}
            )
            resumed = engine.replace(request, resume_run=str(run_dir))
            _run_dir, refreshed = engine._run_directory_for_request(
                resumed, changed_context
            )

        self.assertEqual(refreshed["phases"]["mcqs"], "pending")
        self.assertNotIn("mcqs", refreshed["phase_files"])

    def test_retry_phase_reuses_phases_before_requested_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            with patch.object(engine, "_query_guide", return_value=engine.QueryResult("guide")), patch.object(
                engine, "_query_imp", return_value=engine.QueryResult("imp")
            ), patch.object(
                engine, "_query_mcqs", return_value=engine.QueryResult("mcqs")
            ), patch.object(
                engine, "_query_written", return_value=engine.QueryResult("written")
            ), patch.object(
                engine, "_query_cases", return_value=engine.QueryResult("cases")
            ):
                engine._run_checkpointed_phases(request, context)

            retried = engine.replace(request, retry_phase="written")
            with patch.object(engine, "_query_guide", side_effect=AssertionError("guide rerun")), patch.object(
                engine, "_query_imp", side_effect=AssertionError("imp rerun")
            ), patch.object(
                engine, "_query_mcqs", side_effect=AssertionError("mcqs rerun")
            ), patch.object(
                engine, "_query_written", return_value=engine.QueryResult("written retry")
            ), patch.object(
                engine, "_query_cases", side_effect=AssertionError("cases rerun")
            ):
                sections = engine._run_checkpointed_phases(retried, context)

        self.assertEqual(sections.written, "written retry")
        self.assertEqual(sections.cases, "cases")

    def test_agent_recovery_accepts_repaired_phase_and_runs_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            with patch.object(engine, "_query_guide", return_value=engine.QueryResult("guide")), patch.object(
                engine, "_query_imp", return_value=engine.QueryResult("imp")
            ), patch.object(
                engine, "_query_mcqs", return_value=engine.QueryResult("mcqs")
            ), patch.object(
                engine,
                "_query_written",
                side_effect=engine.PhaseValidationError(
                    "Written Questions", ["Question 1 [duplicate_question]"], "failed"
                ),
            ):
                with self.assertRaises(engine.PhaseValidationError):
                    engine._run_checkpointed_phases(request, context)

            run_dir = next((root / ".transcriber-cache" / "runs").iterdir())
            repaired_response = run_dir / "written-response.md"
            repaired_response.write_text(
                "### Question 1 **[IMP]**\n\n"
                "**Question:** Enumerate the early complications.\n\n"
                "**Model Answer (Short):**\n"
                "- Airway obstruction.\n- Perforation.\n- Shock.\n",
                encoding="utf-8",
            )
            recovered_request = engine.replace(
                request,
                resume_run=str(run_dir),
                recovery_phase="written",
                recovery_response=str(repaired_response),
            )
            engine._apply_agent_recovery(recovered_request, context)

            with patch.object(engine, "_query_guide", side_effect=AssertionError("guide rerun")), patch.object(
                engine, "_query_imp", side_effect=AssertionError("imp rerun")
            ), patch.object(
                engine, "_query_mcqs", side_effect=AssertionError("mcqs rerun")
            ), patch.object(
                engine, "_query_written", side_effect=AssertionError("written rerun")
            ), patch.object(
                engine, "_query_cases", return_value=engine.QueryResult("cases")
            ):
                sections = engine._run_checkpointed_phases(recovered_request, context)

            checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            repaired_file_exists = (run_dir / "phase-written.repaired.md").is_file()

        self.assertEqual(sections.written, "### Question 1 **[IMP]**\n\n**Question:** Enumerate the early complications.\n\n**Model Answer:**\n- Airway obstruction.\n- Perforation.\n- Shock.")
        self.assertEqual(checkpoint["phases"]["written"], "repaired")
        self.assertEqual(checkpoint["status"], "completed")
        self.assertTrue(repaired_file_exists)

    def test_agent_recovery_rejects_invalid_response_without_overwriting_failed_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            with patch.object(engine, "_query_guide", return_value=engine.QueryResult("guide")), patch.object(
                engine, "_query_imp", return_value=engine.QueryResult("imp")
            ), patch.object(
                engine, "_query_mcqs", return_value=engine.QueryResult("mcqs")
            ), patch.object(
                engine,
                "_query_written",
                side_effect=engine.PhaseValidationError("Written Questions", ["missing"], "original failed"),
            ):
                with self.assertRaises(engine.PhaseValidationError):
                    engine._run_checkpointed_phases(request, context)
            run_dir = next((root / ".transcriber-cache" / "runs").iterdir())
            invalid_response = run_dir / "invalid-response.md"
            invalid_response.write_text("not a question section", encoding="utf-8")
            recovered_request = engine.replace(
                request,
                resume_run=str(run_dir),
                recovery_phase="written",
                recovery_response=str(invalid_response),
            )

            with self.assertRaises(engine.PhaseValidationError):
                engine._apply_agent_recovery(recovered_request, context)

            checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["phases"]["written"], "failed")
            self.assertTrue((run_dir / "phase-written.agent-errors.json").is_file())
            self.assertEqual(
                (run_dir / "phase-written.failed.md").read_text(encoding="utf-8"),
                "original failed",
            )

    def test_agent_recovery_rejects_response_outside_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            run_dir = root / ".transcriber-cache" / "runs" / "saved"
            run_dir.mkdir(parents=True)
            (run_dir / "checkpoint.json").write_text(
                json.dumps(
                    {
                        "subject": request.subject,
                        "title": request.title,
                        "status": "running",
                        "phases": {phase: "pending" for phase in engine.PHASE_ORDER},
                        "phase_files": {},
                        "phase_errors": {},
                        "phase_fingerprints": engine._phase_fingerprints(request, context),
                        "source_manifest_hash": engine._json_hash({}),
                    }
                ),
                encoding="utf-8",
            )
            outside_response = root / "outside.md"
            outside_response.write_text("repair", encoding="utf-8")
            recovered_request = engine.replace(
                request,
                resume_run=str(run_dir),
                recovery_phase="written",
                recovery_response=str(outside_response),
            )

            with self.assertRaises(engine.CheckpointError):
                engine._apply_agent_recovery(recovered_request, context)

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
                engine.ProjectQueryScope(
                    "project-one",
                    ("recording",),
                    ("topic.mp3",),
                    (("recording", "topic.mp3"),),
                ),
                engine.ProjectQueryScope(
                    "project-two",
                    ("exam",),
                    ("Final 2024.txt", "Final 2024.pdf"),
                    (("exam", "Final 2024.txt"),),
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
                "**Options:**\n"
                "a. One\n"
                "b. Two\n"
                "c. Three\n"
                "d. Four\n"
                "**Correct Answer:** a. One\n"
                "**Clinical Explanation (Egyptian Arabic):** الدكتور أكد النقطة دي "
                "لأنها مهمة جداً في الامتحان والعلاج."
            )
        )

        self.assertEqual(
            engine.validate_mcqs(result, engine.QuestionEvidence({}, [])), []
        )

    def test_imp_only_written_question_does_not_require_a_source_field(self) -> None:
        result = engine.QueryResult(
            answer=(
                "### Question 1 **[IMP]**\n\n"
                "**Question:** Give reason for the doctor's warning.\n"
                "**Model Answer (Short):** Because the error can cause a lethal complication."
            )
        )

        self.assertEqual(
            engine.validate_written(result, engine.QuestionEvidence({}, [])), []
        )

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

    def test_assessment_prompts_use_compact_source_context(self) -> None:
        report = phase0_report([], [])
        report.year_map = {
            2021: ["End 2021.pdf"],
            2023: ["Final Toxico 2023.pdf"],
            2024: ["فاينال توكسو 2024.pdf"],
        }
        report.question_banks = ["MCQs integrated.pdf", "Khalsa questions.txt"]
        context = engine.build_assessment_source_context(report)

        prompt = engine.build_mcq_prompt(
            "Food Poisoning",
            context,
            engine.canonical_badge_instructions(report.year_map),
            {"mcq": {"options": {"count": 4}}},
        )

        self.assertLessEqual(len(context), engine.MAX_ASSESSMENT_CONTEXT_CHARS)
        self.assertLessEqual(len(prompt), engine.MAX_ASSESSMENT_QUERY_CHARS)
        self.assertIn("Final Toxico 2023.pdf", prompt)
        self.assertIn("فاينال توكسو 2024.pdf", prompt)
        self.assertNotIn("REFERENCE ENRICHMENT POLICY", prompt)

    def test_assessment_prompts_enforce_strict_lecture_scope(self) -> None:
        report = phase0_report([], [])
        report.year_map = {2023: ["Final 2023.pdf"]}
        context = engine.build_assessment_source_context(report)
        badge_inst = engine.canonical_badge_instructions(report.year_map)

        mcq_prompt = engine.build_mcq_prompt("Wounds", context, badge_inst)
        written_prompt = engine.build_written_prompt("Wounds", context, badge_inst)
        case_prompt = engine.build_case_prompt("Wounds", context, badge_inst)

        self.assertIn("STRICT LECTURE SCOPE CONSTRAINT", mcq_prompt)
        self.assertIn("EXCLUDE questions belonging to other chapters", mcq_prompt)
        self.assertIn("STRICT LECTURE SCOPE CONSTRAINT", written_prompt)
        self.assertIn("EXCLUDE questions belonging to other lectures", written_prompt)
        self.assertIn("STRICT LECTURE SCOPE CONSTRAINT", case_prompt)

    def test_generic_invalid_query_does_not_quarantine_healthy_sources(self) -> None:
        source_ids = ("source-one", "source-two")
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=source_ids,
            source_names=("one.txt", "two.txt"),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope(
                    "one",
                    source_ids,
                    ("one.txt", "two.txt"),
                    tuple(zip(source_ids, ("one.txt", "two.txt"))),
                ),
            ),
        )

        with patch.object(
            engine,
            "_run_nlm_json",
            side_effect=engine.NlmError(
                "The query request is invalid. Check the notebook ID, source IDs, and query arguments."
            ),
        ):
            with self.assertRaises(engine.NlmError) as raised:
                engine._run_nlm_cli_query(request)

        self.assertEqual(raised.exception.source_quarantine, ())

    def test_generic_assessment_query_retries_with_compact_prompt(self) -> None:
        query = engine.PhaseQuery(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text=(
                "Create only the body of the ❓ MCQs section.\n\n"
                "VERIFIED ASSESSMENT SOURCES\n"
                "Extract every relevant MCQ from the selected sources."
            ),
            phase_name="MCQs",
            validator=lambda _result: [],
            source_ids=("source-one",),
            source_names=("one.txt",),
        )

        def reject_manifest_then_answer(_query, prompt):
            if "VERIFIED ASSESSMENT SOURCES" in prompt:
                raise engine.NlmError("query request is invalid")
            return engine.QueryResult(
                "A grounded MCQ answer that is deliberately long enough for the "
                "phase response validator."
            )

        with patch.object(engine, "_run_query_once", side_effect=reject_manifest_then_answer):
            result = engine.run_nlm_query(query)

        self.assertIn("grounded MCQ answer", result.answer)
        self.assertEqual(result.source_quarantine, ())

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

    def test_past_exam_manifest_supports_multiple_verified_years(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions = Path(temporary_directory) / "Questions"
            questions.mkdir()
            exam = questions / "Past Exams Collection.pdf"
            exam.write_bytes(b"exam")

            sources = engine.scan_local_sources(
                temporary_directory,
                (
                    {
                        "path": "Questions/Past Exams Collection.pdf",
                        "type": "past_exam",
                        "years": [2022, 2024, 2025],
                    },
                ),
                require_assessment_manifest=True,
            )

        self.assertEqual(sources[0].years, (2022, 2024, 2025))
        self.assertTrue(sources[0].years_verified_by_manifest)

    def test_assessment_manifest_rejects_conflicting_year_and_years(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions = Path(temporary_directory) / "Questions"
            questions.mkdir()
            (questions / "Final.pdf").write_bytes(b"exam")

            with self.assertRaises(engine.Phase0Error):
                engine.scan_local_sources(
                    temporary_directory,
                    (
                        {
                            "path": "Questions/Final.pdf",
                            "type": "past_exam",
                            "year": 2025,
                            "years": [2024, 2025],
                        },
                    ),
                    require_assessment_manifest=True,
                )

    def test_question_bank_cannot_claim_past_exam_year(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            questions = Path(temporary_directory) / "Questions"
            questions.mkdir()
            (questions / "Bank.pdf").write_bytes(b"bank")

            with self.assertRaises(engine.Phase0Error):
                engine.scan_local_sources(
                    temporary_directory,
                    (
                        {
                            "path": "Questions/Bank.pdf",
                            "type": "question_bank",
                            "year": 2025,
                        },
                    ),
                    require_assessment_manifest=True,
                )

    def test_filename_years_support_current_exam_years_for_inventory_only(self) -> None:
        self.assertEqual(engine.extract_filename_exam_years("End 2025.pdf"), (2025,))
        self.assertEqual(engine.extract_filename_exam_years("End 2024.pdf"), (2024,))

    def test_filename_year_does_not_enter_verified_exam_map_without_manifest(self) -> None:
        source = local_source("Questions/Final 2025.pdf", "past_exam")
        source.years = (2025,)

        self.assertEqual(engine.build_exam_year_map([source]), {})

    def test_badges_accept_verified_2025_and_reject_unverified_2099(self) -> None:
        self.assertTrue(
            engine._badge_is_valid("**[Past Exams - 2022, 2025]**", {2022, 2025})
        )
        self.assertTrue(
            engine._badge_is_valid("**[Past Exams - 2026]**", {2026})
        )
        self.assertFalse(
            engine._badge_is_valid("**[Past Exams - 2099]**", {2099})
        )

    def test_exact_mcq_duplicates_merge_years_and_all_sources(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\n b. Naloxone\n c. Vitamin K\n d. Pralidoxime\n"
            "**Source:** Final Exam 2022.pdf\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n\n"
            "### MCQ 2 **[Past Exams - 2025]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\n b. Naloxone\n c. Vitamin K\n d. Pralidoxime\n"
            "**Source:** Final Exam 2025.pdf\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n"
        )
        catalog = [
            {
                "canonical_name": "Final Exam 2022.pdf",
                "normalized_name": engine.normalize_source_key("Final Exam 2022.pdf"),
                "aliases": ["Final Exam 2022.pdf"],
                "role": "past_exam",
                "verified_years": [2022],
                "content_status": "available",
            },
            {
                "canonical_name": "Final Exam 2025.pdf",
                "normalized_name": engine.normalize_source_key("Final Exam 2025.pdf"),
                "aliases": ["Final Exam 2025.pdf"],
                "role": "past_exam",
                "verified_years": [2025],
                "content_status": "available",
            },
        ]

        merged = engine.deduplicate_question_section(
            answer,
            "MCQ",
            {2022: ["Final Exam 2022.pdf"], 2025: ["Final Exam 2025.pdf"]},
            catalog,
        )

        self.assertEqual(len(engine._section_blocks(merged, "MCQ")), 1)
        self.assertIn("Past Exams - 2022, 2025", merged)
        self.assertIn("**Source:** Final Exam 2022.pdf", merged)
        self.assertIn("**Source:** Final Exam 2025.pdf", merged)

    def test_question_fingerprint_ignores_original_exam_number(self) -> None:
        first = "### MCQ 1 **[IMP]**\n\n**Question:** 13) Which antidote is used?"
        second = "### MCQ 2 **[IMP]**\n\n**Question:** Which antidote is used?"

        self.assertEqual(
            engine._question_fingerprint(first, "MCQ"),
            engine._question_fingerprint(second, "MCQ"),
        )

    def test_written_duplicates_merge_all_verified_years_and_sources(self) -> None:
        answer = (
            "### Question 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** 2) Early complication of corrosion:-\n"
            "**Source:** Final Exam 2022.pdf\n"
            "**Model Answer (Short):**\n- Airway obstruction.\n\n"
            "### Question 2 **[Past Exams - 2024]**\n\n"
            "**Question (verbatim):** Early complication of corrosion:-\n"
            "**Source:** Final Exam 2024.pdf\n"
            "**Model Answer (Short):**\n- Airway obstruction.\n"
        )
        catalog = [
            {
                "canonical_name": f"Final Exam {year}.pdf",
                "normalized_name": engine.normalize_source_key(f"Final Exam {year}.pdf"),
                "aliases": [f"Final Exam {year}.pdf"],
                "role": "past_exam",
                "verified_years": [year],
                "content_status": "available",
            }
            for year in (2022, 2024)
        ]

        merged = engine.deduplicate_question_section(
            answer,
            "Question",
            {2022: ["Final Exam 2022.pdf"], 2024: ["Final Exam 2024.pdf"]},
            catalog,
        )

        self.assertEqual(len(engine._section_blocks(merged, "Question")), 1)
        self.assertIn("Past Exams - 2022, 2024", merged)
        self.assertIn("Final Exam 2022.pdf", merged)
        self.assertIn("Final Exam 2024.pdf", merged)

    def test_past_exam_and_question_bank_duplicates_keep_both_badges(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\nb. Naloxone\nc. Vitamin K\nd. Pralidoxime\n"
            "**Source:** Final Exam 2022.pdf\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n\n"
            "### MCQ 2 **[Question Bank]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\nb. Naloxone\nc. Vitamin K\nd. Pralidoxime\n"
            "**Source:** Toxico Bank.pdf\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n"
        )
        catalog = [
            {
                "canonical_name": "Final Exam 2022.pdf",
                "normalized_name": engine.normalize_source_key("Final Exam 2022.pdf"),
                "aliases": ["Final Exam 2022.pdf"],
                "role": "past_exam",
                "verified_years": [2022],
                "content_status": "available",
            },
            {
                "canonical_name": "Toxico Bank.pdf",
                "normalized_name": engine.normalize_source_key("Toxico Bank.pdf"),
                "aliases": ["Toxico Bank.pdf"],
                "role": "question_bank",
                "verified_years": [],
                "content_status": "available",
            },
        ]

        merged = engine.deduplicate_question_section(
            answer,
            "MCQ",
            {2022: ["Final Exam 2022.pdf"]},
            catalog,
        )

        self.assertEqual(len(engine._section_blocks(merged, "MCQ")), 1)
        self.assertIn("Past Exams - 2022", merged)
        self.assertIn("[Question Bank]", merged)
        self.assertIn("Toxico Bank.pdf", merged)

    def test_different_mcq_answers_create_semantic_review_candidate(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\nb. Naloxone\nc. Vitamin K\nd. Pralidoxime\n"
            "**Source:** Final Exam 2022.pdf\n"
            "**Correct Answer:** b. Naloxone\n\n"
            "### MCQ 2 **[Past Exams - 2025]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\nb. Naloxone\nc. Vitamin K\nd. Pralidoxime\n"
            "**Source:** Final Exam 2025.pdf\n"
            "**Correct Answer:** a. Atropine\n"
        )

        errors = engine._duplicate_question_errors(answer)

        self.assertTrue(any("unsafe_duplicate_merge" in error for error in errors))

    def test_mcq_duplicates_with_different_correct_option_stay_separate(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\n b. Naloxone\n c. Vitamin K\n d. Pralidoxime\n"
            "**Source:** Final Exam 2022.pdf\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n\n"
            "### MCQ 2 **[Past Exams - 2025]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\n b. Naloxone\n c. Vitamin K\n d. Pralidoxime\n"
            "**Source:** Final Exam 2025.pdf\n"
            "**Correct Answer:** a. Atropine\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n"
        )

        self.assertEqual(
            len(engine._section_blocks(engine.deduplicate_question_section(answer, "MCQ"), "MCQ")),
            2,
        )

    def test_sourced_and_imp_copies_stay_separate_without_recording_evidence(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** Which antidote is used?\n"
            "**Options (verbatim):**\n"
            "a. Atropine\n b. Naloxone\n c. Vitamin K\n d. Pralidoxime\n"
            "**Source:** Final Exam 2022.pdf\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n\n"
            "### MCQ 2 **[IMP]**\n\n"
            "**Question:** Which antidote is used?\n"
            "**Options:**\n"
            "a. Atropine\n b. Naloxone\n c. Vitamin K\n d. Pralidoxime\n"
            "**Correct Answer:** b. Naloxone\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح\n"
        )

        merged = engine.deduplicate_question_section(answer, "MCQ")

        self.assertEqual(len(engine._section_blocks(merged, "MCQ")), 2)

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

    def test_large_project_scope_is_batched_for_notebooklm(self) -> None:
        source_ids = tuple(f"source-{index}" for index in range(12))
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=source_ids,
            source_names=tuple(f"source-{index}.pdf" for index in range(12)),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope(
                    "one",
                    source_ids,
                    tuple(
                        f"source-{index}.pdf" for index in range(12)
                    ),
                ),
            ),
        )

        calls: list[list[str]] = []

        def query_cli(_config, arguments, _timeout, _operation):
            calls.append(arguments)
            return {"answer": "### MCQ 1\nfrom batch"}

        with patch.object(engine, "_run_nlm_json", side_effect=query_cli):
            merged = engine._run_nlm_cli_query(request)

        source_arguments = [
            arguments[arguments.index("--source-ids") + 1]
            for arguments in calls
        ]
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            source_arguments,
            [
                ",".join(source_ids[: engine.MAX_SOURCE_IDS_PER_QUERY]),
                ",".join(source_ids[engine.MAX_SOURCE_IDS_PER_QUERY : 2 * engine.MAX_SOURCE_IDS_PER_QUERY]),
                ",".join(source_ids[2 * engine.MAX_SOURCE_IDS_PER_QUERY : 3 * engine.MAX_SOURCE_IDS_PER_QUERY]),
                ",".join(source_ids[3 * engine.MAX_SOURCE_IDS_PER_QUERY :]),
            ],
        )
        self.assertIn("from batch", merged.answer)

    def test_rejected_source_group_is_retried_as_smaller_groups(self) -> None:
        source_ids = ("source-one", "source-two", "source-three")
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=source_ids,
            source_names=tuple(f"{source_id}.pdf" for source_id in source_ids),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope("one", source_ids, ("exams.pdf",)),
            ),
        )

        def query_cli(_config, arguments, _timeout, _operation):
            selected_ids = arguments[arguments.index("--source-ids") + 1].split(",")
            if len(selected_ids) > 1:
                raise engine.NlmError("source group was rejected")
            return {"answer": f"### MCQ 1\nfrom {selected_ids[0]}"}

        with patch.object(engine, "_run_nlm_json", side_effect=query_cli):
            merged = engine._run_nlm_cli_query(request)

        for source_id in source_ids:
            self.assertIn(f"from {source_id}", merged.answer)

    def test_rejected_singleton_is_quarantined_and_valid_siblings_continue(self) -> None:
        source_ids = ("source-one", "bad-source", "source-three")
        source_names = ("one.txt", "bad.txt", "three.txt")
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=source_ids,
            source_names=source_names,
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope(
                    "one",
                    source_ids,
                    source_names,
                    tuple(zip(source_ids, source_names)),
                ),
            ),
        )

        def query_cli(_config, arguments, _timeout, _operation):
            selected_ids = arguments[arguments.index("--source-ids") + 1].split(",")
            if "bad-source" in selected_ids and len(selected_ids) > 1:
                raise engine.NlmError("invalid source id bad-source")
            if selected_ids == ["bad-source"]:
                raise engine.NlmError("invalid source id bad-source")
            return {"answer": f"### MCQ 1\nfrom {selected_ids[0]}"}

        with patch.object(engine, "_run_nlm_json", side_effect=query_cli):
            merged = engine._run_nlm_cli_query(request)

        self.assertIn("from source-one", merged.answer)
        self.assertIn("from source-three", merged.answer)
        self.assertNotIn("bad.txt", merged.source_names)
        self.assertEqual(
            merged.source_quarantine,
            (
                engine.SourceQuarantine(
                    "one", "bad-source", "bad.txt", "invalid source id bad-source"
                ),
            ),
        )

    def test_split_scope_keeps_only_matching_source_names(self) -> None:
        scope = engine.ProjectQueryScope(
            "one",
            ("one", "two", "three"),
            ("one.txt", "two.txt", "three.txt"),
            (("one", "one.txt"), ("two", "two.txt"), ("three", "three.txt")),
        )

        child = engine._slice_project_scope(scope, 1, 2)

        self.assertEqual(child.source_ids, ("two",))
        self.assertEqual(child.source_names, ("two.txt",))
        self.assertEqual(child.source_names_by_id, (("two", "two.txt"),))

    def test_all_rejected_sources_fail_without_an_empty_assessment_success(self) -> None:
        source_ids = ("bad-one", "bad-two")
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=source_ids,
            source_names=("one.txt", "two.txt"),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope(
                    "one",
                    source_ids,
                    ("one.txt", "two.txt"),
                    tuple(zip(source_ids, ("one.txt", "two.txt"))),
                ),
            ),
        )

        def reject_selected_source(_config, arguments, _timeout, _operation):
            selected_ids = arguments[arguments.index("--source-ids") + 1].split(",")
            raise engine.NlmError(f"invalid source id {selected_ids[0]}")

        with patch.object(engine, "_run_nlm_json", side_effect=reject_selected_source):
            with self.assertRaises(engine.NlmError) as raised:
                engine._run_nlm_cli_query(request)

        self.assertEqual(
            {item.source_id for item in raised.exception.source_quarantine},
            set(source_ids),
        )

    def test_timeout_is_not_quarantined_as_a_bad_source(self) -> None:
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=("source-one", "source-two"),
            source_names=("one.txt", "two.txt"),
            phase_name="MCQs",
            project_scopes=(
                engine.ProjectQueryScope(
                    "one",
                    ("source-one", "source-two"),
                    ("one.txt", "two.txt"),
                    (("source-one", "one.txt"), ("source-two", "two.txt")),
                ),
            ),
        )

        with patch.object(
            engine,
            "_run_nlm_json",
            side_effect=engine.NlmError("nlm notebook query timed out"),
        ):
            with self.assertRaises(engine.NlmError) as raised:
                engine._run_nlm_cli_query(request)

        self.assertEqual(raised.exception.source_quarantine, ())

    def test_source_quarantine_stops_repeated_attempts_for_immediate_recovery(self) -> None:
        quarantine = engine.SourceQuarantine(
            "notebook", "bad-source", "bad.txt", "query request is invalid"
        )
        query = engine.PhaseQuery(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            phase_name="MCQs",
            validator=lambda _result: [],
            source_ids=("bad-source",),
            source_names=("bad.txt",),
        )
        with patch.object(
            engine,
            "_run_query_once",
            side_effect=engine.NlmError("no queryable sources", (quarantine,)),
        ) as query_once:
            with self.assertRaises(engine.PhaseValidationError) as raised:
                engine.run_nlm_query(query)

        self.assertEqual(query_once.call_count, 1)
        self.assertEqual(raised.exception.source_quarantine, (quarantine,))

    def test_successful_no_grounded_answer_is_not_treated_as_source_failure(self) -> None:
        request = engine.NlmQueryRequest(
            config={},
            notebook=engine.NotebookTarget("one", "one", "url", "One"),
            query_text="Create the section",
            source_ids=("source-one",),
            source_names=("one.txt",),
            phase_name="MCQs",
        )

        with patch.object(
            engine, "_run_nlm_json", return_value={"answer": engine.NO_MCQS}
        ):
            result = engine._run_nlm_cli_query(request)

        self.assertEqual(result.answer, engine.NO_MCQS)
        self.assertEqual(result.source_quarantine, ())

    def test_pdf_matches_remote_txt_without_upload(self) -> None:
        local = local_source("Questions/final Toxico 2023.pdf", "past_exam")
        remote = remote_source("exam-2023", "final Toxico 2023.txt")

        duplicates, ambiguous, missing = engine.build_deduplication_plan(
            [local], [remote]
        )

        self.assertEqual(duplicates, [local])
        self.assertEqual(ambiguous, [])
        self.assertEqual(missing, [])

    def test_quarantine_is_saved_in_phase_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            checkpoint = {"phases": {}, "phase_errors": {}}
            quarantine = engine.SourceQuarantine(
                "notebook", "bad-source", "bad.txt", "invalid source"
            )

            engine._save_phase_checkpoint(
                engine.PhaseCheckpointUpdate(
                    run_dir,
                    checkpoint,
                    "mcqs",
                    "validated",
                    "answer",
                    source_quarantine=(quarantine,),
                )
            )

            saved = json.loads((run_dir / "checkpoint.json").read_text())

        self.assertEqual(saved["source_quarantine"]["mcqs"][0]["source_id"], "bad-source")

    def test_terminal_quarantine_is_saved_with_failed_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            checkpoint = {"phases": {}, "phase_errors": {}}
            quarantine = engine.SourceQuarantine(
                "notebook", "bad-source", "bad.txt", "invalid source"
            )

            def failed_query():
                raise engine.NlmError("no queryable sources", (quarantine,))

            with self.assertRaises(engine.PhaseValidationError):
                engine._execute_checkpointed_phase(
                    "mcqs", failed_query, run_dir, checkpoint
                )

            saved = json.loads((run_dir / "checkpoint.json").read_text())
            recovery = json.loads(
                (run_dir / "phase-mcqs-sources.json").read_text()
            )

        self.assertEqual(saved["phases"]["mcqs"], "failed")
        self.assertEqual(
            saved["source_quarantine"]["mcqs"][0]["source_id"], "bad-source"
        )
        self.assertEqual(recovery["source_quarantine"][0]["source_name"], "bad.txt")

    def test_quarantined_source_is_deleted_and_replaced_from_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            local_path = root / "Questions" / "Final 2024.pdf"
            local_path.parent.mkdir(parents=True)
            local_path.write_bytes(b"local exam")
            local = engine.LocalSource(
                path=str(local_path),
                relative_path="Questions/Final 2024.pdf",
                name="Final 2024.pdf",
                normalized_name=engine.normalize_source_key("Final 2024.pdf"),
                normalized_stem=engine.normalize_source_stem("Final 2024.pdf"),
                extension=".pdf",
                size=10,
                role="past_exam",
            )
            notebook = engine.NotebookTarget("library", "notebook", "url", "Notebook")
            old_remote = engine.RemoteSource(
                "old-source",
                "Final 2024.txt",
                engine.normalize_source_key("Final 2024.txt"),
                engine.normalize_source_stem("Final 2024.txt"),
                notebook_uuid="notebook",
                status="ready",
            )
            new_remote = engine.RemoteSource(
                "new-source",
                "Final 2024.pdf",
                engine.normalize_source_key("Final 2024.pdf"),
                engine.normalize_source_stem("Final 2024.pdf"),
                notebook_uuid="notebook",
                status="ready",
            )
            report = engine.Phase0Report(
                notebook=notebook,
                notebooks=(notebook,),
                local_sources=[local],
                remote_sources=[old_remote],
            )
            context = engine._pipeline_context(
                {}, report, engine.TranscriptIdentity("T", "L", "🎧", "Final 2024"), {}
            )
            request = engine.RunRequest(
                subject="Toxicology",
                notebook_ids=("notebook",),
                lecture_name="Final 2024",
                recording_sources=("Final 2024",),
                slides_path=None,
                sources_root=str(root),
                title="L",
                emoji="🎧",
                target=engine.OutputTarget(str(root), "L.md", str(root / "L.md")),
                audit_only=False,
                source_manifest={},
            )
            quarantine = engine.SourceQuarantine(
                "notebook", "old-source", "Final 2024.txt", "query request is invalid"
            )
            checkpoint = {"phases": {}, "phase_errors": {}}
            with patch.object(
                engine,
                "list_remote_sources",
                side_effect=[[old_remote], []],
            ) as listed, patch.object(
                engine,
                "_delete_remote_source",
            ) as deleted, patch.object(
                engine,
                "_upload_source_with_retries",
                return_value=engine.UploadOutcome([new_remote], True),
            ) as uploaded:
                refreshed_context, replacements = engine._replace_quarantined_sources(
                    request,
                    context,
                    (quarantine,),
                    root,
                    checkpoint,
                    "mcqs",
                )

        deleted.assert_called_once_with({}, notebook, "old-source")
        uploaded.assert_called_once_with({}, notebook, local)
        self.assertEqual(listed.call_count, 2)
        self.assertEqual(replacements[0].new_source_id, "new-source")
        self.assertEqual(
            refreshed_context.report.remote_sources[0].source_id, "new-source"
        )
        self.assertEqual(
            checkpoint["source_replacements"]["mcqs"][0]["local_path"],
            "Questions/Final 2024.pdf",
        )

    def test_source_replacement_refuses_ambiguous_local_matches_before_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_path = root / "Questions" / "Final.pdf"
            second_path = root / "Lecture" / "Final.pdf"
            first_path.parent.mkdir(parents=True)
            second_path.parent.mkdir(parents=True)
            first_path.write_bytes(b"one")
            second_path.write_bytes(b"two")
            locals_ = [
                engine.LocalSource(
                    path=str(path),
                    relative_path=str(path.relative_to(root)),
                    name=path.name,
                    normalized_name=engine.normalize_source_key(path.name),
                    normalized_stem=engine.normalize_source_stem(path.name),
                    extension=".pdf",
                    size=3,
                    role="question_bank",
                )
                for path in (first_path, second_path)
            ]
            notebook = engine.NotebookTarget("library", "notebook", "url", "Notebook")
            remote = engine.RemoteSource(
                "bad-source",
                "Final.txt",
                engine.normalize_source_key("Final.txt"),
                engine.normalize_source_stem("Final.txt"),
                notebook_uuid="notebook",
                status="ready",
            )
            report = engine.Phase0Report(
                notebook=notebook,
                notebooks=(notebook,),
                local_sources=locals_,
                remote_sources=[remote],
            )
            context = engine._pipeline_context(
                {}, report, engine.TranscriptIdentity("T", "L", "🎧", "Final"), {}
            )
            request = engine.RunRequest(
                subject="Toxicology",
                notebook_ids=("notebook",),
                lecture_name="Final",
                recording_sources=("Final",),
                slides_path=None,
                sources_root=str(root),
                title="L",
                emoji="🎧",
                target=engine.OutputTarget(str(root), "L.md", str(root / "L.md")),
                audit_only=False,
                source_manifest={},
            )
            with patch.object(engine, "_delete_remote_source") as deleted:
                with self.assertRaises(engine.Phase0Error) as raised:
                    engine._replace_quarantined_sources(
                        request,
                        context,
                        (engine.SourceQuarantine("notebook", "bad-source", "Final.txt", "invalid"),),
                        root,
                        {"phases": {}, "phase_errors": {}},
                        "mcqs",
                    )

        deleted.assert_not_called()
        self.assertIn("2 local uploadable matches", str(raised.exception))

    def test_source_replacement_reconciles_rotated_remote_uuid_by_canonical_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            local_path = root / "Questions" / "End 2021.pdf"
            local_path.parent.mkdir(parents=True)
            local_path.write_bytes(b"local exam")
            local = engine.LocalSource(
                path=str(local_path),
                relative_path="Questions/End 2021.pdf",
                name="End 2021.pdf",
                normalized_name=engine.normalize_source_key("End 2021.pdf"),
                normalized_stem=engine.normalize_source_stem("End 2021.pdf"),
                extension=".pdf",
                size=10,
                role="past_exam",
            )
            notebook = engine.NotebookTarget("library", "notebook", "url", "Notebook")
            rotated_remote = engine.RemoteSource(
                "rotated-source",
                "End 2021.pdf",
                engine.normalize_source_key("End 2021.pdf"),
                engine.normalize_source_stem("End 2021.pdf"),
                notebook_uuid="notebook",
                status="ready",
            )
            replacement = engine.RemoteSource(
                "replacement-source",
                "End 2021.pdf",
                engine.normalize_source_key("End 2021.pdf"),
                engine.normalize_source_stem("End 2021.pdf"),
                notebook_uuid="notebook",
                status="ready",
            )
            report = engine.Phase0Report(
                notebook=notebook,
                notebooks=(notebook,),
                local_sources=[local],
                remote_sources=[rotated_remote],
            )
            context = engine._pipeline_context(
                {}, report, engine.TranscriptIdentity("T", "L", "🎧", "End 2021"), {}
            )
            request = engine.RunRequest(
                subject="Toxicology",
                notebook_ids=("notebook",),
                lecture_name="End 2021",
                recording_sources=("End 2021",),
                slides_path=None,
                sources_root=str(root),
                title="L",
                emoji="🎧",
                target=engine.OutputTarget(str(root), "L.md", str(root / "L.md")),
                audit_only=False,
                source_manifest={},
            )
            with patch.object(
                engine,
                "list_remote_sources",
                side_effect=[[rotated_remote], []],
            ), patch.object(engine, "_delete_remote_source") as deleted, patch.object(
                engine,
                "_upload_source_with_retries",
                return_value=engine.UploadOutcome([replacement], True),
            ):
                _context, replacements = engine._replace_quarantined_sources(
                    request,
                    context,
                    (engine.SourceQuarantine("notebook", "stale-source", "End 2021.txt", "invalid"),),
                    root,
                    {"phases": {}, "phase_errors": {}},
                    "mcqs",
                )

        deleted.assert_called_once_with({}, notebook, "rotated-source")
        self.assertEqual(replacements[0].old_source_id, "rotated-source")

    def test_phase_auto_retries_after_source_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            quarantine = engine.SourceQuarantine(
                "notebook", "bad-source", "bad.txt", "query request is invalid"
            )
            mcq_calls = 0

            def query_mcqs(_context):
                nonlocal mcq_calls
                mcq_calls += 1
                if mcq_calls == 1:
                    raise engine.PhaseValidationError(
                        "MCQs", ["no queryable sources"], source_quarantine=(quarantine,)
                    )
                return engine.QueryResult("mcqs after replacement")

            with patch.object(engine, "_query_guide", return_value=engine.QueryResult("guide")), patch.object(
                engine, "_query_imp", return_value=engine.QueryResult("imp")
            ), patch.object(
                engine, "_query_mcqs", side_effect=query_mcqs
            ), patch.object(
                engine,
                "_replace_quarantined_sources",
                return_value=(context, []),
            ) as replace_sources, patch.object(
                engine, "_query_written", return_value=engine.QueryResult("written")
            ), patch.object(
                engine, "_query_cases", return_value=engine.QueryResult("cases")
            ):
                sections = engine._run_checkpointed_phases(request, context)

        self.assertEqual(sections.mcqs, "mcqs after replacement")
        self.assertEqual(mcq_calls, 2)
        replace_sources.assert_called_once()

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

    def test_garbled_sourced_mcq_is_blocked_until_ocr_is_normalized(self) -> None:
        answer = (
            "### MCQ 1 **[Question Bank]**\n\n"
            "**Question (verbatim):** Whi ch of t he f ol l owi ng i s der i v ed?\n"
            "**Options (verbatim):**\n"
            "a. Opium.\n"
            "b. Marijuana.\n"
            "c. Atropine.\n"
            "d. Diazepam.\n"
            "**Source:** bank.pdf\n"
            "**Correct Answer:** b. Marijuana.\n"
            "**Clinical Explanation (Egyptian Arabic):** الإجابة ب صحيحة لأن المصدر هو نبات القنب."
        )

        errors = engine.validate_mcqs(
            engine.QueryResult(answer, source_names=("bank.pdf",)),
            engine.QuestionEvidence({2022: ["bank.pdf"]}, ["bank.pdf"]),
        )

        self.assertTrue(any("broken OCR" in error for error in errors))

    def test_garbled_correct_answer_is_blocked_for_agent_ocr_review(self) -> None:
        answer = (
            "### MCQ 1 **[Question Bank]**\n\n"
            "**Question (verbatim):** Which drug is used?\n"
            "**Options (verbatim):**\n"
            "a. Opium.\nb. Marijuana.\nc. Atropine.\nd. Diazepam.\n"
            "**Source:** bank.pdf\n"
            "**Correct Answer:** b. M a r i j u a n a.\n"
            "**Clinical Explanation (Egyptian Arabic):** الإجابة ب صحيحة ومهمة."
        )

        errors = engine.validate_mcqs(
            engine.QueryResult(answer, source_names=("bank.pdf",)),
            engine.QuestionEvidence({2022: ["bank.pdf"]}, ["bank.pdf"]),
        )

        self.assertTrue(any("broken OCR" in error for error in errors))

    def test_canonical_remote_source_alias_satisfies_question_grounding(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2025]**\n\n"
            "**Question (verbatim):** Which drug is used?\n"
            "**Options (verbatim):**\n"
            "a. A\n b. B\n c. C\n d. D\n"
            "**Source:** Final Exam 2025.pdf\n"
            "**Correct Answer:** a. A\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح عربي مهم جداً للإجابة."
        )
        catalog = [
            {
                "canonical_name": "Final Exam 2025.pdf",
                "normalized_name": engine.normalize_source_key("Final Exam 2025.pdf"),
                "aliases": ["Final Exam 2025.pdf", "Final 2025.pdf"],
                "role": "past_exam",
                "verified_years": [2025],
                "content_status": "available",
            }
        ]

        errors = engine.validate_mcqs(
            engine.QueryResult(answer, source_names=("Final Exam 2025.pdf",)),
            engine.QuestionEvidence(
                {2025: ["Final 2025.pdf"]},
                ["Final 2025.pdf"],
                evidence_catalog=catalog,
            ),
        )

        self.assertFalse(any("missing_source" in error for error in errors))

    def test_mcq_answer_text_must_match_the_selected_option(self) -> None:
        answer = (
            "### MCQ 1 **[IMP]**\n\n"
            "**Question:** Which plant is involved?\n"
            "**Options:**\n"
            "a. Opium.\n"
            "b. Marijuana.\n"
            "c. Atropine.\n"
            "d. Diazepam.\n"
            "**Correct Answer:** b. Atropine.\n"
            "**Clinical Explanation (Egyptian Arabic):** الإجابة ب هي الصحيحة في هذا السؤال."
        )

        errors = engine.validate_editorial_quality(answer)

        self.assertTrue(any("text differs" in error for error in errors))

    def test_finalizer_rejects_claimed_year_without_catalog_source_evidence(self) -> None:
        draft = (
            "# 📚 Draft\n\n"
            "---\n\n## 📖 Chronological Guide\n\n"
            + ("a" * 320)
            + "\n\n---\n\n## 🌟 IMP Points\n\n"
            + "\n".join(engine.IMP_HEADINGS)
            + "\n> [!WARNING]\n> None\n> [!CAUTION]\n> None\n\n"
            + "---\n\n## ❓ MCQs\n\n"
            + "### MCQ 1 **[Past Exams - 2025]**\n\n"
            + "**Question (verbatim):** Which drug is used?\n"
            + "**Options (verbatim):**\n"
            + "a. A\nb. B\nc. C\nd. D\n"
            + "**Source:** Final Exam 2024.pdf\n"
            + "**Correct Answer:** a. A\n"
            + "**Clinical Explanation (Egyptian Arabic):** شرح\n\n"
            + "---\n\n## ✍️ Written Questions\n\n> [!NOTE]\n> none\n\n"
            + "---\n\n## 🩺 Clinical Cases\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 1:** **[IMP]**\n"
            + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 2:** **[IMP]**\n"
            + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n"
        )
        catalog = [
            {
                "canonical_name": "Final Exam 2024.pdf",
                "normalized_name": engine.normalize_source_key("Final Exam 2024.pdf"),
                "aliases": ["Final Exam 2024.pdf"],
                "role": "past_exam",
                "verified_years": [2024],
                "content_status": "available",
            }
        ]

        with self.assertRaises(engine.ValidationError) as raised:
            engine.finalize_student_document(draft, {2024, 2025}, evidence_catalog=catalog)

        self.assertIn("source_year_mismatch", str(raised.exception))

    def test_imp_mcq_must_use_exam_shaped_options_and_short_stem(self) -> None:
        answer = (
            "### MCQ 1 **[IMP]**\n\n"
            "**Question:** Gastric lavage can be highly effective and is clinically indicated "
            "in Atropine poisoning even if several hours have passed since ingestion due to the following?\n"
            "**Options (verbatim):**\n"
            "a. Resistance to acid.\n"
            "b. Delayed gastric emptying.\n"
            "c. Rapid excretion.\n"
            "d. Protective eschar.\n"
            "**Correct Answer:** b. Delayed gastric emptying.\n"
            "**Clinical Explanation (Egyptian Arabic):** لأن حركة المعدة بتتأخر فيسمح بالغسيل."
        )

        errors = engine.validate_mcqs(
            engine.QueryResult(answer),
            engine.QuestionEvidence(
                {},
                [],
                {"mcq": {"register": "short direct factual stems", "options": {"count": 4}}},
            ),
        )

        self.assertTrue(any("wrong options field" in error for error in errors))
        self.assertTrue(any("IMP stem" in error for error in errors))

    def test_finalizer_removes_unicode_notebook_citation_residue(self) -> None:
        draft = (
            "# 📚 Draft\n\n"
            "---\n\n## 📖 Chronological Guide\n\n"
            + ("a" * 320)
            + " [34、86]\n\n---\n\n## 🌟 IMP Points\n\n"
            + "\n".join(engine.IMP_HEADINGS)
            + "\n> [!WARNING]\n> None\n> [!CAUTION]\n> None\n\n"
            + "---\n\n## ❓ MCQs\n\n> [!NOTE]\n> none\n\n"
            + "---\n\n## ✍️ Written Questions\n\n> [!NOTE]\n> none\n\n"
            + "---\n\n## 🩺 Clinical Cases\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 1:** **[IMP]**\n"
            + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n\n"
            + "> [!TIP]\n> **🩺 Clinical Case 2:** **[IMP]**\n"
            + "> **Scenario:** x\n> **Questions:** x\n> **Model Answer (Short):** x\n"
        )

        document = engine.finalize_student_document(draft, {2024})

        self.assertNotIn("[34،86]", document)
        self.assertNotIn("[34、86]", document)

    def test_source_manifest_profile_reaches_engine_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            manifest = workspace / "manifest.json"
            manifest.write_text(
                '{"exam_style_profile":{"mcq":{"options":{"count":4}}}}',
                encoding="utf-8",
            )
            args = SimpleNamespace(
                subject="Toxicology",
                emoji="🧪",
                lecture="Plant poisons.mp3",
                filename="Plant poisons 🧪.md",
                output_dir=str(workspace / "Transcripts"),
                sources_root=str(workspace),
                source_manifest=str(manifest),
                exam_style_profile=None,
                assessment_manifest=None,
                notebook_id=["notebook"],
                recording_source=None,
                pptx=None,
                audit_only=True,
                approved_upload=None,
                agent_reviewed=True,
                draft_only=False,
                finalize_draft=False,
            )
            config = {
                "notebook_ids": {"Toxicology": ["notebook"]},
                "transcripts_root": "Transcripts",
            }
            request = engine._run_request(args, config, engine._argument_parser(config))

        self.assertEqual(request.exam_style_profile["mcq"]["options"]["count"], 4)

    def test_successful_draft_cleanup_does_not_touch_other_lectures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            current_draft = Path(temporary_directory) / "current.md.draft.md"
            other_draft = Path(temporary_directory) / "other.md.draft.md"
            current_draft.write_text("draft", encoding="utf-8")
            other_draft.write_text("draft", encoding="utf-8")

            engine._delete_review_draft(str(current_draft))

            self.assertFalse(current_draft.exists())
            self.assertTrue(other_draft.exists())

    def test_successful_direct_run_cleans_stale_draft_for_same_lecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            request, context = self._checkpoint_test_context(root)
            draft_path = Path(engine._draft_output_path(request.target))
            draft_path.parent.mkdir(parents=True)
            draft_path.write_text("stale", encoding="utf-8")
            with patch.object(engine, "run_phase0_sync", return_value=context.report), patch.object(
                engine, "_phase0_request", return_value=engine.Phase0Request(
                    config={}, requested_notebook_ids=("notebook",), subject="Toxicology",
                    sources_root=str(root), lecture_name="recording.m4a",
                    recording_sources=("recording.m4a",), slides_path=None,
                )
            ), patch.object(engine, "_pipeline_context", return_value=context), patch.object(
                engine, "_run_checkpointed_phases", return_value=engine.GeneratedSections(
                    "guide", "imp", "mcqs", "written", "cases"
                )
            ), patch.object(engine, "_save_transcript"):
                engine._run_pipeline({}, request)

            self.assertFalse(draft_path.exists())

    def test_agent_ignored_prepared_source_stays_out_of_upload_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            lecture = Path(temporary_directory) / "Lecture" / "unrelated.ppt"
            lecture.parent.mkdir()
            lecture.write_bytes(b"ignored")
            preparation = engine.prepare_manifest_sources(
                temporary_directory,
                {"references": [{"path": "Lecture/unrelated.ppt", "type": "ignore"}]},
                execute=False,
            )

            sources = engine.scan_local_sources(
                temporary_directory,
                prepared_sources=preparation.by_relative_path,
            )

        self.assertEqual(sources[0].role, "ignore")


    def test_mcq_multiline_options_and_clean_fields(self) -> None:
        answer = (
            "### MCQ 1 **[Past Exams - 2022]**\n\n"
            "**Question:** Main cause of death in hanging is:\n"
            "**Options:** a. Reflex cardiac inhibition b. Asphyxia c. Cerebral anemia d. Tearing of the pons\n"
            "**Source:** Exam 2022.pdf\n"
            "**Correct Answer:** c. Cerebral anemia\n"
            "**Clinical Explanation:** بالرغم من تصنيف الشنق كنوع من أنواع الـ Violent Asphyxia إلا أن السبب الرئيسي هو Cerebral anemia."
        )
        evidence = engine.QuestionEvidence(
            {2022: ["Exam 2022.pdf"]},
            ["Exam 2022.pdf"],
            {"mcq": {"options": {"count": 4}}},
        )
        query_result = engine.QueryResult(
            answer,
            ["Exam 2022.pdf"],
        )
        errors = engine.validate_mcqs(query_result, evidence)
        self.assertEqual(errors, [])

        normalized = engine._normalize_mcq_block(answer)
        self.assertIn("**Options:**\n- **a.** Reflex cardiac inhibition\n- **b.** Asphyxia\n- **c.** Cerebral anemia\n- **d.** Tearing of the pons", normalized)
        self.assertIn("**Question:**", normalized)
        self.assertIn("**Clinical Explanation:**", normalized)

    def test_written_clean_fields_and_concise_english_model_answer(self) -> None:
        answer = (
            "### Question 1 **[Past Exams - 2022]**\n\n"
            "**Question:** Define violent asphyxia and enumerate its main types.\n"
            "**Source:** Exam 2022.pdf\n"
            "**Model Answer:**\n"
            "- **Definition:** Death resulting from mechanical interference with respiration by an external violent force.\n"
            "- **Types:**\n"
            "  1. Hanging\n"
            "  2. Strangulation\n"
            "  3. Throttling\n"
            "  4. Suffocation (Smothering / Choking)\n"
            "  5. Traumatic asphyxia\n"
            "  6. Drowning\n"
            "**Clinical Explanation:** الدكتور ركز على إن الأساس في التعريف هو الـ Mechanical interference."
        )
        evidence = engine.QuestionEvidence(
            {2022: ["Exam 2022.pdf"]},
            ["Exam 2022.pdf"],
        )
        query_result = engine.QueryResult(
            answer,
            ["Exam 2022.pdf"],
        )
        errors = engine.validate_written(query_result, evidence)
        self.assertEqual(errors, [])

        legacy_block = (
            "### Question 1 **[Past Exams - 2022]**\n\n"
            "**Question (verbatim):** Define violent asphyxia.\n"
            "**Model Answer (Short):** Death from mechanical interference.\n"
            "**Clinical Explanation (Egyptian Arabic):** شرح عربي."
        )
        normalized = engine._normalize_written_block(legacy_block)
        self.assertIn("**Question:**", normalized)
        self.assertIn("**Model Answer:**", normalized)
        self.assertIn("**Clinical Explanation:**", normalized)
        self.assertNotIn("(verbatim)", normalized)
        self.assertNotIn("(Short)", normalized)
        self.assertNotIn("(Egyptian Arabic)", normalized)

    def test_clinical_cases_standard_headings_and_clean_format(self) -> None:
        answer = (
            "### Clinical Case 1 **[Past Exams - 2022]**\n\n"
            "**Scenario:** A 19-year-old male is pulled out of fresh water canal.\n"
            "**Questions:**\n"
            "1. What is the diagnosis?\n"
            "2. Explain the mechanism of death.\n"
            "**Source:** Exam 2022.pdf\n"
            "**Model Answer:**\n"
            "1. Fresh water drowning.\n"
            "2. Hemodilution and ventricular fibrillation.\n"
            "**Clinical Explanation:** شرح الحالة بالعامية المصرية.\n\n"
            "### Clinical Case 2 **[IMP]**\n\n"
            "**Scenario:** A patient presents with acute organophosphate toxicity.\n"
            "**Questions:**\n"
            "1. Mention the antidote of choice.\n"
            "**Model Answer:**\n"
            "1. Atropine sulfate + Oximes (Pralidoxime).\n"
            "**Clinical Explanation:** الأتروبين هو الـ Antidote الأساسي."
        )
        evidence = engine.CaseEvidence(
            {2022: ["Exam 2022.pdf"]},
            ["Exam 2022.pdf"],
            ("lecture_recording.m4a",),
        )
        query_result = engine.QueryResult(
            answer,
            ["Exam 2022.pdf", "lecture_recording.m4a"],
        )
        errors = engine.validate_cases(query_result, evidence)
        self.assertEqual(errors, [])

        # Check normalization of legacy TIP callout
        legacy_tip = (
            "> [!TIP]\n"
            "> **🩺 Clinical Case 1:** **[IMP]**\n"
            "> **Scenario:** A patient with carbon monoxide poisoning.\n"
            "> **Questions:** 1. Diagnosis?\n"
            "> **Model Answer (Short):** 1. CO poisoning.\n"
            "> **Clinical Explanation (Egyptian Arabic):** شرح."
        )
        normalized_case = engine._normalize_case_block(legacy_tip)
        self.assertTrue(normalized_case.startswith("### Clinical Case 1 **[IMP]**"))
        self.assertNotIn("> [!TIP]", normalized_case)
        self.assertIn("**Model Answer:**", normalized_case)
        self.assertNotIn("(Short)", normalized_case)


if __name__ == "__main__":
    unittest.main()

