#!/usr/bin/env python3
"""
Fixes the availability date on specific episodes by exact title match —
used here for the three "LEAD Topic 1... Stephen Fogarty" episodes that
were created manually with the wrong date, to bring them in line with the
rest of the LEAD series (2026-08-03).
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"
COURSES_CHANNEL_ID = "29874"

TARGET_TITLES = [
    "LEAD Topic 1, Part 1: Authentic Leadership, Stephen Fogarty",
    "LEAD Topic 1, Part 2: Authentic Leadership, Stephen Fogarty",
    "LEAD Topic 1, Part 3: Authentic Leadership, Stephen Fogarty",
]
NEW_DATE = "2026-08-03"


def auth():
    return (CLIENT_ID, SECRET)


def find_episodes_by_title(titles):
    matches = {}
    url = f"{BASE}/channels/{COURSES_CHANNEL_ID}/episodes?per_page=100"
    while url:
        resp = requests.get(url, auth=auth(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for ep in data["data"]:
            title = ep["attributes"]["title"].strip()
            if title in titles:
                matches[title] = ep
        url = data.get("links", {}).get("next")
    return matches


def main():
    output = []

    def out(text=""):
        print(text)
        output.append(text)
        with open("fix_dates_output.log", "w") as f:
            f.write("\n".join(output))

    out(f"Looking for {len(TARGET_TITLES)} target episode(s)...")
    matches = find_episodes_by_title(TARGET_TITLES)

    for title in TARGET_TITLES:
        if title not in matches:
            out(f"NOT FOUND: {title}")
            continue

        ep = matches[title]
        episode_id = ep["id"]
        video_url = ep["attributes"].get("video_url")
        out(f"\nFound '{title}' (id={episode_id}), current published_to_library_at: "
            f"{ep['attributes'].get('published_to_library_at')}")

        patch_resp = requests.patch(
            f"{BASE}/episodes/{episode_id}",
            auth=auth(),
            json={"data": {"type": "Episode", "id": episode_id, "attributes": {
                "published_to_library_at": f"{NEW_DATE}T12:00:00+10:00"
            }}},
            timeout=30,
        )
        out(f"  PATCH episode -> {patch_resp.status_code}")

        # Also fix the live time to match, same as every other episode
        existing_times = requests.get(f"{BASE}/episodes/{episode_id}/episode_times", auth=auth(), timeout=30)
        if existing_times.ok:
            for item in existing_times.json().get("data", []):
                del_resp = requests.delete(
                    f"{BASE}/episodes/{episode_id}/episode_times/{item['id']}", auth=auth(), timeout=30
                )
                out(f"  Deleted old episode_time {item['id']} -> {del_resp.status_code}")

        time_resp = requests.post(
            f"{BASE}/episodes/{episode_id}/episode_times",
            auth=auth(),
            json={"data": {"type": "EpisodeTime", "attributes": {
                "starts_at": f"{NEW_DATE}T12:00:00+10:00",
                "video_url": video_url,
            }}},
            timeout=30,
        )
        out(f"  Created new episode_time -> {time_resp.status_code}")

    out("\nDone.")


if __name__ == "__main__":
    main()
