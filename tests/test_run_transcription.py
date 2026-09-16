"""Coverage for the launcher -- 1,300 lines that had no tests at all.

This is the only entry point users invoke: manifest parsing, the
--auto-manifest guesser, the lecture lock, and engine resolution all live
here, and so did several silent failures.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = (
    Path(__file__).parents[1] / "skills" / "universal-transcriber" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "test_launcher_module", SCRIPTS_DIR / "run_transcription.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the launcher")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def write_manifest(root: Path, payload: dict) -> str:
    path = root / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


BASE_MANIFEST = {
    "title": "OPs",
    "recording_sources": ["مبيد حشرى.m4a"],
    "slides": {"path": "Lecture/OPs.pptx", "action": "auto"},
    "assessment_sources": [
        {"path": "Questions/End 2022.pdf", "type": "past_exam", "year": 2022}
    ],
    "exam_style_profile": {
        "mcq": {"options": {"count": 4, "labels": "lowercase a. through d."}}
    },
}


class SourceManifestParsingTests(unittest.TestCase):
    def test_a_valid_manifest_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manifest = launcher._source_manifest(
                write_manifest(Path(root), BASE_MANIFEST)
            )

            self.assertEqual(manifest.title, "OPs")
            self.assertEqual(manifest.recording_sources, ("مبيد حشرى.m4a",))
            self.assertEqual(manifest.slides, "Lecture/OPs.pptx")

    def test_a_missing_manifest_names_the_path(self) -> None:
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher._source_manifest("/definitely/not/here.json")

        self.assertIn("not found", str(caught.exception))

    def test_broken_json_is_reported_not_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "manifest.json"
            path.write_text('{"title": "OPs",}', encoding="utf-8")

            with self.assertRaises(launcher.LauncherError) as caught:
                launcher._source_manifest(str(path))

            self.assertIn("not valid JSON", str(caught.exception))

    def test_an_empty_title_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            payload = {**BASE_MANIFEST, "title": "   "}

            with self.assertRaises(launcher.LauncherError):
                launcher._source_manifest(write_manifest(Path(root), payload))

    def test_a_repeated_recording_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            payload = {
                **BASE_MANIFEST,
                "recording_sources": ["a.m4a", "A.m4a"],
            }

            with self.assertRaises(launcher.LauncherError) as caught:
                launcher._source_manifest(write_manifest(Path(root), payload))

            self.assertIn("cannot repeat", str(caught.exception))

    def test_an_unsupported_slides_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            payload = {
                **BASE_MANIFEST,
                "slides": {"path": "Lecture/OPs.pptx", "action": "teleport"},
            }

            with self.assertRaises(launcher.LauncherError) as caught:
                launcher._source_manifest(write_manifest(Path(root), payload))

            self.assertIn("Unsupported slides action", str(caught.exception))

    def test_a_recording_may_be_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            payload = {
                **BASE_MANIFEST,
                "recording_sources": [{"path": "مبيد حشرى.m4a"}],
            }

            manifest = launcher._source_manifest(write_manifest(Path(root), payload))

            self.assertEqual(manifest.recording_sources, ("مبيد حشرى.m4a",))


class AutoManifestTests(unittest.TestCase):
    """--auto-manifest guessed OPs.mp3 for a lecture recorded as مبيد حشرى.m4a."""

    def _module(self, root: Path, recordings: list[str], slides: list[str]):
        (root / "Lecture").mkdir(parents=True)
        (root / "Questions").mkdir(parents=True)
        for name in (*recordings, *slides):
            (root / "Lecture" / name).write_bytes(b"x")
        (root / "Questions" / "End 2022.pdf").write_bytes(b"x")
        return root

    def test_the_only_recording_is_used_whatever_it_is_called(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module(Path(tmp), ["مبيد حشرى.m4a"], ["OPs.pptx"])

            manifest_path = launcher.generate_auto_manifest(root, "OPs")
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

            self.assertEqual(payload["recording_sources"], ["مبيد حشرى.m4a"])

    def test_a_filename_is_never_invented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module(Path(tmp), ["مبيد حشرى.m4a"], ["OPs.pptx"])

            manifest_path = launcher.generate_auto_manifest(root, "OPs")
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

            self.assertNotIn("OPs.mp3", payload["recording_sources"])

    def test_several_unmatched_recordings_list_the_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module(
                Path(tmp), ["مبيد حشرى.m4a", "محاضرة تانية.m4a"], ["OPs.pptx"]
            )

            with self.assertRaises(launcher.LauncherError) as caught:
                launcher.generate_auto_manifest(root, "OPs")

            message = str(caught.exception)
            self.assertIn("مبيد حشرى.m4a", message)
            self.assertIn("محاضرة تانية.m4a", message)

    def test_a_name_match_still_wins_over_the_single_file_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module(
                Path(tmp), ["OPs recording.m4a", "Addiction.m4a"], ["OPs.pptx"]
            )

            manifest_path = launcher.generate_auto_manifest(root, "OPs")
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

            self.assertEqual(payload["recording_sources"], ["OPs recording.m4a"])

    def test_years_in_question_filenames_become_past_exams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module(Path(tmp), ["مبيد حشرى.m4a"], ["OPs.pptx"])

            manifest_path = launcher.generate_auto_manifest(root, "OPs")
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            entry = payload["assessment_sources"][0]

            self.assertEqual(entry["type"], "past_exam")
            self.assertEqual(entry["year"], 2022)


class RemoteDiscoveryFailureTests(unittest.TestCase):
    """`except Exception: pass` hid a missing nlm and an expired auth token."""

    def _module_needing_remote(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "Lecture").mkdir(parents=True)
        (root / "Questions").mkdir(parents=True)
        (root / "Lecture" / "rec.m4a").write_bytes(b"x")
        (root / "module.json").write_text(
            json.dumps({"notebooks": [{"id": "nb-1"}]}), encoding="utf-8"
        )
        return root

    def test_a_failing_nlm_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module_needing_remote(tmp)
            with patch.object(launcher, "_warn_remote_discovery") as warn:
                with patch.object(
                    launcher.subprocess,
                    "run",
                    side_effect=FileNotFoundError("nlm not found"),
                ):
                    launcher.generate_auto_manifest(root, "OPs")

            warn.assert_called_once()
            self.assertIn("nlm not found", warn.call_args[0][0])

    def test_a_non_zero_exit_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module_needing_remote(tmp)
            completed = SimpleNamespace(
                returncode=1, stdout="", stderr="auth token expired"
            )
            with patch.object(launcher, "_warn_remote_discovery") as warn:
                with patch.object(launcher.subprocess, "run", return_value=completed):
                    launcher.generate_auto_manifest(root, "OPs")

            warn.assert_called_once()
            self.assertIn("auth token expired", warn.call_args[0][0])

    def test_the_configured_executable_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._module_needing_remote(tmp)
            completed = SimpleNamespace(returncode=0, stdout="[]", stderr="")
            with patch.object(
                launcher.subprocess, "run", return_value=completed
            ) as run:
                launcher.generate_auto_manifest(root, "OPs", "/opt/bin/nlm")

            self.assertEqual(run.call_args[0][0][0], "/opt/bin/nlm")


class EnginePathTests(unittest.TestCase):
    """rglob picked .agents/skills, the generated mirror, over the source."""

    def test_the_generated_mirror_is_never_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "skills" / "universal-transcriber" / "scripts"
            mirror = workspace / ".agents" / "skills" / "universal-transcriber" / "scripts"
            for directory in (source, mirror):
                directory.mkdir(parents=True)
                (directory / "universal_transcribe.py").write_text("", encoding="utf-8")

            with patch.object(launcher, "__file__", str(workspace / "nowhere.py")):
                resolved = launcher._engine_path(workspace)

            self.assertNotIn(".agents", resolved.parts)
            self.assertEqual(resolved, source / "universal_transcribe.py")

    def test_a_missing_engine_names_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with patch.object(launcher, "__file__", str(workspace / "nowhere.py")):
                with self.assertRaises(launcher.LauncherError) as caught:
                    launcher._engine_path(workspace)

            self.assertIn(str(workspace), str(caught.exception))


class PreflightAuditTests(unittest.TestCase):
    """--finalize-draft and --recovery-phase never upload anything."""

    def _invocation(self, **overrides):
        return SimpleNamespace(
            **{"finalize_draft": False, "recovery_phase": None, **overrides}
        )

    def test_a_normal_run_still_runs_the_audit(self) -> None:
        self.assertTrue(launcher._needs_preflight_audit(self._invocation()))

    def test_finalize_draft_skips_the_audit(self) -> None:
        self.assertFalse(
            launcher._needs_preflight_audit(self._invocation(finalize_draft=True))
        )

    def test_recovery_skips_the_audit(self) -> None:
        self.assertFalse(
            launcher._needs_preflight_audit(self._invocation(recovery_phase="mcqs"))
        )

    def test_the_audit_subprocess_is_not_spawned_for_local_work(self) -> None:
        with patch.object(launcher, "_run_audit") as audit:
            with patch.object(launcher, "_run_engine", return_value=0) as engine:
                launcher._run_transcription(
                    ["python3", "engine.py"],
                    Path("/tmp"),
                    self._invocation(finalize_draft=True),
                )

        audit.assert_not_called()
        engine.assert_called_once()


class LectureLockTests(unittest.TestCase):
    """Two runs of the same lecture must not share a run directory."""

    def _module(self, root: Path):
        return SimpleNamespace(
            module_id="toxo",
            paths=SimpleNamespace(root=root),
        )

    def test_the_same_lecture_cannot_start_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._module(Path(tmp))
            recording = SimpleNamespace(title="OPs")

            with launcher._lecture_lock(module, recording, None):
                with self.assertRaises(launcher.LauncherError) as caught:
                    with launcher._lecture_lock(module, recording, None):
                        pass

            self.assertIn("already running", str(caught.exception))

    def test_a_different_lecture_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._module(Path(tmp))

            with launcher._lecture_lock(module, SimpleNamespace(title="OPs"), None):
                with launcher._lecture_lock(
                    module, SimpleNamespace(title="Addiction"), None
                ):
                    pass

    def test_the_lock_is_released_after_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._module(Path(tmp))
            recording = SimpleNamespace(title="OPs")

            with launcher._lecture_lock(module, recording, None):
                pass
            with launcher._lecture_lock(module, recording, None):
                pass

    def test_the_key_ignores_case_and_unicode_form(self) -> None:
        module = self._module(Path("/tmp"))
        first = launcher._lecture_key(module, SimpleNamespace(title="OPs"), None)
        second = launcher._lecture_key(module, SimpleNamespace(title="ops "), None)

        self.assertEqual(first, second)


class EngineTimeoutTests(unittest.TestCase):
    def test_a_wedged_engine_is_stopped_and_explained(self) -> None:
        with patch.object(
            launcher.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="engine", timeout=1),
        ):
            exit_code = launcher._run_engine(["engine"], Path("/tmp"), 1, "Audit")

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
