import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from module_registry import (
    ModuleConfigError,
    configured_slide,
    discover_modules,
    load_module,
    resolve_module,
)
import manage_modules as manager


LAUNCHER_PATH = SCRIPTS_DIR / "run_transcription.py"
LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "test_transcription_launcher", LAUNCHER_PATH
)
if LAUNCHER_SPEC is None or LAUNCHER_SPEC.loader is None:
    raise RuntimeError("Could not load the transcription launcher")
launcher = importlib.util.module_from_spec(LAUNCHER_SPEC)
sys.modules[LAUNCHER_SPEC.name] = launcher
LAUNCHER_SPEC.loader.exec_module(launcher)


class ModuleRegistryTests(unittest.TestCase):
    def test_different_lecture_locks_can_run_in_same_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module = SimpleNamespace(
                module_id="toxo",
                paths=SimpleNamespace(root=Path(temporary_directory)),
            )
            first = SimpleNamespace(title="Corrosives.m4a")
            second = SimpleNamespace(title="Volatile.m4a")

            with launcher._lecture_lock(module, first, None):
                with launcher._lecture_lock(module, second, None):
                    pass

    def test_same_lecture_lock_rejects_duplicate_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module = SimpleNamespace(
                module_id="toxo",
                paths=SimpleNamespace(root=Path(temporary_directory)),
            )
            recording = SimpleNamespace(title="Corrosives.m4a")

            with launcher._lecture_lock(module, recording, None):
                with self.assertRaises(launcher.LauncherError):
                    with launcher._lecture_lock(module, recording, None):
                        pass

    def test_multipart_lecture_key_uses_ordered_manifest_recordings(self) -> None:
        module = SimpleNamespace(module_id="toxo")
        recording = SimpleNamespace(title="Part 1.m4a")
        first_order = SimpleNamespace(
            title="Corrosives",
            recording_sources=("Part 1.m4a", "Part 2.m4a"),
        )
        second_order = SimpleNamespace(
            title="Corrosives",
            recording_sources=("Part 2.m4a", "Part 1.m4a"),
        )

        self.assertNotEqual(
            launcher._lecture_key(module, recording, first_order),
            launcher._lecture_key(module, recording, second_order),
        )

    def test_lecture_key_normalizes_path_separators(self) -> None:
        module = SimpleNamespace(module_id="toxo")
        recording = SimpleNamespace(title="Lecture\\Part 1.m4a")
        windows_manifest = SimpleNamespace(
            title="Corrosives", recording_sources=("Lecture\\Part 1.m4a",)
        )
        posix_manifest = SimpleNamespace(
            title="Corrosives", recording_sources=("Lecture/Part 1.m4a",)
        )

        self.assertEqual(
            launcher._lecture_key(module, recording, windows_manifest),
            launcher._lecture_key(module, recording, posix_manifest),
        )

    def test_alias_selects_only_its_module_and_resolves_configured_slide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            modules = workspace / "modules"
            for module_id, display_name, alias in (
                ("toxo", "Toxicology", "سموم"),
                ("ent", "ENT", "انف واذن"),
            ):
                module_root = modules / module_id
                lecture = module_root / "Lecture"
                lecture.mkdir(parents=True)
                (module_root / "Questions").mkdir()
                (module_root / "Transcripts").mkdir()
                (lecture / "slides.pptx").touch()
                payload = {
                    "schema_version": 1,
                    "module_id": module_id,
                    "display_name": display_name,
                    "aliases": [alias],
                    "notebook": {"id": f"{module_id}-notebook", "title": display_name},
                    "output": {"emoji": "📚"},
                    "lecture_slides": {"recording.mp3": "Lecture/slides.pptx"},
                }
                (module_root / "module.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )

            selected = resolve_module(discover_modules(workspace), "سموم")

            self.assertEqual(selected.module_id, "toxo")
            self.assertEqual(
                configured_slide(selected, "recording.mp3"),
                modules / "toxo" / "Lecture" / "slides.pptx",
            )

    def test_source_manifest_preserves_agent_exam_style_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "title": "Corrosives",
                        "recording_sources": ["corrosives.m4a"],
                        "exam_style_profile": {
                            "mcq": {"options": {"count": 4}},
                            "written": {"answer_shape": "numbered"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = launcher._source_manifest(str(manifest_path))

        self.assertEqual(manifest.exam_style_profile["mcq"]["options"]["count"], 4)

    def test_source_manifest_without_exam_style_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {"title": "Corrosives", "recording_sources": ["corrosives.m4a"]}
                ),
                encoding="utf-8",
            )

            with self.assertRaises(launcher.LauncherError):
                launcher._source_manifest(str(manifest_path))

    def test_source_manifest_accepts_object_sources_and_reference_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "title": "Corrosives",
                        "recording_sources": [
                            {"source": "Corrosive 1.m4a", "action": "use_remote"}
                        ],
                        "slides": {"path": "Lecture/slides.ppt", "action": "convert"},
                        "references": [
                            {
                                "path": "Lecture/book.pdf",
                                "type": "textbook",
                                "action": "ocr",
                                "allow_unspoken_additions": True,
                            }
                        ],
                        "exam_style_profile": {"mcq": {"options": {"count": 4}}},
                    }
                ),
                encoding="utf-8",
            )

            manifest = launcher._source_manifest(str(manifest_path))

        self.assertEqual(manifest.recording_sources, ("Corrosive 1.m4a",))
        self.assertEqual(manifest.slides, "Lecture/slides.ppt")
        self.assertEqual(manifest.references[0]["action"], "ocr")

    def test_source_manifest_accepts_multiple_assessment_years(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "title": "Corrosives",
                        "recording_sources": ["corrosives.m4a"],
                        "assessment_sources": [
                            {
                                "path": "Questions/Past Exams Collection.pdf",
                                "type": "past_exam",
                                "years": [2022, 2024, 2025],
                            }
                        ],
                        "exam_style_profile": {"mcq": {"options": {"count": 4}}},
                    }
                ),
                encoding="utf-8",
            )

            manifest = launcher._source_manifest(str(manifest_path))

        self.assertEqual(
            manifest.assessment_sources[0]["years"], [2022, 2024, 2025]
        )

    def test_source_manifest_rejects_conflicting_year_and_years(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "title": "Corrosives",
                        "recording_sources": ["corrosives.m4a"],
                        "assessment_sources": [
                            {
                                "path": "Questions/Final.pdf",
                                "type": "past_exam",
                                "year": 2025,
                                "years": [2024, 2025],
                            }
                        ],
                        "exam_style_profile": {"mcq": {"options": {"count": 4}}},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(launcher.LauncherError):
                launcher._source_manifest(str(manifest_path))

    def test_engine_command_forwards_checkpoint_recovery_flags(self) -> None:
        module = SimpleNamespace(
            display_name="Toxicology",
            emoji="🧪",
            notebook=SimpleNamespace(profile=None),
            paths=SimpleNamespace(root=Path("."), transcripts=Path("Transcripts")),
            module_id="toxo",
        )
        recording = SimpleNamespace(title="lecture.m4a")
        invocation = launcher.EngineInvocation(
            engine_path=Path("engine.py"),
            module=module,
            notebook_ids=("notebook",),
            recording=recording,
            slides_path=None,
            resume_run="run-id",
            retry_phase="written",
        )

        command = launcher._engine_command(invocation)

        self.assertIn("--resume-run", command)
        self.assertIn("run-id", command)
        self.assertEqual(command[-2:], ["--retry-phase", "written"])

    def test_engine_command_forwards_agent_recovery_response(self) -> None:
        module = SimpleNamespace(
            display_name="Toxicology",
            emoji="🧪",
            notebook=SimpleNamespace(profile=None),
            paths=SimpleNamespace(root=Path("."), transcripts=Path("Transcripts")),
            module_id="toxo",
        )
        invocation = launcher.EngineInvocation(
            engine_path=Path("engine.py"),
            module=module,
            notebook_ids=("notebook",),
            recording=SimpleNamespace(title="lecture.m4a"),
            slides_path=None,
            resume_run="run-id",
            recovery_phase="written",
            recovery_response="/cache/phase-written-response.md",
        )

        command = launcher._engine_command(invocation)

        self.assertEqual(
            command[-4:],
            [
                "--recovery-phase",
                "written",
                "--recovery-response",
                "/cache/phase-written-response.md",
            ],
        )

    def test_source_manifest_rejects_repeated_approved_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "title": "Corrosives",
                        "recording_sources": ["corrosives.m4a"],
                        "approved_uploads": ["Book.pdf", "book.pdf"],
                        "exam_style_profile": {"mcq": {"options": {"count": 4}}},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(launcher.LauncherError):
                launcher._source_manifest(str(manifest_path))

    def test_manifest_slides_skip_automatic_inventory_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_root = Path(temporary_directory)
            (module_root / "Lecture").mkdir()
            (module_root / "Questions").mkdir()
            slide_path = module_root / "Lecture" / "animal poisoning.pptx"
            slide_path.touch()
            (module_root / "Questions" / "unclassified.pdf").write_bytes(b"exam")
            module = SimpleNamespace(
                display_name="Toxicology",
                emoji="🧪",
                notebook=SimpleNamespace(profile=None),
                paths=SimpleNamespace(
                    root=module_root, transcripts=module_root / "Transcripts"
                ),
            )
            context = SimpleNamespace(
                engine_path=Path("/engine.py"),
                engine=SimpleNamespace(
                    normalize_source_key=lambda value: value.casefold(),
                    normalize_source_stem=lambda value: Path(value).stem.casefold(),
                ),
                config={},
                module=module,
                notebooks=(SimpleNamespace(notebook_uuid="notebook"),),
            )
            args = SimpleNamespace(
                slides=None, audit_only=True, draft_only=False, finalize_draft=False
            )
            recording = SimpleNamespace(
                title="Animal poisoning.m4a",
                normalized_name="animal poisoning.m4a",
                normalized_stem="animal poisoning",
            )
            manifest = launcher.SourceManifest(
                title="Animal poisoning",
                recording_sources=("Animal poisoning.m4a",),
                slides="Lecture/animal poisoning.pptx",
                approved_uploads=(),
                exam_style_profile={},
                assessment_sources=(),
            )

            with (
                patch.object(launcher, "_recordings", return_value=[recording]),
                patch.object(launcher, "_run_audit", return_value=0) as run_audit,
            ):
                result = launcher._execute_recording(args, context, recording, manifest)

        self.assertEqual(result, 0)
        self.assertIn(str(slide_path), run_audit.call_args.args[0])

    def test_module_config_accepts_multiple_notebook_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_root = Path(temporary_directory) / "modules" / "ent"
            (module_root / "Lecture").mkdir(parents=True)
            (module_root / "Questions").mkdir()
            (module_root / "Transcripts").mkdir()
            (module_root / "module.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "module_id": "ent",
                        "display_name": "ENT",
                        "notebooks": [
                            {"id": "one", "title": "ENT"},
                            {"id": "two", "title": "ENT references"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            module = load_module(module_root)

        self.assertEqual(module.notebook.ids, ("one", "two"))

    def test_new_module_without_matching_notebook_is_planned_for_creation(self) -> None:
        request = manager.CreateRequest(
            workspace=Path("/tmp/workspace"),
            modules_root=None,
            module_id="cardiology",
            display_name="Cardiology",
            aliases=(),
            notebook_ids=(),
            notebook_title=None,
            nlm_profile=None,
            emoji="📚",
            apply=False,
        )

        with patch.object(manager, "_nlm_json", return_value=[]):
            notebooks = manager._resolved_notebooks(request)

        self.assertEqual(notebooks[0].notebook_id, "<created-on-apply>")

    def test_notebook_list_wrapper_is_supported_and_explicit_order_is_preserved(self) -> None:
        request = manager.CreateRequest(
            workspace=Path("/tmp/workspace"),
            modules_root=None,
            module_id="cardiology",
            display_name="Cardiology",
            aliases=(),
            notebook_ids=("two", "one"),
            notebook_title=None,
            nlm_profile=None,
            emoji="📚",
            apply=False,
        )

        with patch.object(
            manager,
            "_nlm_json",
            return_value={
                "notebooks": [
                    {"id": "one", "title": "Cardiology"},
                    {"id": "two", "title": "Cardiology references"},
                ]
            },
        ):
            notebooks = manager._resolved_notebooks(request)

        self.assertEqual([notebook.notebook_id for notebook in notebooks], ["two", "one"])

    def test_apply_without_matching_notebook_requests_creation(self) -> None:
        request = manager.CreateRequest(
            workspace=Path("/tmp/workspace"),
            modules_root=None,
            module_id="cardiology",
            display_name="Cardiology",
            aliases=(),
            notebook_ids=(),
            notebook_title=None,
            nlm_profile=None,
            emoji="📚",
            apply=True,
        )

        with patch.object(
            manager,
            "_nlm_json",
            side_effect=[
                {"notebooks": []},
                {"notebook_id": "new-id", "title": "Cardiology"},
            ],
        ):
            notebooks = manager._resolved_notebooks(request)

        self.assertEqual(notebooks[0].notebook_id, "new-id")

    def test_module_creation_writes_canonical_directories_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            request = manager.CreateRequest(
                workspace=workspace,
                modules_root=None,
                module_id="cardiology",
                display_name="Cardiology",
                aliases=("heart",),
                notebook_ids=(),
                notebook_title=None,
                nlm_profile=None,
                emoji="❤️",
                apply=True,
            )

            with patch.object(
                manager,
                "_nlm_json",
                side_effect=[
                    {"notebooks": []},
                    {"notebook_id": "new-id", "title": "Cardiology"},
                ],
            ):
                manager._create_module(request)

            module_root = workspace / "modules" / "cardiology"
            self.assertTrue((module_root / "Lecture").is_dir())
            self.assertTrue((module_root / "Questions").is_dir())
            self.assertTrue((module_root / "Transcripts").is_dir())
            self.assertFalse((module_root / "Exams").exists())
            manifest = json.loads((module_root / "module.json").read_text())

        self.assertEqual(manifest["notebooks"], [{"id": "new-id", "title": "Cardiology"}])

    def test_legacy_exam_merge_stops_before_overwriting_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_root = Path(temporary_directory) / "modules" / "toxo"
            (module_root / "Lecture").mkdir(parents=True)
            (module_root / "Exams").mkdir()
            (module_root / "Questions").mkdir()
            (module_root / "Transcripts").mkdir()
            (module_root / "Exams" / "Final 2023.pdf").write_bytes(b"exam")
            (module_root / "Questions" / "Final 2023.pdf").write_bytes(b"existing")
            (module_root / "module.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "module_id": "toxo",
                        "display_name": "Toxicology",
                        "notebooks": [{"id": "notebook", "title": "Toxo"}],
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                workspace=temporary_directory,
                modules_root=None,
                module="toxo",
            )

            with self.assertRaises(manager.ModuleManagerError):
                manager._merge_legacy_exams(args)

            self.assertTrue((module_root / "Exams" / "Final 2023.pdf").exists())

    def test_module_discovery_rejects_missing_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_root = Path(temporary_directory) / "modules" / "ent"
            (module_root / "Lecture").mkdir(parents=True)
            (module_root / "Questions").mkdir()
            (module_root / "module.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "module_id": "ent",
                        "display_name": "ENT",
                        "notebooks": [{"id": "one", "title": "ENT"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ModuleConfigError):
                discover_modules(Path(temporary_directory))

    def test_generate_auto_manifest_matches_slides_and_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_root = Path(temporary_directory) / "modules" / "toxo"
            lecture_dir = module_root / "Lecture"
            questions_dir = module_root / "Questions"
            lecture_dir.mkdir(parents=True)
            questions_dir.mkdir(parents=True)

            (lecture_dir / "paracetamol.pdf").write_bytes(b"slide content")
            (lecture_dir / "Paracetamol audio 1.mp3").write_bytes(b"audio 1")
            (questions_dir / "End 2023.pdf").write_bytes(b"exam 2023")
            (questions_dir / "Khalsa Question Bank.pdf").write_bytes(b"qbank")

            manifest_path = launcher.generate_auto_manifest(module_root, "Paracetamol")
            self.assertTrue(manifest_path.is_file())
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest_data["title"], "Paracetamol")
            self.assertEqual(manifest_data["slides"]["path"], "Lecture/paracetamol.pdf")
            self.assertIn("Paracetamol audio 1.mp3", manifest_data["recording_sources"])
            self.assertEqual(len(manifest_data["assessment_sources"]), 2)

            exam_item = next(
                item for item in manifest_data["assessment_sources"]
                if item["path"] == "Questions/End 2023.pdf"
            )
            self.assertEqual(exam_item["type"], "past_exam")
            self.assertEqual(exam_item["year"], 2023)

            qbank_item = next(
                item for item in manifest_data["assessment_sources"]
                if item["path"] == "Questions/Khalsa Question Bank.pdf"
            )
            self.assertEqual(qbank_item["type"], "question_bank")


if __name__ == "__main__":
    unittest.main()

