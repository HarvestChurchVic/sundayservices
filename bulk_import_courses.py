#!/usr/bin/env python3
"""
Bulk-imports course episodes (LEAD, DISCIPLE, DISCIPLE 2.0) from spreadsheets
into Planning Center Publishing. Completely separate from the main sermon
pipeline — this never touches feed_items.json, R2, or the podcast feed.

Each spreadsheet needs columns: #, Title, Link (a YouTube URL).

Safe to re-run: checks existing episode titles in the COURSES channel first
and skips anything already imported, so running it twice never creates
duplicates.

Defaults to a DRY RUN (prints what it would do, creates nothing). Pass
--live to actually create episodes in Planning Center.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"

COURSES_CHANNEL_ID = "29874"

# Maps each spreadsheet filename to its existing Planning Center series ID
FILE_TO_SERIES = {
    "LEAD_Playlist.xlsx": "95156",       # LEAD
    "Disciple_Playlist.xlsx": "95059",   # DISCIPLE
    "Disciple_2_0_Playlist.xlsx": "95060",  # DISCIPLE 2.0
}


def auth():
    return (CLIENT_ID, SECRET)


def get_existing_titles(channel_id: str) -> set:
    """Fetches every existing episode title in the channel, so we never
    create the same course episode twice."""
    titles = set()
    url = f"{BASE}/channels/{channel_id}/episodes?per_page=100"
    while url:
        resp = requests.get(url, auth=auth(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for ep in data["data"]:
            titles.add(ep["attributes"]["title"].strip())
        url = data.get("links", {}).get("next")
    return titles


def create_episode(title: str, video_url: str, series_id: str, live: bool) -> dict:
    if not live:
        return {"dry_run": True, "title": title, "video_url": video_url, "series_id": series_id}

    payload = {
        "data": {
            "type": "Episode",
            "attributes": {
                "title": title,
                "video_url": video_url,
                "library_video_url": video_url,
                "series_id": series_id,
                "stream_type": "prerecorded",
            },
            "relationships": {
                "channel": {"data": {"type": "Channel", "id": COURSES_CHANNEL_ID}}
            },
        }
    }
    resp = requests.post(f"{BASE}/episodes", auth=auth(), json=payload, timeout=30)
    resp.raise_for_status()
    episode = resp.json()["data"]
    episode_id = episode["id"]

    # Same fix as the sermon pipeline: replace the auto-assigned default
    # live time with one that actually has this video attached
    existing_times = requests.get(f"{BASE}/episodes/{episode_id}/episode_times", auth=auth(), timeout=30)
    if existing_times.ok:
        for item in existing_times.json().get("data", []):
            requests.delete(f"{BASE}/episodes/{episode_id}/episode_times/{item['id']}", auth=auth(), timeout=30)

    requests.post(
        f"{BASE}/episodes/{episode_id}/episode_times",
        auth=auth(),
        json={"data": {"type": "EpisodeTime", "attributes": {"video_url": video_url}}},
        timeout=30,
    )

    return {"id": episode_id, "title": title, "url": episode["attributes"]["church_center_url"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Actually create episodes (default is dry-run)")
    args = parser.parse_args()

    mode = "LIVE" if args.live else "DRY RUN (nothing will be created)"
    print(f"=== Course import — {mode} ===\n")

    existing_titles = get_existing_titles(COURSES_CHANNEL_ID)
    print(f"Found {len(existing_titles)} existing episode(s) already in the COURSES channel.\n")

    results = {"created": [], "skipped_duplicate": [], "errors": []}

    for filename, series_id in FILE_TO_SERIES.items():
        path = Path("course-imports") / filename
        if not path.exists():
            print(f"Skipping {filename} — file not found.")
            continue

        df = pd.read_excel(path)
        print(f"--- {filename} ({len(df)} rows) -> series {series_id} ---")

        for _, row in df.iterrows():
            title = str(row["Title"]).strip()
            link = str(row["Link"]).strip()

            if title in existing_titles:
                print(f"  SKIP (already exists): {title}")
                results["skipped_duplicate"].append(title)
                continue

            try:
                result = create_episode(title, link, series_id, args.live)
                print(f"  {'WOULD CREATE' if not args.live else 'CREATED'}: {title}")
                results["created"].append(result)
            except Exception as e:
                print(f"  ERROR on '{title}': {e}")
                results["errors"].append({"title": title, "error": str(e)})

        print()

    print("=== Summary ===")
    print(f"Created (or would create): {len(results['created'])}")
    print(f"Skipped as duplicates: {len(results['skipped_duplicate'])}")
    print(f"Errors: {len(results['errors'])}")

    with open("course_import_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
