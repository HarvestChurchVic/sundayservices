#!/usr/bin/env python3
"""Checks Planning Center's Sunday Sermons channel for duplicate episodes
among the six recently reprocessed titles, since some may have existed
there already before the reprocessing created new ones."""
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"
CHANNEL_ID = "28229"

TARGETS = [
    "final word",
    "god's love vs god's wrath",
    "throneroom of heaven",
    "the faithful",
    "goodness",
    "revelation of jesus",
]


def main():
    all_episodes = []
    url = f"{BASE}/channels/{CHANNEL_ID}/episodes?per_page=100"
    while url:
        resp = requests.get(url, auth=(CLIENT_ID, SECRET), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_episodes.extend(data["data"])
        url = data.get("links", {}).get("next")

    print(f"Total episodes in Sunday Sermons channel: {len(all_episodes)}\n")

    for target in TARGETS:
        matches = [e for e in all_episodes if target in e["attributes"]["title"].lower()]
        print(f"--- '{target}' ({len(matches)} match(es)) ---")
        for m in matches:
            print(f"  id={m['id']}  title={m['attributes']['title']!r}  "
                  f"published_to_library_at={m['attributes'].get('published_to_library_at')}  "
                  f"created_at={m['attributes'].get('created_at')}")
        print()


if __name__ == "__main__":
    main()
