#!/usr/bin/env python3
"""
Read-only test of the Planning Center Publishing API connection. Creates,
modifies, and deletes nothing — just confirms the credentials work and
shows what's actually in the account, so we can match speaker names to
their real IDs before building anything that writes data.
"""
import os
import sys

import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"


def get(path):
    resp = requests.get(f"{BASE}{path}", auth=(CLIENT_ID, SECRET))
    if not resp.ok:
        print(f"FAILED: GET {path} -> {resp.status_code}")
        print(resp.text[:1000])
        sys.exit(1)
    return resp.json()


def main():
    output = []

    def out(text=""):
        print(text)
        output.append(text)

    out("Testing connection to Planning Center Publishing API...\n")

    speakers = get("/speakers")
    out(f"Found {len(speakers['data'])} speaker(s):")
    for s in speakers["data"]:
        out(f"  id={s['id']}  name={s['attributes'].get('first_name', '')} {s['attributes'].get('last_name', '')}")

    out()
    channels = get("/channels")
    out(f"Found {len(channels['data'])} channel(s):")
    for c in channels["data"]:
        out(f"  id={c['id']}  name={c['attributes'].get('name', '')}")

    out()
    series = get("/series?per_page=10&order=-created_at")
    out(f"Most recent series (up to 10):")
    for s in series["data"]:
        out(f"  id={s['id']}  title={s['attributes'].get('title', '')}")

    out("\nConnection test successful — nothing was created or modified.")

    out("\n--- Checking what operations are supported on Speaker records ---")
    spec_resp = requests.get(f"{BASE}/open_api/2024-03-25", auth=(CLIENT_ID, SECRET))
    if spec_resp.ok:
        spec = spec_resp.json()
        paths = spec.get("paths", {})
        for path, methods in paths.items():
            if "/speakers" in path:
                out(f"{path}: {sorted(methods.keys())}")
    else:
        out(f"Could not fetch OpenAPI spec: {spec_resp.status_code}")

    with open("pco_test_output.log", "w") as f:
        f.write("\n".join(output))


if __name__ == "__main__":
    main()
