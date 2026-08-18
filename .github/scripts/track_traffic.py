#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
import urllib.error

def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not repo:
        print("Error: GITHUB_REPOSITORY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    traffic_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "traffic")
    os.makedirs(traffic_dir, exist_ok=True)

    history_file = os.path.join(traffic_dir, "clones_history.json")
    badge_file = os.path.join(traffic_dir, "clones_badge.json")

    # Load existing history
    history_data = {"history": {}, "total_count": 0, "total_uniques": 0}
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception as e:
            print(f"Warning: Could not parse existing history file: {e}")
            history_data = {"history": {}, "total_count": 0, "total_uniques": 0}

    # Fetch from GitHub Traffic API
    if token:
        api_url = f"https://api.github.com/repos/{repo}/traffic/clones"
        req = urllib.request.Request(api_url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")

        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    clones = payload.get("clones", [])
                    print(f"Fetched {len(clones)} days of clone records from GitHub Traffic API.")

                    for item in clones:
                        timestamp = item.get("timestamp", "")
                        date_str = timestamp[:10]  # YYYY-MM-DD
                        count = item.get("count", 0)
                        uniques = item.get("uniques", 0)

                        if date_str:
                            existing = history_data["history"].get(date_str, {"count": 0, "uniques": 0})
                            history_data["history"][date_str] = {
                                "count": max(existing.get("count", 0), count),
                                "uniques": max(existing.get("uniques", 0), uniques)
                            }
                else:
                    print(f"Non-200 status code: {response.status}")
        except urllib.error.HTTPError as e:
            print(f"HTTPError fetching traffic data: {e.code} {e.reason}", file=sys.stderr)
        except Exception as e:
            print(f"Error fetching traffic data: {e}", file=sys.stderr)
    else:
        print("GITHUB_TOKEN not provided, skipping API fetch.")

    # Calculate aggregate totals
    total_count = sum(day.get("count", 0) for day in history_data["history"].values())
    total_uniques = sum(day.get("uniques", 0) for day in history_data["history"].values())

    history_data["total_count"] = total_count
    history_data["total_uniques"] = total_uniques

    # Write history
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2, ensure_ascii=False)
    print(f"Saved history: {total_count} total clones ({total_uniques} unique).")

    # Generate Shields.io endpoint badge JSON
    # Schema: https://shields.io/badges/endpoint-badge
    badge_payload = {
        "schemaVersion": 1,
        "label": "downloads",
        "message": str(total_count),
        "color": "brightgreen" if total_count > 0 else "blue"
    }

    with open(badge_file, "w", encoding="utf-8") as f:
        json.dump(badge_payload, f, indent=2, ensure_ascii=False)
    print(f"Saved badge payload: {badge_payload}")

if __name__ == "__main__":
    main()
