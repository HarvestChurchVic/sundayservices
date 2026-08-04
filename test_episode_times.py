#!/usr/bin/env python3
"""
Inspects the EpisodeTime records attached to the test episode (the "Live
times" entries shown in the interface), and deletes them, since the goal
is a backdated episode with no future "live" time attached.
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"

EPISODE_ID = "719809"


def main():
    output = []

    def out(text=""):
        print(text)
        output.append(text)

    resp = requests.get(f"{BASE}/episodes/{EPISODE_ID}/episode_times", auth=(CLIENT_ID, SECRET))
    out(f"GET episode_times -> {resp.status_code}")
    out(json.dumps(resp.json(), indent=2))

    if resp.ok:
        for item in resp.json().get("data", []):
            time_id = item["id"]
            out(f"\nDeleting episode_time id={time_id}...")
            del_resp = requests.delete(
                f"{BASE}/episodes/{EPISODE_ID}/episode_times/{time_id}",
                auth=(CLIENT_ID, SECRET),
            )
            out(f"DELETE -> {del_resp.status_code}")

    out("\n--- Re-checking episode state after deletion ---")
    resp2 = requests.get(f"{BASE}/episodes/{EPISODE_ID}", auth=(CLIENT_ID, SECRET))
    if resp2.ok:
        attrs = resp2.json()["data"]["attributes"]
        out(f"published_live_at is now: {attrs.get('published_live_at')}")
        out(f"published_to_library_at is now: {attrs.get('published_to_library_at')}")

    with open("pco_test_output.log", "w") as f:
        f.write("\n".join(output))


if __name__ == "__main__":
    main()
