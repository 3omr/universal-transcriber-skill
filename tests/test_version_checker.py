"""
test_version_checker.py
-----------------------
Unit and integration tests for the version checker and update notification system.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills" / "universal-transcriber" / "scripts"
if str(SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(SKILLS_DIR))

import version_checker


class TestVersionChecker(unittest.TestCase):
    def test_version_string_and_parse(self):
        self.assertEqual(version_checker.get_current_version(), "1.2.0")
        self.assertEqual(version_checker.parse_version("1.0.0"), (1, 0, 0))
        self.assertEqual(version_checker.parse_version("v1.2.0"), (1, 2, 0))
        self.assertEqual(version_checker.parse_version("V2.10.3"), (2, 10, 3))
        self.assertEqual(version_checker.parse_version("invalid"), (0,))

    def test_is_newer_version(self):
        self.assertTrue(version_checker.is_newer_version("1.3.0", "1.2.0"))
        self.assertTrue(version_checker.is_newer_version("2.0.0", "1.9.9"))
        self.assertTrue(version_checker.is_newer_version("1.2.1", "1.2.0"))
        self.assertFalse(version_checker.is_newer_version("1.2.0", "1.2.0"))
        self.assertFalse(version_checker.is_newer_version("1.1.0", "1.2.0"))
        self.assertFalse(version_checker.is_newer_version("0.9.0", "1.2.0"))

    def test_format_update_notice(self):
        notice = version_checker.format_update_notice("1.2.0", "1.1.0")
        self.assertIn("v1.1.0 → v1.2.0", notice)
        self.assertIn("npx skills update 3omr/universal-transcriber-skill", notice)
        self.assertIn("git pull origin main", notice)
        self.assertIn("╭", notice)
        self.assertIn("╰", notice)

    def test_check_for_updates_with_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            cache_dir = workspace / ".transcriber-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / "version_check.json"
            
            # Write a valid cache with newer version
            cache_file.write_text(
                json.dumps({"latest_version": "1.3.0", "checked_at": time.time()}),
                encoding="utf-8",
            )

            result = version_checker.check_for_updates(workspace=workspace, cache_ttl=3600)
            self.assertEqual(result, "1.3.0")

            # Write cache with same version
            cache_file.write_text(
                json.dumps({"latest_version": "1.2.0", "checked_at": time.time()}),
                encoding="utf-8",
            )
            result_same = version_checker.check_for_updates(workspace=workspace, cache_ttl=3600)
            self.assertIsNone(result_same)

    @patch("version_checker.fetch_latest_release_from_github")
    def test_check_for_updates_network_fetch_and_cache(self, mock_fetch):
        mock_fetch.return_value = "1.3.0"
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            cache_dir = workspace / ".transcriber-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            result = version_checker.check_for_updates(workspace=workspace, force=True)
            self.assertEqual(result, "1.3.0")
            
            cache_file = cache_dir / "version_check.json"
            self.assertTrue(cache_file.exists())
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(cached_data["latest_version"], "1.3.0")

    @patch.dict(os.environ, {"UNIVERSAL_TRANSCRIBER_NO_UPDATE_CHECK": "1"})
    def test_suppression_via_env_var(self):
        with patch("version_checker.fetch_latest_release_from_github") as mock_fetch:
            mock_fetch.return_value = "2.0.0"
            result = version_checker.check_for_updates()
            self.assertIsNone(result)
            mock_fetch.assert_not_called()

    @patch.dict(os.environ, {"NO_UPDATE_NOTIFIER": "true"})
    def test_suppression_via_no_update_notifier(self):
        with patch("version_checker.fetch_latest_release_from_github") as mock_fetch:
            mock_fetch.return_value = "2.0.0"
            result = version_checker.check_for_updates()
            self.assertIsNone(result)
            mock_fetch.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_network_failure_fails_silently(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection timed out")
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            result = version_checker.check_for_updates(workspace=workspace, force=True)
            self.assertIsNone(result)

    def test_cli_version_flags(self):
        # Universal Transcriber CLI --version
        res_ut = subprocess.run(
            [sys.executable, str(REPO_ROOT / "skills" / "universal-transcriber" / "scripts" / "run_transcription.py"), "--version"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_ut.returncode, 0)
        self.assertIn("1.2.0", res_ut.stdout + res_ut.stderr)

        # Transcriber Anki CLI --version
        res_anki = subprocess.run(
            [sys.executable, str(REPO_ROOT / "skills" / "transcriber-anki" / "scripts" / "run_anki_export.py"), "--version"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_anki.returncode, 0)
        self.assertIn("1.2.0", res_anki.stdout + res_anki.stderr)


if __name__ == "__main__":
    unittest.main()
