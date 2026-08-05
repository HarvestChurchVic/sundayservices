#!/usr/bin/env python3
"""
Read-only test of the Planning Center Publishing API connection. Creates,
modifies, and deletes nothing — just confirms the credentials work and
shows what's actually in the account, so we can match speaker names to
their real IDs before building anything that writes data.
"""
import json
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
        # Write progressively so a later crash doesn't lose earlier output
        with open("pco_test_output.log", "w") as f:
            f.write("\n".join(output))

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

    out("\n--- Checking series-to-channel relationship ---")
    series_full = get("/series?per_page=3&include=channel")
    out(json.dumps(series_full, indent=2)[:2500])

    out("\n--- Checking series query params in OpenAPI spec ---")
    spec_resp2 = requests.get(f"{BASE}/open_api/2024-03-25", auth=(CLIENT_ID, SECRET))
    if spec_resp2.ok:
        spec2 = spec_resp2.json()
        for path, methods in spec2.get("paths", {}).items():
            if path == "/series" or path == "/channels/{channel_id}/series":
                out(f"\n{path}: {sorted(methods.keys())}")
                get_params = methods.get("get", {}).get("parameters", [])
                for p in get_params:
                    out(f"  param: {p.get('name')}")

    out("\n--- Fetching a real Sunday Sermons episode for edit-link testing ---")
    real_ep = get("/channels/28229/episodes?per_page=1&order=-created_at")
    if real_ep.get("data"):
        ep = real_ep["data"][0]
        out(f"id={ep['id']}  title={ep['attributes'].get('title')}")

    out("\n--- Diagnosing The Foundation's actual PCO state ---")
    all_eps = get("/channels/28229/episodes?per_page=100&order=-created_at")
    for ep in all_eps.get("data", []):
        if ep["attributes"]["title"].strip().lower() == "the foundation":
            eid = ep["id"]
            out(f"Found episode id={eid}")
            out(json.dumps(ep["attributes"], indent=2))
            times = get(f"/episodes/{eid}/episode_times")
            out(f"\nEpisode times ({len(times.get('data', []))}):")
            out(json.dumps(times, indent=2)[:2000])
            break
    else:
        out("Could not find an episode titled 'The Foundation'")

    out("\n--- Checking what operations are supported on Speaker records ---")
    spec_resp = requests.get(f"{BASE}/open_api/2024-03-25", auth=(CLIENT_ID, SECRET))
    if spec_resp.ok:
        spec = spec_resp.json()
        paths = spec.get("paths", {})
        for path, methods in paths.items():
            if "/speakers" in path or "/episode_times" in path:
                out(f"{path}: {sorted(methods.keys())}")
                if "post" in methods:
                    props = methods["post"].get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
                    out(f"    POST schema: {json.dumps(props)[:800]}")
    else:
        out(f"Could not fetch OpenAPI spec: {spec_resp.status_code}")


if __name__ == "__main__":
    main()
