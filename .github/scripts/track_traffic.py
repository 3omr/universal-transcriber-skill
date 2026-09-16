#!/usr/bin/env python3
"""Record the repository's clone traffic and render a Shields endpoint badge.

GitHub's traffic API only keeps fourteen days, so this runs on a schedule and
merges each fetch into a history file that is committed back. The merge takes
the per-day maximum rather than the newest value, because a day that is still
in progress reports a lower count than the same day did on a later fetch.

The logic is split into small functions so it can be tested without the
network or the GitHub Actions environment.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def empty_history() -> dict[str, Any]:
    return {"history": {}, "total_count": 0, "total_uniques": 0}


def load_history(history_file: str) -> dict[str, Any]:
    """Read the committed history, falling back to an empty one."""
    if not os.path.exists(history_file):
        return empty_history()
    try:
        with open(history_file, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Warning: Could not parse existing history file: {error}")
        return empty_history()
    if not isinstance(loaded, dict) or not isinstance(loaded.get("history"), dict):
        print("Warning: history file has an unexpected shape; starting fresh.")
        return empty_history()
    return {**empty_history(), **loaded}


def merge_clones(history_data: dict[str, Any], clones: list[Any]) -> dict[str, Any]:
    """Fold an API response into the history, keeping the per-day maximum."""
    history = dict(history_data.get("history") or {})
    for item in clones:
        if not isinstance(item, dict):
            continue
        date_str = str(item.get("timestamp", ""))[:10]
        if not date_str:
            continue
        existing = history.get(date_str) or {}
        history[date_str] = {
            "count": max(
                int(existing.get("count", 0) or 0), int(item.get("count", 0) or 0)
            ),
            "uniques": max(
                int(existing.get("uniques", 0) or 0), int(item.get("uniques", 0) or 0)
            ),
        }
    return {**history_data, "history": history}


def aggregate(history_data: dict[str, Any]) -> dict[str, Any]:
    days = (history_data.get("history") or {}).values()
    return {
        **history_data,
        "total_count": sum(int(day.get("count", 0) or 0) for day in days),
        "total_uniques": sum(int(day.get("uniques", 0) or 0) for day in days),
    }


def badge_payload(total_count: int) -> dict[str, Any]:
    # Schema: https://shields.io/badges/endpoint-badge
    return {
        "schemaVersion": 1,
        "label": "downloads",
        "message": str(total_count),
        "color": "brightgreen" if total_count > 0 else "blue",
    }


def fetch_clones(repo: str, token: str) -> list[Any]:
    """Fetch the clone records, returning an empty list on any failure."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/traffic/clones"
    )
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(request) as response:
            if response.status != 200:
                print(f"Non-200 status code: {response.status}")
                return []
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(
            f"HTTPError fetching traffic data: {error.code} {error.reason}",
            file=sys.stderr,
        )
        return []
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        print(f"Error fetching traffic data: {error}", file=sys.stderr)
        return []
    clones = payload.get("clones", []) if isinstance(payload, dict) else []
    print(f"Fetched {len(clones)} days of clone records from GitHub Traffic API.")
    return clones if isinstance(clones, list) else []


def write_json(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print(
            "Error: GITHUB_REPOSITORY environment variable is not set.",
            file=sys.stderr,
        )
        return 1

    traffic_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "traffic"
    )
    os.makedirs(traffic_dir, exist_ok=True)
    history_file = os.path.join(traffic_dir, "clones_history.json")
    badge_file = os.path.join(traffic_dir, "clones_badge.json")

    history_data = load_history(history_file)
    if token:
        history_data = merge_clones(history_data, fetch_clones(repo, token))
    else:
        print("GITHUB_TOKEN not provided, skipping API fetch.")
    history_data = aggregate(history_data)

    write_json(history_file, history_data)
    print(
        f"Saved history: {history_data['total_count']} total clones "
        f"({history_data['total_uniques']} unique)."
    )
    badge = badge_payload(history_data["total_count"])
    write_json(badge_file, badge)
    print(f"Saved badge payload: {badge}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
