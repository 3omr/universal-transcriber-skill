import importlib.util
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "universal-transcriber"
    / "scripts"
    / "batch_state.py"
)
SPEC = importlib.util.spec_from_file_location("test_batch_state_script", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the batch state helper")
batch_state = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = batch_state
SPEC.loader.exec_module(batch_state)


def write_manifest(directory: Path, title: str, recordings: list[str]) -> Path:
    manifest_path = directory / f"{title.casefold().replace(' ', '-')}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "title": title,
                "recording_sources": recordings,
                "exam_style_profile": {"mcq": {"options": {"count": 4}}},
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


class BatchStateTests(unittest.TestCase):
    def test_batch_rejects_recording_owned_by_two_lectures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = write_manifest(root, "First", ["Lecture 1.m4a"])
            second = write_manifest(root, "Second", ["lecture 1.m4a"])

            with self.assertRaises(batch_state.BatchStateError):
                batch_state.create_ledger("toxo", root / "cache", (first, second))

    def test_lecture_key_preserves_multipart_order(self) -> None:
        first = batch_state.LectureUnit(
            "Corrosives", ("Part 1.m4a", "Part 2.m4a"), "/tmp/first.json"
        )
        equivalent = batch_state.LectureUnit(
            " CORROSIVES ", ("part 1.m4a", "part 2.m4a"), "/tmp/second.json"
        )
        reversed_parts = batch_state.LectureUnit(
            "Corrosives", ("Part 2.m4a", "Part 1.m4a"), "/tmp/third.json"
        )

        self.assertEqual(
            batch_state.lecture_key("TOXO", first),
            batch_state.lecture_key("toxo", equivalent),
        )
        self.assertNotEqual(
            batch_state.lecture_key("toxo", first),
            batch_state.lecture_key("toxo", reversed_parts),
        )

    def test_concurrent_updates_preserve_both_lecture_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifests = (
                write_manifest(root, "First", ["first.m4a"]),
                write_manifest(root, "Second", ["second.m4a"]),
            )
            ledger_path = batch_state.create_ledger("toxo", root / "cache", manifests)
            lecture_keys = tuple(batch_state.read_ledger(ledger_path)["lectures"])

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        batch_state.update_ledger,
                        ledger_path,
                        batch_state.LedgerUpdate(lecture_key, "queued"),
                    )
                    for lecture_key in lecture_keys
                ]
                for future in futures:
                    future.result()

            ledger = batch_state.read_ledger(ledger_path)
            self.assertEqual(
                {lecture["status"] for lecture in ledger["lectures"].values()},
                {"queued"},
            )

    def test_failed_lecture_can_requeue_without_resetting_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifests = (
                write_manifest(root, "First", ["first.m4a"]),
                write_manifest(root, "Second", ["second.m4a"]),
            )
            ledger_path = batch_state.create_ledger("toxo", root / "cache", manifests)
            lecture_key = next(iter(batch_state.read_ledger(ledger_path)["lectures"]))
            for status in ("queued", "running", "failed", "queued", "running"):
                batch_state.update_ledger(
                    ledger_path,
                    batch_state.LedgerUpdate(lecture_key, status),
                )
            batch_state.update_ledger(
                ledger_path,
                batch_state.LedgerUpdate(lecture_key, "running", agent_id="worker-2"),
            )

            lecture = batch_state.read_ledger(ledger_path)["lectures"][lecture_key]

        self.assertEqual(lecture["status"], "running")
        self.assertEqual(lecture["attempts"], 2)

    def test_invalid_transition_keeps_ledger_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifests = (
                write_manifest(root, "First", ["first.m4a"]),
                write_manifest(root, "Second", ["second.m4a"]),
            )
            ledger_path = batch_state.create_ledger("toxo", root / "cache", manifests)
            lecture_key = next(iter(batch_state.read_ledger(ledger_path)["lectures"]))

            with self.assertRaises(batch_state.BatchStateError):
                batch_state.update_ledger(
                    ledger_path,
                    batch_state.LedgerUpdate(lecture_key, "verified"),
                )

            lecture = batch_state.read_ledger(ledger_path)["lectures"][lecture_key]

        self.assertEqual(lecture["status"], "manifest_ready")


if __name__ == "__main__":
    unittest.main()
