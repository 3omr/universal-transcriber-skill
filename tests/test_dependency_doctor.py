import io
import subprocess
import sys
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

import dependency_doctor


class TestDependencyDoctor(unittest.TestCase):
    def test_all_present_reports_success(self) -> None:
        with patch.object(dependency_doctor.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(
                 dependency_doctor.importlib.util, "find_spec", return_value=object()
             ):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out), 0)
        self.assertIn("All required tooling is present", out.getvalue())

    def test_missing_required_tool_fails_with_an_install_hint(self) -> None:
        def only_poppler(name: str):
            return "/usr/bin/" + name if name in {"pdftotext", "pdfinfo"} else None

        with patch.object(dependency_doctor.shutil, "which", side_effect=only_poppler):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out), 1)

        text = out.getvalue()
        self.assertIn("Missing required tooling", text)
        self.assertIn("nlm", text)
        self.assertIn("https://github.com/tmc/nlm", text)

    def test_missing_optional_tool_does_not_fail(self) -> None:
        def no_ffmpeg(name: str):
            return None if name == "ffmpeg" else "/usr/bin/" + name

        with patch.object(dependency_doctor.shutil, "which", side_effect=no_ffmpeg), \
             patch.object(
                 dependency_doctor.importlib.util, "find_spec", return_value=object()
             ):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out), 0)

        text = out.getvalue()
        self.assertIn("Optional tooling not installed", text)
        self.assertIn("ffmpeg", text)

    def test_every_dependency_declares_a_purpose_and_install_hint(self) -> None:
        for dependency in dependency_doctor.DEPENDENCIES:
            self.assertTrue(dependency.purpose, dependency.name)
            self.assertTrue(dependency.install_hint, dependency.name)
            self.assertTrue(
                dependency.executables or dependency.python_module,
                f"{dependency.name} has nothing to look for",
            )

    def test_every_executable_dependency_declares_a_probe(self) -> None:
        # A tool with no probe can only ever be checked for presence, which is
        # the weakness --doctor-live exists to fix. Python packages are probed
        # by importing them, so they need no argv.
        for dependency in dependency_doctor.DEPENDENCIES:
            if dependency.python_module:
                continue
            self.assertIsNotNone(
                dependency.probe,
                f"{dependency.name} cannot be liveness-checked",
            )


class TestLivenessProbes(unittest.TestCase):
    def test_probes_do_not_run_unless_live_is_requested(self) -> None:
        def fail_loudly(*_args: object, **_kwargs: object):
            raise AssertionError("the default report must not shell out")

        with patch.object(dependency_doctor.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(
                 dependency_doctor.importlib.util, "find_spec", return_value=object()
             ), \
             patch.object(dependency_doctor.subprocess, "run", side_effect=fail_loudly):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out), 0)

        self.assertIn("--doctor-live", out.getvalue())

    def test_a_healthy_probe_passes_and_says_the_tooling_works(self) -> None:
        def succeed(command, **_kwargs: object):
            return subprocess.CompletedProcess(command, 0, "ok", "")

        with patch.object(dependency_doctor.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(
                 dependency_doctor.importlib, "import_module", return_value=object()
             ), \
             patch.object(dependency_doctor.subprocess, "run", side_effect=succeed):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out, live=True), 0)

        self.assertIn("present and working", out.getvalue())

    def test_an_installed_but_unauthenticated_nlm_fails_the_check(self) -> None:
        # The whole point of --doctor-live: nlm is on PATH, so the presence
        # check is green, but it cannot reach NotebookLM.
        def fail_only_nlm(command, **_kwargs: object):
            if command[0].endswith("nlm"):
                return subprocess.CompletedProcess(command, 1, "", "not authenticated")
            return subprocess.CompletedProcess(command, 0, "ok", "")

        def which(name: str):
            return "/usr/bin/" + name

        with patch.object(dependency_doctor.shutil, "which", side_effect=which), \
             patch.object(
                 dependency_doctor.importlib, "import_module", return_value=object()
             ), \
             patch.object(dependency_doctor.subprocess, "run", side_effect=fail_only_nlm):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out, live=True), 1)

        text = out.getvalue()
        self.assertIn("installed but not working", text)
        self.assertIn("not authenticated", text)
        self.assertIn("nlm auth", text)

    def test_the_nlm_probe_is_the_call_the_engine_actually_makes(self) -> None:
        commands: list[list[str]] = []

        def capture(command, **_kwargs: object):
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with patch.object(dependency_doctor.shutil, "which", side_effect=lambda n: "/usr/bin/" + n), \
             patch.object(
                 dependency_doctor.importlib, "import_module", return_value=object()
             ), \
             patch.object(dependency_doctor.subprocess, "run", side_effect=capture):
            dependency_doctor.report(io.StringIO(), live=True)

        self.assertIn(["/usr/bin/nlm", "notebook", "list"], commands)

    def test_a_probe_that_times_out_is_reported_rather_than_hanging_the_check(self) -> None:
        def time_out(command, **_kwargs: object):
            raise subprocess.TimeoutExpired(command, 45)

        with patch.object(dependency_doctor.shutil, "which", return_value="/usr/bin/nlm"), \
             patch.object(
                 dependency_doctor.importlib, "import_module", return_value=object()
             ), \
             patch.object(dependency_doctor.subprocess, "run", side_effect=time_out):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out, live=True), 1)

        self.assertIn("timed out", out.getvalue())

    def test_a_python_package_that_is_present_but_will_not_import_is_unhealthy(self) -> None:
        def explode(name: str):
            raise ImportError(f"{name} has a broken native extension")

        with patch.object(dependency_doctor.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(dependency_doctor.importlib.util, "find_spec", return_value=object()), \
             patch.object(dependency_doctor.importlib, "import_module", side_effect=explode), \
             patch.object(
                 dependency_doctor.subprocess,
                 "run",
                 side_effect=lambda c, **k: subprocess.CompletedProcess(c, 0, "", ""),
             ):
            out = io.StringIO()
            # genanki and reportlab are optional, so a broken import warns
            # loudly without failing the run.
            self.assertEqual(dependency_doctor.report(out, live=True), 0)

        text = out.getvalue()
        self.assertIn("Optional tooling installed but not working", text)
        self.assertIn("broken native extension", text)

    def test_an_unsupported_python_version_fails_the_check(self) -> None:
        with patch.object(dependency_doctor.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(
                 dependency_doctor.importlib.util, "find_spec", return_value=object()
             ), \
             patch.object(dependency_doctor.sys, "version_info", (3, 9, 18)):
            out = io.StringIO()
            self.assertEqual(dependency_doctor.report(out), 1)

        self.assertIn("3.10+ is required", out.getvalue())


if __name__ == "__main__":
    unittest.main()
