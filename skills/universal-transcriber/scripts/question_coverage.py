#!/usr/bin/env python3
"""Measure how much of each exam paper a transcript actually extracted.

The assessment prompts say "Extract every relevant MCQ" from the verified
past-exam sources, but nothing measured the result.  An OPs run pulled four
MCQs out of ten exam papers and no one could say whether that was thorough or
a near-total miss.

Coverage is deliberately reported as a ratio against the *whole* paper, which
is an upper bound: extraction is scoped to one lecture's topics, so a low
ratio is a signal to look, not proof of a bug.  The gate below turns a ratio
under the floor into a warning by default; pass --block to make it an error.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from transcript_parser import parse_transcript

# A stem is "3)" / "3-" / "3." at the start of a line.
STEM_PATTERN = re.compile(r"^\s*(\d{1,2})\s*[).\-]\s*(?=\S)", re.MULTILINE)
# An option label is "a)" / "a." / "(a)" / "©" style OCR damage aside.
OPTION_PATTERN = re.compile(r"^\s*[(\[]?\s*([a-eA-E])\s*[).\]]\s*\S", re.MULTILINE)
# Exam papers open each part with a numbered banner ("1- First Question
# (multiple choice):"), which looks exactly like a stem but is structure.
SECTION_HEADER = re.compile(
    r"^\s*(?:first|second|third|fourth|fifth|sixth)?\s*question\b"
    r"|^\s*\w*\s*question\s*[(\[]",
    re.IGNORECASE,
)


# Calibrated across the twelve toxo transcripts, whose MCQ coverage against
# the whole Questions/ folder spans 3.9% to 19.6% and whose written coverage
# spans 0.4% to 4.3%. Lectures legitimately differ in how much of the exam
# corpus is theirs, so these floors sit at the bottom of the observed
# distribution: they catch an extraction that collapsed, not one that is
# merely narrow.
DEFAULT_MCQ_FLOOR = 0.05
DEFAULT_WRITTEN_FLOOR = 0.006


@dataclass
class PaperCount:
    path: Path
    mcqs: int = 0
    written: int = 0
    readable: bool = True
    reason: str = ""

    @property
    def total(self) -> int:
        return self.mcqs + self.written


@dataclass
class CoverageReport:
    papers: list[PaperCount] = field(default_factory=list)
    extracted_mcqs: int = 0
    extracted_written: int = 0
    mcq_floor: float = DEFAULT_MCQ_FLOOR
    written_floor: float = DEFAULT_WRITTEN_FLOOR

    @property
    def available_mcqs(self) -> int:
        return sum(paper.mcqs for paper in self.papers)

    @property
    def available_written(self) -> int:
        return sum(paper.written for paper in self.papers)

    @property
    def mcq_coverage(self) -> float:
        return _ratio(self.extracted_mcqs, self.available_mcqs)

    @property
    def written_coverage(self) -> float:
        return _ratio(self.extracted_written, self.available_written)

    @property
    def below_floor(self) -> list[str]:
        low: list[str] = []
        if self.available_mcqs and self.mcq_coverage < self.mcq_floor:
            low.append(
                f"MCQ coverage {self.mcq_coverage:.1%} is below the "
                f"{self.mcq_floor:.0%} floor ({self.extracted_mcqs} extracted "
                f"from {self.available_mcqs} available)"
            )
        if self.available_written and self.written_coverage < self.written_floor:
            low.append(
                f"Written coverage {self.written_coverage:.1%} is below the "
                f"{self.written_floor:.0%} floor ({self.extracted_written} "
                f"extracted from {self.available_written} available)"
            )
        return low


def _ratio(extracted: int, available: int) -> float:
    return (extracted / available) if available else 0.0


def pdf_text(path: Path) -> str:
    """Extract a PDF's text layer, preferring layout mode for exam papers."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext is not installed; run --doctor")
    completed = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[:200] or "pdftotext failed")
    return completed.stdout


def count_questions(text: str) -> tuple[int, int]:
    """Split a paper's numbered stems into MCQ and written counts.

    A stem followed by lettered options before the next stem is an MCQ;
    anything else numbered is a written item.  Exam papers restart their
    numbering per section, so stems are counted positionally rather than by
    their printed number.
    """
    stems = list(STEM_PATTERN.finditer(text))
    mcqs = 0
    written = 0
    for index, stem in enumerate(stems):
        end = stems[index + 1].start() if index + 1 < len(stems) else len(text)
        body = text[stem.end() : end]
        if SECTION_HEADER.match(body.lstrip().splitlines()[0] if body.strip() else ""):
            continue
        labels = {match.group(1).lower() for match in OPTION_PATTERN.finditer(body)}
        if len(labels) >= 3:
            mcqs += 1
        else:
            written += 1
    return mcqs, written


