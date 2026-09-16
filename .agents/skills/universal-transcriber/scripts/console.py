#!/usr/bin/env python3
"""Console stream setup, shared by every entry point.

Essentially everything this tool prints is Egyptian Arabic, and every
transcript filename carries an emoji. On Windows ``sys.stdout`` defaults to the
ANSI code page (cp1252), which can encode neither, so the first progress line
raises ``UnicodeEncodeError`` and takes the run down before it has done
anything. POSIX is already UTF-8 and is unaffected.

This lives in its own module because the launcher deliberately does not import
the engine at module scope -- it loads it dynamically -- so there was nowhere
cheap for both of them to share it.
"""

from __future__ import annotations

import sys


def configure_console_streams() -> None:
    """Make stdout/stderr UTF-8 and line-buffered before anything is printed.

    Line buffering keeps progress visible while a long phase runs. ``errors=
    "replace"`` is the deliberate choice over the default ``strict``: a console
    that cannot render one glyph should cost that glyph, not the transcript.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not reconfigure:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (ValueError, OSError):
            # A redirected or already-detached stream may refuse
            # reconfiguration. Losing line buffering is survivable; crashing on
            # startup is not.
            pass


__all__ = ["configure_console_streams"]
