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
import re
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


def extract_topic_number(title: str) -> str | None:
    m = re.search(r"Topic (\d+)", title)
    return m.group(1) if m else None


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


def create_episode(title: str, video_url: str, series_id: str, live: bool, published_date: str = None) -> dict:
    if not live:
        return {"dry_run": True, "title": title, "video_url": video_url, "series_id": series_id}

    attributes = {
        "title": title,
        "video_url": video_url,
        "library_video_url": video_url,
        "series_id": series_id,
        "stream_type": "prerecorded",
    }
    if published_date:
        attributes["published_to_library_at"] = f"{published_date}T12:00:00+10:00"

    payload = {
        "data": {
            "type": "Episode",
            "attributes": attributes,
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

    time_attrs = {"video_url": video_url}
    if published_date:
        time_attrs["starts_at"] = f"{published_date}T12:00:00+10:00"

    requests.post(
        f"{BASE}/episodes/{episode_id}/episode_times",
        auth=auth(),
        json={"data": {"type": "EpisodeTime", "attributes": time_attrs}},
        timeout=30,
    )

    return {"id": episode_id, "title": title, "url": episode["attributes"]["church_center_url"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Actually create episodes (default is dry-run)")
    parser.add_argument("--only-files", default=None,
                         help="Comma-separated list of filenames to process (default: all three)")
    parser.add_argument("--published-date", default=None,
                         help="Fixed date (YYYY-MM-DD) to set as published_to_library_at and live time for every episode this run. If omitted, availability is left at Planning Center's default.")
    parser.add_argument("--topic-dates", default=None,
                         help="Per-topic dates as 'topicNum:YYYY-MM-DD,topicNum:YYYY-MM-DD,...'. "
                              "Overrides --published-date for rows whose title contains a matching "
                              "'Topic N'. Rows with no matching topic number fall back to --published-date.")
    args = parser.parse_args()

    topic_dates = {}
    if args.topic_dates:
        for pair in args.topic_dates.split(","):
            topic_num, date = pair.split(":")
            topic_dates[topic_num.strip()] = date.strip()

    files_to_process = FILE_TO_SERIES
    if args.only_files:
        wanted = {f.strip() for f in args.only_files.split(",")}
        files_to_process = {k: v for k, v in FILE_TO_SERIES.items() if k in wanted}

    mode = "LIVE" if args.live else "DRY RUN (nothing will be created)"
    print(f"=== Course import — {mode} ===\n")

    existing_titles = get_existing_titles(COURSES_CHANNEL_ID)
    print(f"Found {len(existing_titles)} existing episode(s) already in the COURSES channel.\n")

    results = {"created": [], "skipped_duplicate": [], "skipped_no_topic": [], "errors": []}

    for filename, series_id in files_to_process.items():
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

            topic_num = extract_topic_number(title)

            if topic_dates and topic_num is None:
                print(f"  SKIP (no topic number, not in scope for this topic-dated run): {title}")
                results["skipped_no_topic"].append(title)
                continue

            row_date = topic_dates.get(topic_num, args.published_date)

            try:
                result = create_episode(title, link, series_id, args.live, row_date)
                print(f"  {'WOULD CREATE' if not args.live else 'CREATED'} (date={row_date}): {title}")
                results["created"].append(result)
            except Exception as e:
                print(f"  ERROR on '{title}': {e}")
                results["errors"].append({"title": title, "error": str(e)})

        print()

    print("=== Summary ===")
    print(f"Created (or would create): {len(results['created'])}")
    print(f"Skipped as duplicates: {len(results['skipped_duplicate'])}")
    print(f"Skipped (no topic number): {len(results['skipped_no_topic'])}")
    print(f"Errors: {len(results['errors'])}")

    with open("course_import_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
