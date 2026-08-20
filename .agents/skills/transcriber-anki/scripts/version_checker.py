"""
version_checker.py
------------------
Manages version information and checks for newer skill releases on GitHub.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

__version__ = "1.1.0"
REPO_NAME = "3omr/universal-transcriber-skill"
GITHUB_API_URL = f"https://api.github.com/repos/{REPO_NAME}/releases/latest"
DEFAULT_CACHE_TTL = 86400  # 24 hours in seconds
DEFAULT_TIMEOUT = 1.5  # seconds


def get_current_version() -> str:
    """Return the current local version string."""
    return __version__


def parse_version(ver_str: str) -> Tuple[int, ...]:
    """Parse a semantic version string into a tuple of integers."""
    clean = ver_str.strip().lstrip("vV")
    parts = re.findall(r"\d+", clean)
    if not parts:
        return (0,)
    return tuple(map(int, parts))


def is_newer_version(latest: str, current: Optional[str] = None) -> bool:
    """Return True if latest version is strictly greater than current version."""
    if current is None:
        current = __version__
    return parse_version(latest) > parse_version(current)


def get_cache_file_path(workspace: Optional[Path] = None) -> Path:
    """Determine the cache file path for version check results."""
    if workspace:
        cache_dir = workspace / ".transcriber-cache"
        if cache_dir.exists():
            return cache_dir / "version_check.json"

    # User-level cache
    home_cache = Path.home() / ".cache" / "universal-transcriber"
    return home_cache / "version_check.json"


def fetch_latest_release_from_github(timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """Fetch the latest release tag from GitHub API."""
    req = urllib.request.Request(
        GITHUB_API_URL,
        headers={
            "User-Agent": f"universal-transcriber/{__version__}",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status == 200:
            data = json.loads(response.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            return tag.lstrip("vV") if tag else None
    return None


def check_for_updates(
    workspace: Optional[Path] = None,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    timeout: float = DEFAULT_TIMEOUT,
    force: bool = False,
) -> Optional[str]:
    """
    Check if a newer version is available.
    Returns latest version string if an update is available, else None.
    Never raises exceptions (fails silently).
    """
    # Check suppression environment variables
    if os.getenv("UNIVERSAL_TRANSCRIBER_NO_UPDATE_CHECK", "").lower() in ("1", "true", "yes") or \
       os.getenv("NO_UPDATE_NOTIFIER", "").lower() in ("1", "true", "yes"):
        return None

    cache_file = get_cache_file_path(workspace)
    now = time.time()

    # Try reading from cache
    if not force and cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            checked_at = cached.get("checked_at", 0)
            latest_ver = cached.get("latest_version")
            if now - checked_at < cache_ttl and latest_ver:
                if is_newer_version(latest_ver):
                    return latest_ver
                return None
        except Exception:
            pass

    # Query GitHub
    try:
        latest_ver = fetch_latest_release_from_github(timeout=timeout)
        if latest_ver:
            # Write to cache
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump({"latest_version": latest_ver, "checked_at": now}, f)
            except Exception:
                pass

            if is_newer_version(latest_ver):
                return latest_ver
    except Exception:
        pass

    return None


def format_update_notice(latest_version: str, current_version: Optional[str] = None) -> str:
    """Format a prominent, beautiful CLI update banner."""
    if current_version is None:
        current_version = __version__

    line1 = f" 🚀 Update available! v{current_version} → v{latest_version} "
    width = max(len(line1), len(f"    npx skills update {REPO_NAME}")) + 4

    top    = "╭" + "─" * width + "╮"
    pad1   = "│" + line1.center(width) + "│"
    sep    = "├" + "─" * width + "┤"
    pad2   = "│" + "  To update your skill:".ljust(width) + "│"
    pad3   = "│" + f"    npx skills update {REPO_NAME}".ljust(width) + "│"
    pad4   = "│" + "  Or update via Git:".ljust(width) + "│"
    pad5   = "│" + "    git pull origin main".ljust(width) + "│"
    bottom = "╰" + "─" * width + "╯"

    return f"\n{top}\n{pad1}\n{sep}\n{pad2}\n{pad3}\n{pad4}\n{pad5}\n{bottom}\n"


def print_update_notice_if_available(workspace: Optional[Path] = None, quiet: bool = False) -> None:
    """Print the update notification banner to stderr if a newer version is found."""
    if quiet:
        return
    try:
        newer_ver = check_for_updates(workspace=workspace)
        if newer_ver:
            sys.stderr.write(format_update_notice(newer_ver))
            sys.stderr.flush()
    except Exception:
        pass
