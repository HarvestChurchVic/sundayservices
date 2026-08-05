#!/usr/bin/env python3
"""
Diagnostic: on the real "The Foundation" episode, deletes its episode_time
(without recreating one) and re-asserts stream_type=prerecorded afterward,
to test whether creating a replacement EpisodeTime is what's resetting
stream_type and overriding the video URL.
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"
EPISODE_ID = "720662"


def auth():
    return (CLIENT_ID, SECRET)


def main():
    output = []

    def out(text=""):
        print(text)
        output.append(text)
        with open("pco_test_output.log", "w") as f:
            f.write("\n".join(output))

    out("--- Current state before change ---")
    ep = requests.get(f"{BASE}/episodes/{EPISODE_ID}", auth=auth(), timeout=30).json()["data"]
    out(f"stream_type: {ep['attributes']['stream_type']}")

    out("\n--- Deleting existing episode_time(s), NOT recreating ---")
    times = requests.get(f"{BASE}/episodes/{EPISODE_ID}/episode_times", auth=auth(), timeout=30)
    for item in times.json().get("data", []):
        d = requests.delete(f"{BASE}/episodes/{EPISODE_ID}/episode_times/{item['id']}", auth=auth(), timeout=30)
        out(f"Deleted {item['id']} -> {d.status_code}")

    out("\n--- Checking stream_type immediately after deletion (before re-asserting) ---")
    ep2 = requests.get(f"{BASE}/episodes/{EPISODE_ID}", auth=auth(), timeout=30).json()["data"]
    out(f"stream_type: {ep2['attributes']['stream_type']}")

    out("\n--- Re-asserting stream_type=prerecorded ---")
    patch = requests.patch(
        f"{BASE}/episodes/{EPISODE_ID}",
        auth=auth(),
        json={"data": {"type": "Episode", "id": EPISODE_ID, "attributes": {"stream_type": "prerecorded"}}},
        timeout=30,
    )
    out(f"PATCH -> {patch.status_code}")

    out("\n--- Final state ---")
    ep3 = requests.get(f"{BASE}/episodes/{EPISODE_ID}", auth=auth(), timeout=30).json()["data"]
    out(f"stream_type: {ep3['attributes']['stream_type']}")
    out(f"video_url: {ep3['attributes']['video_url']}")

    times2 = requests.get(f"{BASE}/episodes/{EPISODE_ID}/episode_times", auth=auth(), timeout=30)
    out(f"\nRemaining episode_times: {len(times2.json().get('data', []))}")


if __name__ == "__main__":
    main()
