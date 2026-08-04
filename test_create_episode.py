#!/usr/bin/env python3
"""
Creates ONE test episode via the Planning Center Publishing API, clearly
marked so it's obvious in the interface, to verify how published_to_library_at
actually behaves — specifically whether setting it puts the episode in
"available on a specific date and time" mode. Prints back the full raw
response so we can see every field Planning Center actually stored, not
just what we sent.

Does NOT delete anything. Deletion happens as a separate, deliberate step
once the result has been checked in the real interface.
"""
import json
import os

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"

CHANNEL_ID = "28229"  # Sunday Sermons


def main():
    payload = {
        "data": {
            "type": "Episode",
            "attributes": {
                "title": "TEST - DELETE ME - Availability Field Check",
                "description": "Test episode created to verify published_to_library_at behaviour. Safe to delete.",
                "published_to_library_at": "2026-07-19T12:00:00+10:00",
            },
            "relationships": {
                "channel": {
                    "data": {"type": "Channel", "id": CHANNEL_ID}
                }
            },
        }
    }

    print("Creating test episode with payload:")
    print(json.dumps(payload, indent=2))
    print()

    resp = requests.post(
        f"{BASE}/episodes",
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
    print(f"Created episode id={data['id']}")
    print("\nFull stored attributes:")
    for key, value in data["attributes"].items():
        print(f"  {key}: {value}")

    print(f"\nCheck it in Planning Center, then we'll delete episode id={data['id']} once confirmed.")


if __name__ == "__main__":
    main()
