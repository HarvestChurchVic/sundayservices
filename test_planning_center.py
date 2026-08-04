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
    print("Testing connection to Planning Center Publishing API...\n")

    speakers = get("/speakers")
    print(f"Found {len(speakers['data'])} speaker(s):")
    for s in speakers["data"]:
        print(f"  id={s['id']}  name={s['attributes'].get('first_name', '')} {s['attributes'].get('last_name', '')}")

    print()
    channels = get("/channels")
    print(f"Found {len(channels['data'])} channel(s):")
    for c in channels["data"]:
        print(f"  id={c['id']}  name={c['attributes'].get('name', '')}")

    print()
    series = get("/series?per_page=10&order=-created_at")
    print(f"Most recent series (up to 10):")
    for s in series["data"]:
        print(f"  id={s['id']}  title={s['attributes'].get('title', '')}")

    print("\nConnection test successful — nothing was created or modified.")


if __name__ == "__main__":
    main()
