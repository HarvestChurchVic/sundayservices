#!/usr/bin/env python3
"""
Updates the existing test episode (id 719809) so published_live_at matches
published_to_library_at, based on the warning shown in Planning Center's
interface when they differ. Prints back the full stored attributes so we
can confirm the fix took effect.
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"

EPISODE_ID = "719809"


def main():
    payload = {
        "data": {
            "type": "Episode",
            "attributes": {
                "published_to_library_at": "2026-07-19T12:00:00+10:00",
                "published_live_at": "2026-07-19T12:00:00+10:00",
            },
        }
    }

    print(f"Updating episode {EPISODE_ID} with payload:")
    print(json.dumps(payload, indent=2))
    print()

    resp = requests.patch(
        f"{BASE}/episodes/{EPISODE_ID}",
        auth=(CLIENT_ID, SECRET),
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
    )

    print(f"Response status: {resp.status_code}\n")

    with open("pco_test_output.log", "w") as f:
        f.write(f"Response status: {resp.status_code}\n\n")
        f.write(json.dumps(resp.json(), indent=2))

    if not resp.ok:
        print("FAILED:")
        print(json.dumps(resp.json(), indent=2))
        return

    data = resp.json()["data"]
    print("Updated. Full stored attributes:")
    for key, value in data["attributes"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
