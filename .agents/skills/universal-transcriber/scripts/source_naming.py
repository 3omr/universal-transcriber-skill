#!/usr/bin/env python3
"""How a source's name is folded before anything compares two of them.

A single lecture's files arrive named by whoever made them: "OPs.pptx",
"corrosivesد.سمير.pptx", "End 2021.pdf", "٢٠٢١ نهائي.pdf". Matching a local
file against a NotebookLM source title, a manifest entry against an inventory
row, or one exam paper against another all come down to folding both sides the
same way first.

Extracted from universal_transcribe because nearly forty call sites across the
engine reach for these, and a second, subtly different normalizer appearing
somewhere is exactly the class of bug that made a Windows path lookup fail
silently.
"""

from __future__ import annotations

import os
import re
import unicodedata

from exam_years import ARABIC_DIGITS


def _normalized_source_text(source_name: str) -> str:
    normalized = unicodedata.normalize("NFKC", os.path.basename(source_name or ""))
    normalized = normalized.translate(ARABIC_DIGITS).casefold().strip()
    normalized = normalized.replace("_", " ")
    normalized = re.sub(r"[^\w؀-ۿ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_source_key(source_name: str) -> str:
    """The comparable form of a file's full name, extension included."""
    return _normalized_source_text(source_name)


def normalize_source_stem(source_name: str) -> str:
    """The comparable form of a file's name without its extension."""
    return _normalized_source_text(os.path.splitext(source_name or "")[0])


def normalize_relative_source_path(source_path: str) -> str:
    """The comparable form of a path relative to the module root.

    Folds "\\" to "/" so a Windows relative path and a manifest entry written
    with forward slashes reach the same key.
    """
    normalized = unicodedata.normalize("NFKC", source_path or "")
    normalized = normalized.replace("\\", "/").casefold().strip(" ./")
    return re.sub(r"/+", "/", normalized)


__all__ = [
    "normalize_relative_source_path",
    "normalize_source_key",
    "normalize_source_stem",
]
