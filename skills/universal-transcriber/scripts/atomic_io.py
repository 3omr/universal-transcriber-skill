#!/usr/bin/env python3
"""Writes that either land whole or do not land at all.

Every file this engine writes is one another process may be reading: a
checkpoint a resumed run will load, an inventory cache a parallel worker will
consult, a transcript the Agent is reviewing. A half-written JSON file is worse
than a missing one, because the missing one is obviously missing.

_prepare_temp comes from output_assembly, which already had to solve this for
transcripts and Index.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from output_assembly import _prepare_temp


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path = _prepare_temp(str(path), content.encode("utf-8"))
    try:
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    )


__all__ = ["_atomic_write_json", "_atomic_write_text"]
