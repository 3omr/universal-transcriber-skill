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


if __name__ == "__main__":
    unittest.main()
