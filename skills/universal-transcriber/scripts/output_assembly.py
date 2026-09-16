#!/usr/bin/env python3
"""Writing the transcript and its index to disk, atomically.

A half-written transcript or an index that lost its rows is worse than a
failed run, so the transcript and Index.md are staged as temporary files and
swapped into place together; if the second swap fails the first is rolled
back. The index write is serialised with a lock because several lectures can
be transcribed in parallel against the same Index.md.

Extracted from universal_transcribe.py, which re-exports this surface.
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from pathlib import Path

from file_lock import exclusive_file_lock
from transcriber_models import OutputTarget, TranscriberError, TranscriptIdentity


def format_markdown_tables(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    for line in lines:
        if (
            line.strip().startswith("|")
            and output
            and output[-1].strip()
            and not output[-1].strip().startswith("|")
        ):
            output.append("")
        output.append(line.rstrip())
    return "\n".join(output).strip()


def _index_row(identity: TranscriptIdentity, target: OutputTarget) -> str:
    encoded_name = urllib.parse.quote(target.file_name, safe="/")
    return (
        f"| {identity.emoji} {identity.title} | [فتح التفريغ](./{encoded_name}) | "
        "شاملة الدليل الزمني وIMP Points وMCQs والأسئلة التحريرية "
        "والحالات السريرية |\n"
    )


def _new_index(identity: TranscriptIdentity) -> str:
    return (
        f"# 📚 فهرس Transcripts محاضرات مادة ({identity.subject})\n\n"
        "| اسم المحاضرة | رابط التفريغ | الملاحظات |\n"
        "| :--- | :--- | :--- |\n"
        "---\n*تم توليد وتحديث هذا الفهرس تلقائياً عبر "
        "Universal Transcriber Engine.*\n"
    )


def _index_with_row(index_content: str, new_row: str) -> str:
    lines = index_content.splitlines(keepends=True)
    insert_at = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("---")),
        len(lines),
    )
    lines.insert(insert_at, new_row)
    return format_markdown_tables("".join(lines)) + "\n"


def render_index_content(
    identity: TranscriptIdentity, target: OutputTarget
) -> tuple[str, str]:
    index_path = os.path.join(target.transcripts_dir, "Index.md")
    new_row = _index_row(identity, target)
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as index_file:
            index_content = index_file.read()
    else:
        index_content = _new_index(identity)
    encoded_name = urllib.parse.quote(target.file_name, safe="/")
    if target.file_name in index_content or encoded_name in index_content:
        return index_path, format_markdown_tables(index_content) + "\n"
    return index_path, _index_with_row(index_content, new_row)


def _prepare_temp(path: str, content: bytes) -> str:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "wb") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
    except OSError:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    return temp_path


def _prepared_targets(targets: dict[str, bytes]) -> dict[str, str]:
    prepared_paths: dict[str, str] = {}
    try:
        for path, content in targets.items():
            prepared_paths[path] = _prepare_temp(path, content)
    except OSError:
        _remove_prepared_files(prepared_paths)
        raise
    return prepared_paths


def _remove_prepared_files(prepared_paths: dict[str, str]) -> None:
    for temp_path in prepared_paths.values():
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _restore_replaced_files(
    replaced: list[str], previous: dict[str, bytes | None]
) -> list[str]:
    restoration_errors: list[str] = []
    for path in reversed(replaced):
        try:
            old_content = previous[path]
            if old_content is None:
                if os.path.exists(path):
                    os.unlink(path)
            else:
                os.replace(_prepare_temp(path, old_content), path)
        except OSError as restoration_error:  # pragma: no cover - catastrophic I/O
            restoration_errors.append(f"{path}: {restoration_error}")
    return restoration_errors


def _existing_target_contents(targets: dict[str, bytes]) -> dict[str, bytes | None]:
    return {
        path: Path(path).read_bytes() if os.path.exists(path) else None
        for path in targets
    }


def commit_transcript_and_index(
    output_path: str, transcript: str, index_path: str, index_content: str
) -> None:
    targets = {
        output_path: transcript.encode("utf-8"),
        index_path: index_content.encode("utf-8"),
    }
    previous = _existing_target_contents(targets)
    try:
        prepared = _prepared_targets(targets)
    except OSError as error:
        raise TranscriberError(f"Atomic output preparation failed: {error}") from error
    replaced: list[str] = []
    try:
        for path in targets:
            os.replace(prepared[path], path)
            replaced.append(path)
        prepared.clear()
    except OSError as error:
        restoration_errors = _restore_replaced_files(replaced, previous)
        detail = (
            f"; restoration failed for {', '.join(restoration_errors)}"
            if restoration_errors
            else ""
        )
        raise TranscriberError(f"Atomic output commit failed: {error}{detail}") from error
    finally:
        _remove_prepared_files(prepared)


def commit_managed_transcript(
    identity: TranscriptIdentity, target: OutputTarget, transcript: str
) -> str:
    lock_path = Path(target.transcripts_dir) / ".transcriber-index.lock"
    with exclusive_file_lock(lock_path):
        index_path, index_content = render_index_content(identity, target)
        commit_transcript_and_index(
            target.output_path,
            transcript,
            index_path,
            index_content,
        )
    return index_path


def _delete_review_draft(draft_path: str) -> None:
    try:
        Path(draft_path).unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        raise TranscriberError(
            f"Final transcript committed but draft cleanup failed: {draft_path}: {error}"
        ) from error
