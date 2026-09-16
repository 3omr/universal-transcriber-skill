#!/usr/bin/env python3
"""Cross-platform advisory file locking for the transcriber's cache files.

Every lock in this project guards a file under ``.transcriber-cache``: the
batch ledger, a prepared artifact, a notebook upload, a lecture run. They used
to call ``fcntl.flock`` directly from five separate modules, each importing
``fcntl`` at module scope -- which made the whole skill unimportable on
Windows, where that module does not exist.

The helpers below keep POSIX behavior identical (``fcntl.flock``) and fall back
to ``msvcrt.locking`` elsewhere. Both raise ``AlreadyLocked`` -- a subclass of
``BlockingIOError``, so existing ``except BlockingIOError`` handlers still
catch it -- when ``blocking=False`` and another process holds the lock.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ModuleNotFoundError:  # Windows
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False

try:  # Windows
    import msvcrt

    _HAVE_MSVCRT = True
except ModuleNotFoundError:  # POSIX
    msvcrt = None  # type: ignore[assignment]
    _HAVE_MSVCRT = False


class AlreadyLocked(BlockingIOError):
    """Raised when a non-blocking lock request finds the file already held."""


def lock_file(handle: IO[str], *, blocking: bool = True) -> None:
    """Take an exclusive advisory lock on an open file handle.

    The lock is released when the handle is closed, which is how every caller
    in this project uses it.
    """
    if _HAVE_FCNTL:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as error:
            raise AlreadyLocked(str(error)) from error
        return

    if _HAVE_MSVCRT:
        # msvcrt locks a byte range rather than the whole file, so every caller
        # must agree on the range; byte 0 of the lock file is the convention
        # here. LK_LOCK retries for ~10s before raising, LK_NBLCK fails at once.
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK  # type: ignore[attr-defined]
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), mode, 1)  # type: ignore[attr-defined]
        except OSError as error:
            raise AlreadyLocked(str(error)) from error
        return

    raise RuntimeError(
        "No file locking primitive is available on this platform "
        "(neither fcntl nor msvcrt could be imported)"
    )


@contextmanager
def exclusive_file_lock(
    lock_path: Path | str, *, blocking: bool = True
) -> Iterator[IO[str]]:
    """Open ``lock_path`` and hold an exclusive lock on it for the block.

    Yields the open handle so callers that record run metadata into the lock
    file (the lecture lock does) can write through it.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        lock_file(handle, blocking=blocking)
        yield handle


__all__ = [
    "AlreadyLocked",
    "exclusive_file_lock",
    "lock_file",
]