def count_paper(path: Path) -> PaperCount:
    try:
        text = pdf_text(path)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        return PaperCount(path, readable=False, reason=str(error))
    if not text.strip():
        return PaperCount(path, readable=False, reason="no text layer (needs OCR)")
    mcqs, written = count_questions(text)
    return PaperCount(path, mcqs=mcqs, written=written)


def count_extracted(transcript: str) -> tuple[int, int]:
    """How many MCQs and written questions a transcript actually contains.

    This used to count `### MCQ N` headings with a local regex. It now goes
    through transcript_parser, which reads the same headings but is the one
    place in the repository that knows what the format is. Blocks with a
    missing field are still counted: a malformed question is an extraction
    that happened, and hiding it here would flatter the coverage ratio.
    """
    parsed = parse_transcript(transcript)
    return len(parsed.mcqs), len(parsed.written)


def build_report(
    questions_dir: Path,
    transcript_path: Path | None,
    mcq_floor: float = DEFAULT_MCQ_FLOOR,
    written_floor: float = DEFAULT_WRITTEN_FLOOR,
) -> CoverageReport:
    report = CoverageReport(mcq_floor=mcq_floor, written_floor=written_floor)
    for path in sorted(questions_dir.glob("*.pdf")):
        report.papers.append(count_paper(path))
    if transcript_path is not None:
        transcript = transcript_path.read_text(encoding="utf-8")
        report.extracted_mcqs, report.extracted_written = count_extracted(transcript)
    return report


def render(report: CoverageReport, stream=sys.stdout) -> None:
    name_width = max(
        [len(paper.path.name) for paper in report.papers] or [10]
    )
    print(f"{'paper'.ljust(name_width)}  {'mcqs':>5}  {'written':>7}", file=stream)
    print("-" * (name_width + 16), file=stream)
    for paper in report.papers:
        if not paper.readable:
            print(
                f"{paper.path.name.ljust(name_width)}  {'?':>5}  {'?':>7}  "
                f"({paper.reason})",
                file=stream,
            )
            continue
        print(
            f"{paper.path.name.ljust(name_width)}  {paper.mcqs:>5}  "
            f"{paper.written:>7}",
            file=stream,
        )
    print("-" * (name_width + 16), file=stream)
    print(
        f"{'available'.ljust(name_width)}  {report.available_mcqs:>5}  "
        f"{report.available_written:>7}",
        file=stream,
    )
    print(
        f"{'extracted'.ljust(name_width)}  {report.extracted_mcqs:>5}  "
        f"{report.extracted_written:>7}",
        file=stream,
    )
    print(
        f"{'coverage'.ljust(name_width)}  {report.mcq_coverage:>4.0%}  "
        f"{report.written_coverage:>6.0%}",
        file=stream,
    )
    for message in report.below_floor:
        print(f"[!] {message}", file=stream)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions-dir",
        required=True,
        help="Directory of exam PDFs (usually <module>/Questions)",
    )
    parser.add_argument("--transcript", help="Transcript Markdown to measure")
    parser.add_argument(
        "--mcq-floor",
        type=float,
        default=DEFAULT_MCQ_FLOOR,
        help=f"MCQ coverage floor as a fraction (default {DEFAULT_MCQ_FLOOR})",
    )
    parser.add_argument(
        "--written-floor",
        type=float,
        default=DEFAULT_WRITTEN_FLOOR,
        help=(
            "Written coverage floor as a fraction "
            f"(default {DEFAULT_WRITTEN_FLOOR})"
        ),
    )
    parser.add_argument(
        "--block",
        action="store_true",
        help="Exit non-zero when coverage is below the floor",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead")
    args = parser.parse_args()

    questions_dir = Path(args.questions_dir).expanduser()
    if not questions_dir.is_dir():
        print(f"[Error] Not a directory: {questions_dir}", file=sys.stderr)
        return 2
    transcript_path = Path(args.transcript).expanduser() if args.transcript else None
    if transcript_path is not None and not transcript_path.is_file():
        print(f"[Error] Not a file: {transcript_path}", file=sys.stderr)
        return 2

    report = build_report(
        questions_dir, transcript_path, args.mcq_floor, args.written_floor
    )
    if args.json:
        print(
            json.dumps(
                {
                    "papers": [
                        {
                            "name": paper.path.name,
                            "mcqs": paper.mcqs,
                            "written": paper.written,
                            "readable": paper.readable,
                            "reason": paper.reason,
                        }
                        for paper in report.papers
                    ],
                    "available_mcqs": report.available_mcqs,
                    "available_written": report.available_written,
                    "extracted_mcqs": report.extracted_mcqs,
                    "extracted_written": report.extracted_written,
                    "mcq_coverage": round(report.mcq_coverage, 4),
                    "written_coverage": round(report.written_coverage, 4),
                    "below_floor": report.below_floor,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        render(report)
    if args.block and report.below_floor:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
