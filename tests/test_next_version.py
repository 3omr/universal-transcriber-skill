"""Coverage for the automatic release-version decision.

The version reaches users through the GitHub release tag the update notifier
polls, so getting this wrong either ships nothing or ships the wrong number.
"""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "next_version.py"
SPEC = importlib.util.spec_from_file_location("test_next_version_module", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load next_version.py")
next_version = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = next_version
SPEC.loader.exec_module(next_version)

decide = next_version.decide


class TitleTests(unittest.TestCase):
    def test_feat_is_a_minor_release(self) -> None:
        decision = decide("1.4.0", title="feat: add the coverage gate")

        self.assertEqual(decision.bump, "minor")
        self.assertEqual(decision.version, "1.5.0")
        self.assertTrue(decision.releases)

    def test_fix_is_a_patch_release(self) -> None:
        self.assertEqual(decide("1.4.0", title="fix: repair the badge").version, "1.4.1")

    def test_perf_and_revert_are_patches(self) -> None:
        for title in ("perf: cache the inventory", "revert: undo the merge change"):
            with self.subTest(title=title):
                self.assertEqual(decide("1.4.0", title=title).bump, "patch")

    def test_a_scope_does_not_confuse_the_type(self) -> None:
        decision = decide("1.4.0", title="feat(engine): split the monolith")

        self.assertEqual(decision.bump, "minor")

    def test_the_bang_marks_a_breaking_change(self) -> None:
        decision = decide("1.4.0", title="feat!: drop the legacy manifest")

        self.assertEqual(decision.bump, "major")
        self.assertEqual(decision.version, "2.0.0")

    def test_a_scoped_bang_also_marks_a_breaking_change(self) -> None:
        self.assertEqual(
            decide("1.4.0", title="refactor(cli)!: rename --pptx").version, "2.0.0"
        )

    def test_housekeeping_types_cut_no_release(self) -> None:
        for title in (
            "chore: tidy the imports",
            "docs: explain config.json",
            "ci: pin the runner",
            "test: cover the launcher",
            "build: bump the wheel",
            "style: reflow the prompts",
        ):
            with self.subTest(title=title):
                decision = decide("1.4.0", title=title)

                self.assertEqual(decision.bump, "skip")
                self.assertEqual(decision.version, "1.4.0")
                self.assertFalse(decision.releases)

    def test_a_title_with_no_type_is_a_patch(self) -> None:
        decision = decide("1.4.0", title="Release 1.4.0: fix a pile of things")

        self.assertEqual(decision.bump, "patch")
        self.assertIn("no conventional-commit type", decision.reason)

    def test_an_unknown_type_is_a_patch_not_a_crash(self) -> None:
        decision = decide("1.4.0", title="wibble: something new")

        self.assertEqual(decision.bump, "patch")
        self.assertIn("not a known type", decision.reason)

    def test_the_type_is_case_insensitive(self) -> None:
        self.assertEqual(decide("1.4.0", title="FEAT: shout").bump, "minor")


class BodyTests(unittest.TestCase):
    def test_a_breaking_change_footer_forces_major(self) -> None:
        decision = decide(
            "1.4.0", title="fix: adjust the badge", body="BREAKING CHANGE: badges moved"
        )

        self.assertEqual(decision.bump, "major")

    def test_the_hyphenated_spelling_is_accepted(self) -> None:
        decision = decide(
            "1.4.0", title="fix: adjust", body="BREAKING-CHANGE: badges moved"
        )

        self.assertEqual(decision.bump, "major")

    def test_the_phrase_in_prose_does_not_count(self) -> None:
        decision = decide(
            "1.4.0",
            title="fix: adjust",
            body="This is not a breaking change for anyone.",
        )

        self.assertEqual(decision.bump, "patch")

    def test_a_breaking_body_overrides_a_housekeeping_type(self) -> None:
        decision = decide(
            "1.4.0", title="chore: tidy", body="BREAKING CHANGE: config moved"
        )

        self.assertEqual(decision.bump, "major")


class LabelTests(unittest.TestCase):
    def test_a_label_beats_the_title(self) -> None:
        decision = decide("1.4.0", labels=["release:minor"], title="chore: tidy")

        self.assertEqual(decision.bump, "minor")
        self.assertIn("label", decision.reason)

    def test_release_skip_suppresses_a_feature(self) -> None:
        decision = decide("1.4.0", labels=["release:skip"], title="feat: add a thing")

        self.assertFalse(decision.releases)
        self.assertEqual(decision.version, "1.4.0")

    def test_the_strongest_label_wins(self) -> None:
        decision = decide("1.4.0", labels=["release:patch", "release:major"])

        self.assertEqual(decision.bump, "major")

    def test_unrelated_labels_are_ignored(self) -> None:
        decision = decide("1.4.0", labels=["bug", "good first issue"], title="feat: x")

        self.assertEqual(decision.bump, "minor")

    def test_labels_are_case_insensitive(self) -> None:
        self.assertEqual(decide("1.4.0", labels=["Release:Major"]).bump, "major")


class ArithmeticTests(unittest.TestCase):
    def test_a_minor_bump_resets_the_patch(self) -> None:
        self.assertEqual(next_version.apply_bump("1.4.7", "minor"), "1.5.0")

    def test_a_major_bump_resets_minor_and_patch(self) -> None:
        self.assertEqual(next_version.apply_bump("1.4.7", "major"), "2.0.0")

    def test_zero_x_never_jumps_to_one_point_zero(self) -> None:
        # 0.x has no stable API to break; reaching 1.0.0 is a human decision.
        self.assertEqual(next_version.apply_bump("0.3.2", "major"), "0.4.0")

    def test_skip_leaves_the_version_alone(self) -> None:
        self.assertEqual(next_version.apply_bump("1.4.7", "skip"), "1.4.7")

    def test_a_leading_v_is_tolerated(self) -> None:
        self.assertEqual(next_version.apply_bump("v1.4.7", "patch"), "1.4.8")

    def test_a_malformed_version_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            next_version.apply_bump("1.4", "patch")


class CliTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_json_output_carries_every_field(self) -> None:
        result = self._run("--current", "1.4.0", "--title", "feat: x", "--json")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["version"], "1.5.0")
        self.assertEqual(payload["bump"], "minor")
        self.assertTrue(payload["releases"])
        self.assertTrue(payload["reason"])

    def test_a_bad_current_version_exits_non_zero(self) -> None:
        result = self._run("--current", "not-a-version", "--title", "feat: x")

        self.assertEqual(result.returncode, 2)
        self.assertIn("MAJOR.MINOR.PATCH", result.stderr)

    def test_repeated_label_flags_accumulate(self) -> None:
        result = self._run(
            "--current", "1.4.0",
            "--title", "chore: tidy",
            "--label", "bug",
            "--label", "release:minor",
            "--json",
        )

        self.assertEqual(json.loads(result.stdout)["bump"], "minor")


