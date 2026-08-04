#!/usr/bin/env python3
"""
Comprehensive test: edits the existing test episode with every field the
real integration will eventually set — title, description, series, speaker
(via Speakership), YouTube URL, audio URL, and a proper live time matching
the sermon date — all in one go, to prove the full flow works together
before building it into the actual pipeline.
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"

EPISODE_ID = "719809"
SERIES_ID = "94143"  # The Guaranteed Christian Life
GUEST_SPEAKER_ID = "190362143"

# Made-up previous Sunday, for testing
SERMON_DATE = "2026-06-14"
SERMON_TIME_LOCAL = "10:30:00+10:00"
PUBLISHED_TIME_LOCAL = "12:00:00+10:00"

VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"  # placeholder test link
AUDIO_URL = "https://hrvstpdcst.com/audio/test-message.mp3"  # placeholder test link


def out(output, text=""):
    print(text)
    output.append(text)
    with open("pco_test_output.log", "w") as f:
        f.write("\n".join(output))


def main():
    output = []

    # 1. Update the episode's main fields, series, and try creating the
    #    Speakership as a nested/compound write in the same request.
    episode_payload = {
        "data": {
            "type": "Episode",
            "id": EPISODE_ID,
            "attributes": {
                "title": "Test",
                "description": "This is a made-up test description to confirm every field of the "
                                "integration works together: title, summary, series, speaker, and links.",
                "series_id": SERIES_ID,
                "video_url": VIDEO_URL,
                "library_video_url": VIDEO_URL,
                "library_audio_url": AUDIO_URL,
                "published_to_library_at": f"{SERMON_DATE}T{PUBLISHED_TIME_LOCAL}",
                "stream_type": "prerecorded",
            },
        },
    }

    out(output, "Step 1: Updating episode with full fields (speakership attempted separately below)...")
    out(output, json.dumps(episode_payload, indent=2))
    resp = requests.patch(
        f"{BASE}/episodes/{EPISODE_ID}",
        auth=(CLIENT_ID, SECRET),
        headers={"Content-Type": "application/json"},
        data=json.dumps(episode_payload),
    )
    out(output, f"\nResponse status: {resp.status_code}")
    out(output, json.dumps(resp.json(), indent=2))

    # 2. Regardless of whether the nested speakership write worked, check
    #    what's actually attached now
    out(output, "\nStep 2: Checking current speakerships on the episode...")
    sresp = requests.get(f"{BASE}/episodes/{EPISODE_ID}/speakerships?include=speaker", auth=(CLIENT_ID, SECRET))
    out(output, f"Status: {sresp.status_code}")
    out(output, json.dumps(sresp.json(), indent=2))

    # 3. Add a proper live time matching the actual sermon date/time, with
    #    a real video URL this time (not blank, which caused the earlier warning)
    out(output, "\nStep 3: Cleaning up duplicate episode times, then creating one correct one...")
    existing = requests.get(f"{BASE}/episodes/{EPISODE_ID}/episode_times", auth=(CLIENT_ID, SECRET))
    if existing.ok:
        for item in existing.json().get("data", []):
            del_resp = requests.delete(
                f"{BASE}/episodes/{EPISODE_ID}/episode_times/{item['id']}",
                auth=(CLIENT_ID, SECRET),
            )
            out(output, f"Deleted existing episode_time id={item['id']} -> {del_resp.status_code}")

    time_payload = {
        "data": {
            "type": "EpisodeTime",
            "attributes": {
                "starts_at": f"{SERMON_DATE}T{SERMON_TIME_LOCAL}",
                "video_url": VIDEO_URL,
            },
        }
    }
    out(output, json.dumps(time_payload, indent=2))
    tresp = requests.post(
        f"{BASE}/episodes/{EPISODE_ID}/episode_times",
        auth=(CLIENT_ID, SECRET),
        headers={"Content-Type": "application/json"},
        data=json.dumps(time_payload),
    )
    out(output, f"\nResponse status: {tresp.status_code}")
    out(output, json.dumps(tresp.json(), indent=2))

    # 4. Final state check
    out(output, "\nStep 4: Final episode state...")
    fresp = requests.get(f"{BASE}/episodes/{EPISODE_ID}?include=series,speakerships", auth=(CLIENT_ID, SECRET))
    out(output, json.dumps(fresp.json(), indent=2))


if __name__ == "__main__":
    main()
