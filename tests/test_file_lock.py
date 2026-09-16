import sys
import tempfile
import unittest
from multiprocessing import Process, Queue
from pathlib import Path

SCRIPTS_DIR = (
    Path(__file__).parents[1]
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from file_lock import AlreadyLocked, exclusive_file_lock


def _try_lock_in_child(lock_path: str, result: Queue) -> None:
    try:
        with exclusive_file_lock(lock_path, blocking=False):
            result.put("acquired")
    except AlreadyLocked:
        result.put("already_locked")


class TestFileLock(unittest.TestCase):
    def test_lock_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "locks" / "nested" / "a.lock"
            with exclusive_file_lock(lock_path) as handle:
                handle.write("payload")
            self.assertTrue(lock_path.is_file())

    def test_lock_is_released_after_the_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "a.lock"
            with exclusive_file_lock(lock_path):
                pass
            with exclusive_file_lock(lock_path, blocking=False):
                pass  # reacquiring must not raise

    def test_non_blocking_lock_rejects_a_second_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "a.lock"
            with exclusive_file_lock(lock_path):
                result: Queue = Queue()
                child = Process(
                    target=_try_lock_in_child, args=(str(lock_path), result)
                )
                child.start()
                child.join(timeout=30)
                self.assertEqual(result.get(timeout=5), "already_locked")

    def test_already_locked_is_a_blocking_io_error(self) -> None:
        # Existing call sites catch BlockingIOError; keep that contract.
        self.assertTrue(issubclass(AlreadyLocked, BlockingIOError))


if __name__ == "__main__":
    unittest.main()
