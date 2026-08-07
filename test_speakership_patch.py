#!/usr/bin/env python3
"""
Proves the auto-assigned Speakership can be updated to a different speaker
via PATCH, using the real speakership already sitting on "The Foundation"
(id 175426, episode 720662). Switches it to Ps Keith Ainge, confirms the
change stuck, then sets it to the correct speaker for this sermon
(Ps Andrew Cartledge).
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"
EPISODE_ID = "720662"
SPEAKERSHIP_ID = "175426"

KEITH_AINGE_ID = "185233127"
ANDREW_CARTLEDGE_ID = "180515070"


def auth():
    return (CLIENT_ID, SECRET)


def set_speaker(speaker_id):
    return requests.patch(
        f"{BASE}/episodes/{EPISODE_ID}/speakerships/{SPEAKERSHIP_ID}",
        auth=auth(),
        json={"data": {"type": "Speakership", "id": SPEAKERSHIP_ID, "attributes": {},
                        "relationships": {"speaker": {"data": {"type": "Speaker", "id": speaker_id}}}}},
        timeout=30,
    )


def get_current_speaker():
    resp = requests.get(f"{BASE}/episodes/{EPISODE_ID}/speakerships/{SPEAKERSHIP_ID}?include=speaker", auth=auth(), timeout=30)
    return resp.json()


def main():
    output = []

    def out(text=""):
        print(text)
        output.append(text)
        with open("pco_test_output.log", "w") as f:
            f.write("\n".join(output))

    out("--- Before change ---")
    out(json.dumps(get_current_speaker(), indent=2)[:500])

    out("\n--- Switching to Keith Ainge (proving the PATCH genuinely works) ---")
    r1 = set_speaker(KEITH_AINGE_ID)
    out(f"PATCH -> {r1.status_code}")
    out(f"Response body: {json.dumps(r1.json(), indent=2)}")
    out(json.dumps(get_current_speaker(), indent=2)[:500])

    out("\n--- Setting back to the correct speaker: Andrew Cartledge ---")
    r2 = set_speaker(ANDREW_CARTLEDGE_ID)
    out(f"PATCH -> {r2.status_code}")
    out(json.dumps(get_current_speaker(), indent=2)[:500])


if __name__ == "__main__":
    main()
