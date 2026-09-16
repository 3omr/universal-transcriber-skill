import io
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


if __name__ == "__main__":
    unittest.main()