class PlanScriptTests(unittest.TestCase):
    """The workflow reads its inputs from the environment, never from argv."""

    def _plan(self, **environment: str) -> dict[str, str]:
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "ci" / "plan-version.sh")],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **environment},
        )
        return dict(
            line.split("=", 1) for line in result.stdout.strip().splitlines()
        )

    def test_the_plan_reports_the_repository_version(self) -> None:
        plan = self._plan(PR_TITLE="feat: x", PR_BODY="", PR_LABELS="[]")
        version_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

        self.assertEqual(plan["current"], version_file)

    def test_labels_arrive_as_json(self) -> None:
        plan = self._plan(
            PR_TITLE="chore: tidy", PR_BODY="", PR_LABELS='["release:major"]'
        )

        self.assertEqual(plan["bump"], "major")
        self.assertEqual(plan["releases"], "true")

    def test_a_title_containing_shell_metacharacters_is_inert(self) -> None:
        plan = self._plan(
            PR_TITLE='feat: add $(touch /tmp/pwned) `and` "quotes"',
            PR_BODY="",
            PR_LABELS="[]",
        )

        self.assertEqual(plan["bump"], "minor")
        self.assertFalse(Path("/tmp/pwned").exists())

    def test_missing_labels_are_treated_as_none(self) -> None:
        plan = self._plan(PR_TITLE="fix: y", PR_BODY="", PR_LABELS="")

        self.assertEqual(plan["bump"], "patch")


class HeadlineTests(unittest.TestCase):
    """The release page shows the title; a second prefix there reads badly."""

    def test_the_conventional_prefix_is_dropped(self) -> None:
        self.assertEqual(
            next_version.release_headline("feat: add the coverage gate"),
            "Add the coverage gate",
        )

    def test_a_scope_and_bang_are_dropped_too(self) -> None:
        self.assertEqual(
            next_version.release_headline("feat(engine)!: split the monolith"),
            "Split the monolith",
        )

    def test_a_title_without_a_prefix_is_left_alone(self) -> None:
        self.assertEqual(
            next_version.release_headline("Plain title with no prefix"),
            "Plain title with no prefix",
        )

    def test_an_already_capitalised_title_is_not_mangled(self) -> None:
        self.assertEqual(
            next_version.release_headline("fix: OCR the scanned papers"),
            "OCR the scanned papers",
        )

    def test_an_empty_title_yields_nothing_rather_than_crashing(self) -> None:
        self.assertEqual(next_version.release_headline(""), "")
        self.assertEqual(next_version.release_headline("feat:"), "feat:")

    def test_the_cli_prints_only_the_headline(self) -> None:
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--current", "1.5.0",
                "--title", "fix: repair the badge",
                "--headline",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "Repair the badge")


if __name__ == "__main__":
    unittest.main()
