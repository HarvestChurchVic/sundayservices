#!/usr/bin/env python3
"""Fixes episode 724478's published_to_library_at from the year-1015 typo
to the correct 2025-06-22, and fixes its live time to match."""
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"
EPISODE_ID = "724478"
CORRECT_DATE = "2025-06-22T12:00:00+10:00"


def auth():
    return (CLIENT_ID, SECRET)


def main():
    print("Before:")
    ep = requests.get(f"{BASE}/episodes/{EPISODE_ID}", auth=auth()).json()["data"]
    print(f"  published_to_library_at: {ep['attributes']['published_to_library_at']}")

    print("\nPatching...")
    resp = requests.patch(
        f"{BASE}/episodes/{EPISODE_ID}",
        auth=auth(),
        json={"data": {"type": "Episode", "id": EPISODE_ID,
                        "attributes": {"published_to_library_at": CORRECT_DATE, "stream_type": "prerecorded"}}},
    )
    print(f"PATCH -> {resp.status_code}")

    print("\nAfter:")
    ep2 = requests.get(f"{BASE}/episodes/{EPISODE_ID}", auth=auth()).json()["data"]
    print(f"  published_to_library_at: {ep2['attributes']['published_to_library_at']}")
    print(f"  stream_type: {ep2['attributes']['stream_type']}")


if __name__ == "__main__":
    main()
