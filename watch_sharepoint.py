#!/usr/bin/env python3
"""
Watches a SharePoint folder for new sermon video files and processes them
automatically. Meant to run on a schedule (see .github/workflows/watch_sharepoint.yml).

Expected filename convention for videos:
    YYYY-MM-DD_Title-With-Dashes_Speaker-Name.mp4
e.g. 2026-07-25_Strong-and-Courageous_Andrew-Cartledge.mp4

An optional matching thumbnail can be dropped in the same folder with the
same base filename and a .png extension, e.g.
2026-07-25_Strong-and-Courageous_Andrew-Cartledge.png

Files that don't match the naming convention are skipped and logged, not
processed — so a stray or oddly-named file won't crash the run.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import boto3
import requests
from botocore.client import Config as BotoConfig
from dotenv import load_dotenv
from msal import ConfidentialClientApplication

WORKDIR = Path(__file__).parent
PROCESSED_FILE = WORKDIR / "sharepoint_processed.json"

load_dotenv(WORKDIR / "config.env")

FILENAME_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_([A-Za-z0-9\-]+)_([A-Za-z0-9\-]+)\.(mp4|mov|m4v)$",
    re.IGNORECASE,
)


def env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        sys.exit(f"Missing required config value: {key} (check config.env)")
    return val


# ---------------------------------------------------------------------------
# Microsoft Graph auth and helpers
# ---------------------------------------------------------------------------

def get_graph_token() -> str:
    app = ConfidentialClientApplication(
        client_id=env("MS_CLIENT_ID"),
        client_credential=env("MS_CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{env('MS_TENANT_ID')}",
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        sys.exit(f"Failed to authenticate with Microsoft Graph: {result.get('error_description')}")
    return result["access_token"]


def graph_get(token: str, url: str) -> dict:
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json()


def get_site_id(token: str) -> str:
    hostname = env("SHAREPOINT_HOSTNAME")
    site_path = env("SHAREPOINT_SITE_PATH")
    url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{site_path}"
    return graph_get(token, url)["id"]


def list_folder_children(token: str, site_id: str, folder_path: str) -> list[dict]:
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{folder_path}:/children"
    return graph_get(token, url).get("value", [])


def download_file_content(token: str, download_url: str, dest_path: Path) -> None:
    resp = requests.get(download_url, headers={"Authorization": f"Bearer {token}"}, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


# ---------------------------------------------------------------------------
# R2 upload (thin copy of the same logic in pipeline.py, kept standalone so
# this script has no import-time dependency on pipeline.py)
# ---------------------------------------------------------------------------

def get_r2_client():
    account_id = env("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def upload_to_r2(local_path: Path, key: str) -> None:
    client = get_r2_client()
    bucket = env("R2_BUCKET_NAME")
    client.upload_file(str(local_path), bucket, key, ExtraArgs={"ACL": "public-read"})


# ---------------------------------------------------------------------------
# Processed-file tracking
# ---------------------------------------------------------------------------

def load_processed() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed(processed: set) -> None:
    PROCESSED_FILE.write_text(json.dumps(sorted(processed), indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    token = get_graph_token()
    site_id = get_site_id(token)
    folder_path = env("SHAREPOINT_FOLDER_PATH")
    children = list_folder_children(token, site_id, folder_path)

    processed = load_processed()
    by_name = {item["name"]: item for item in children if "file" in item}

    new_count = 0
    for name, item in by_name.items():
        item_id = item["id"]
        if item_id in processed:
            continue

        match = FILENAME_PATTERN.match(name)
        if not match:
            print(f"Skipping '{name}' — doesn't match the naming convention.")
            continue

        date_str, title_slug, speaker_slug, _ext = match.groups()
        title = title_slug.replace("-", " ")
        speaker = speaker_slug.replace("-", " ")

        print(f"\nProcessing new file: {name}")
        print(f"  Date: {date_str} | Title: {title} | Speaker: {speaker}")

        local_video = WORKDIR / "downloads" / name
        local_video.parent.mkdir(exist_ok=True)
        download_file_content(token, item["@microsoft.graph.downloadUrl"], local_video)

        r2_key = f"raw-uploads/{name}"
        print(f"  Uploading to R2 as {r2_key}...")
        upload_to_r2(local_video, r2_key)
        local_video.unlink()

        # Look for a matching thumbnail in the same folder
        thumb_name = f"{Path(name).stem}.png"
        thumb_item = by_name.get(thumb_name)
        if thumb_item:
            print(f"  Found matching thumbnail: {thumb_name}")
            local_thumb = WORKDIR / "downloads" / thumb_name
            download_file_content(token, thumb_item["@microsoft.graph.downloadUrl"], local_thumb)
            upload_to_r2(local_thumb, f"images/{Path(name).stem}.png")
            local_thumb.unlink()
            processed.add(thumb_item["id"])  # don't try to process the image as a video

        print("  Running pipeline...")
        result = subprocess.run(
            [
                "python", str(WORKDIR / "pipeline.py"), "",
                "--title", title,
                "--speaker", speaker,
                "--sermon-date", date_str,
                "--source-file", r2_key,
            ],
            check=False,
        )
        if result.returncode != 0:
            print(f"  Pipeline failed for {name} (exit code {result.returncode}) — "
                  f"will retry next run since it's not marked processed.")
            continue

        processed.add(item_id)
        save_processed(processed)
        new_count += 1

    if new_count == 0:
        print("No new sermon files found.")
    else:
        print(f"\nProcessed {new_count} new sermon(s).")


if __name__ == "__main__":
    main()
