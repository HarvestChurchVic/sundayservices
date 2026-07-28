#!/usr/bin/env python3
"""
Migrates every episode from the old Subsplash RSS feed into the R2-hosted
podcast feed — no transcription, no AI blurb generation. Brings across
exactly what's already there: date, title, speaker, existing blurb (if any),
mp3, and thumbnail. Re-hosts audio + images on R2 and merges everything into
feed_items.json in the same schema your pipeline already uses, then rebuilds
feed.xml sorted by date.

Requires: pip install boto3 requests feedgen

Environment variables required (same ones your GitHub Action secrets use):
    R2_ACCOUNT_ID
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET            (e.g. harvestchurch-sermons)
    R2_PUBLIC_BASE_URL   (e.g. https://hrvstpdcst.com)

Usage:
    export R2_ACCOUNT_ID=...
    export R2_ACCESS_KEY_ID=...
    export R2_SECRET_ACCESS_KEY=...
    export R2_BUCKET=harvestchurch-sermons
    export R2_PUBLIC_BASE_URL=https://hrvstpdcst.com

    python migrate_subsplash_feed.py --dry-run     # preview only, no uploads
    python migrate_subsplash_feed.py               # do it for real

Safe to re-run: episodes already present in feed_items.json (matched by
title + pub_date) are skipped, so you can stop and resume any time.
"""

import argparse
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

SUBSPLASH_FEED_URL = "https://podcasts.subsplash.com/4pt45qv/podcast.rss"
FEED_ITEMS_PATH = Path("feed_items.json")  # run this from your repo root
DOWNLOAD_DIR = Path("./_migration_downloads")

ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def slugify(text: str, date: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"{date}-{s}"[:100]


def clean_blurb(raw_html: str) -> str:
    """Converts Subsplash's HTML summary into plain text paragraphs,
    matching the plain-text style already used in feed_items.json."""
    if not raw_html or not raw_html.strip():
        return ""
    text = html.unescape(raw_html)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_subsplash_feed():
    print(f"Fetching {SUBSPLASH_FEED_URL} ...")
    resp = requests.get(SUBSPLASH_FEED_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        author = (item.findtext(f"{ITUNES_NS}author") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()
        summary = item.findtext(f"{ITUNES_NS}summary") or item.findtext("description") or ""
        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url") if enclosure is not None else None
        length = int(enclosure.get("length", 0)) if enclosure is not None else 0
        image_el = item.find(f"{ITUNES_NS}image")
        image_url = image_el.get("href") if image_el is not None else None

        if not audio_url:
            continue  # skip anything without audio

        pub_dt = datetime.strptime(pub_date_raw, "%a, %d %b %Y %H:%M:%S %z") \
            if pub_date_raw else None

        items.append({
            "title": title,
            "speaker": author,
            "blurb": clean_blurb(summary),
            "audio_url": audio_url,
            "filesize": length,
            "pub_date": pub_dt,
            "image_url": image_url,
        })
    print(f"Parsed {len(items)} episodes from Subsplash feed.")
    return items


def get_r2_client():
    import boto3
    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


def upload_to_r2(client, bucket, local_path: Path, key: str, content_type: str) -> str:
    client.upload_file(str(local_path), bucket, key, ExtraArgs={"ContentType": content_type})
    base = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")
    return f"{base}/{key}"


def build_feed_xml(items, out_path: Path):
    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title("Harvest Church")
    fg.link(href="https://www.harvestchurch.org.au/", rel="alternate")
    fg.description(
        "Harvest Church exists to love God passionately, serve others "
        "diligently and make disciples of Jesus. Join Senior Pastors "
        "Andrew & Rachel Cartledge as they teach biblical truth that will "
        "build your life."
    )
    fg.language("en")
    fg.podcast.itunes_category("Religion & Spirituality", "Christianity")
    fg.podcast.itunes_explicit("no")

    for ep in sorted(items, key=lambda e: e["pub_date"]):
        fe = fg.add_entry()
        fe.title(ep["title"])
        fe.description(ep["blurb"] or ep["title"])
        fe.enclosure(ep["mp3_url"], str(ep.get("filesize", 0)), "audio/mpeg")
        fe.pubDate(ep["pub_date"])
        fe.podcast.itunes_author(ep.get("speaker", ""))
        if ep.get("image_url"):
            fe.podcast.itunes_image(ep["image_url"])

    fg.rss_file(str(out_path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N episodes (for testing)")
    args = parser.parse_args()

    existing = json.loads(FEED_ITEMS_PATH.read_text()) if FEED_ITEMS_PATH.exists() else []
    existing_keys = {(e["title"], e["pub_date"][:10]) for e in existing}

    subsplash_items = parse_subsplash_feed()
    if args.limit:
        subsplash_items = subsplash_items[:args.limit]

    to_migrate = [
        ep for ep in subsplash_items
        if (ep["title"], ep["pub_date"].date().isoformat()) not in existing_keys
    ]
    print(f"{len(to_migrate)} new episodes to migrate "
          f"({len(subsplash_items) - len(to_migrate)} already present, skipped).")

    if args.dry_run:
        for ep in to_migrate:
            print(f"  [dry-run] {ep['pub_date'].date()}  {ep['title']}  ({ep['speaker']})")
        return

    if not to_migrate:
        print("Nothing to do.")
        return

    client = get_r2_client()
    bucket = os.environ["R2_BUCKET"]
    new_entries = list(existing)

    for i, ep in enumerate(to_migrate, 1):
        date_str = ep["pub_date"].date().isoformat()
        slug = slugify(ep["title"], date_str)
        print(f"[{i}/{len(to_migrate)}] {date_str}  {ep['title']}")

        mp3_local = download(ep["audio_url"], DOWNLOAD_DIR / f"{slug}.mp3")
        mp3_url = upload_to_r2(client, bucket, mp3_local, f"audio/{slug}.mp3", "audio/mpeg")
        filesize = mp3_local.stat().st_size

        image_url = None
        if ep["image_url"]:
            img_local = download(ep["image_url"], DOWNLOAD_DIR / f"{slug}.jpg")
            image_url = upload_to_r2(client, bucket, img_local, f"images/{slug}.jpg", "image/jpeg")

        new_entries.append({
            "title": ep["title"],
            "speaker": ep["speaker"],
            "blurb": ep["blurb"],
            "mp3_url": mp3_url,
            "filesize": filesize,
            "pub_date": ep["pub_date"].isoformat(),
            "image_url": image_url,
        })

    FEED_ITEMS_PATH.write_text(json.dumps(new_entries, indent=2))
    print(f"Wrote {len(new_entries)} total episodes to {FEED_ITEMS_PATH}")

    feed_items_for_xml = []
    for e in new_entries:
        e2 = dict(e)
        e2["pub_date"] = datetime.fromisoformat(e["pub_date"])
        feed_items_for_xml.append(e2)

    feed_xml_path = Path("feed.xml")
    build_feed_xml(feed_items_for_xml, feed_xml_path)
    feed_public_url = upload_to_r2(client, bucket, feed_xml_path, "feed.xml", "application/rss+xml")
    print(f"Uploaded feed.xml -> {feed_public_url}")

    # Also re-upload feed_items.json to R2 for reference/backup, matching pattern
    upload_to_r2(client, bucket, FEED_ITEMS_PATH, "feed_items.json", "application/json")
    print("Done. Remember to also commit the updated feed_items.json back to the repo.")


if __name__ == "__main__":
    main()
