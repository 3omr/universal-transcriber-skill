"""Coverage for the traffic tracker that runs unattended every six hours.

It commits back to the repository, so a silent break stays broken.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "track_traffic.py"
SPEC = importlib.util.spec_from_file_location("test_track_traffic_module", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load track_traffic.py")
tracker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tracker
SPEC.loader.exec_module(tracker)


class LoadHistoryTests(unittest.TestCase):
    def test_a_missing_file_starts_an_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            history = tracker.load_history(str(Path(root) / "absent.json"))

            self.assertEqual(history, tracker.empty_history())

    def test_a_valid_file_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clones_history.json"
            path.write_text(
                json.dumps({"history": {"2026-09-01": {"count": 3, "uniques": 2}}}),
                encoding="utf-8",
            )

            history = tracker.load_history(str(path))

            self.assertEqual(history["history"]["2026-09-01"]["count"], 3)

    def test_corrupt_json_does_not_lose_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clones_history.json"
            path.write_text("{not json", encoding="utf-8")

            self.assertEqual(tracker.load_history(str(path)), tracker.empty_history())

    def test_an_unexpected_shape_starts_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clones_history.json"
            path.write_text(json.dumps({"history": [1, 2, 3]}), encoding="utf-8")

            self.assertEqual(tracker.load_history(str(path)), tracker.empty_history())


class MergeClonesTests(unittest.TestCase):
    def test_new_days_are_added(self) -> None:
        merged = tracker.merge_clones(
            tracker.empty_history(),
            [{"timestamp": "2026-09-01T00:00:00Z", "count": 4, "uniques": 3}],
        )

        self.assertEqual(merged["history"]["2026-09-01"], {"count": 4, "uniques": 3})

    def test_a_day_in_progress_never_lowers_a_recorded_day(self) -> None:
        # The same day reports a lower count while it is still in progress.
        history = {"history": {"2026-09-01": {"count": 9, "uniques": 5}}}

        merged = tracker.merge_clones(
            history, [{"timestamp": "2026-09-01T00:00:00Z", "count": 2, "uniques": 1}]
        )

        self.assertEqual(merged["history"]["2026-09-01"], {"count": 9, "uniques": 5})

    def test_a_higher_later_count_wins(self) -> None:
        history = {"history": {"2026-09-01": {"count": 2, "uniques": 1}}}

        merged = tracker.merge_clones(
            history, [{"timestamp": "2026-09-01T00:00:00Z", "count": 9, "uniques": 5}]
        )

        self.assertEqual(merged["history"]["2026-09-01"], {"count": 9, "uniques": 5})

    def test_records_without_a_timestamp_are_skipped(self) -> None:
        merged = tracker.merge_clones(
            tracker.empty_history(), [{"count": 4}, "not a record", None]
        )

        self.assertEqual(merged["history"], {})

    def test_the_fourteen_day_window_does_not_erase_older_days(self) -> None:
        history = {"history": {"2026-01-01": {"count": 7, "uniques": 4}}}

        merged = tracker.merge_clones(
            history, [{"timestamp": "2026-09-01T00:00:00Z", "count": 1, "uniques": 1}]
        )

        self.assertIn("2026-01-01", merged["history"])
        self.assertEqual(len(merged["history"]), 2)


class AggregateTests(unittest.TestCase):
    def test_totals_sum_every_recorded_day(self) -> None:
        history = {
            "history": {
                "2026-09-01": {"count": 4, "uniques": 3},
                "2026-09-02": {"count": 6, "uniques": 2},
            }
        }

        aggregated = tracker.aggregate(history)

        self.assertEqual(aggregated["total_count"], 10)
        self.assertEqual(aggregated["total_uniques"], 5)

    def test_an_empty_history_totals_zero(self) -> None:
        aggregated = tracker.aggregate(tracker.empty_history())

        self.assertEqual(aggregated["total_count"], 0)


class BadgeTests(unittest.TestCase):
    def test_a_real_count_is_green(self) -> None:
        badge = tracker.badge_payload(42)

        self.assertEqual(badge["message"], "42")
        self.assertEqual(badge["color"], "brightgreen")
        self.assertEqual(badge["schemaVersion"], 1)

    def test_zero_is_blue(self) -> None:
        self.assertEqual(tracker.badge_payload(0)["color"], "blue")


class FetchTests(unittest.TestCase):
    def test_an_http_error_yields_no_records_instead_of_raising(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.github.com", 403, "Forbidden", {}, None
        )
        with patch.object(tracker.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(tracker.fetch_clones("o/r", "token"), [])

    def test_a_network_error_yields_no_records(self) -> None:
        with patch.object(
            tracker.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("no route"),
        ):
            self.assertEqual(tracker.fetch_clones("o/r", "token"), [])


class MainTests(unittest.TestCase):
    def test_a_missing_repository_variable_fails_loudly(self) -> None:
        with patch.dict(tracker.os.environ, {}, clear=True):
            self.assertEqual(tracker.main(), 1)


if __name__ == "__main__":
    unittest.main()
