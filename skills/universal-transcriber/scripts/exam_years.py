#!/usr/bin/env python3
"""Reading exam years out of source text and filenames.

A leaf module with no transcriber imports, so anything in the engine can use
it. Exam years are provenance: a badge claiming **[Past Exams - 2022]** is
only honest if 2022 came from the paper itself, which is why these are the
one place year parsing happens.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

MIN_REASONABLE_EXAM_YEAR = 2000
# Eastern Arabic and Persian digits appear in scanned Egyptian exam papers and
# in filenames typed on an Arabic keyboard.
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def maximum_reasonable_exam_year() -> int:
    """Next year, so a paper for the upcoming exam is still accepted."""
    return date.today().year + 1


def is_reasonable_exam_year(year: int) -> bool:
    return MIN_REASONABLE_EXAM_YEAR <= year <= maximum_reasonable_exam_year()


def extract_exam_years(source_text: str) -> tuple[int, ...]:
    normalized = unicodedata.normalize("NFKC", source_text or "").translate(ARABIC_DIGITS)
    years = {int(year) for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", normalized)}
    return tuple(sorted(year for year in years if is_reasonable_exam_year(year)))


def extract_filename_exam_years(file_name: str) -> tuple[int, ...]:
    """Also accept the two-digit form ("Final 21.pdf") that filenames use."""
    normalized = unicodedata.normalize("NFKC", file_name or "").translate(ARABIC_DIGITS)
    years = set(extract_exam_years(normalized))
    for short_year in re.findall(r"(?<!\d)(2\d)(?!\d)", normalized):
        expanded = 2000 + int(short_year)
        if is_reasonable_exam_year(expanded):
            years.add(expanded)
    return tuple(sorted(years))
