#!/usr/bin/env python3
"""
Migrates every episode from the old Subsplash RSS feed into the R2-hosted
podcast feed — no transcription, no AI blurb generation. Brings across
exactly what's already there: date, title, speaker, existing blurb (if any),
mp3, and thumbnail. Re-hosts audio + images on R2 and merges everything into
feed_items.json in the same schema your pipeline already uses, then rebuilds
feed.xml sorted by date.

Requires: pip install boto3 requests feedgen ftfy

Environment variables required (same ones your GitHub Action secrets use):
    R2_ACCOUNT_ID
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET            (e.g. harvestchurch-sermons)
    R2_PUBLIC_BASE_URL   (e.g. https://hrvstpdcst.com)

Usage:
    python migrate_subsplash_feed.py --dry-run             # preview only
    python migrate_subsplash_feed.py                       # do it for real
    python migrate_subsplash_feed.py --fix-existing-blurbs # repair garbled
                                                             # text/titles
                                                             # already
                                                             # migrated

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

# Titles to skip entirely — case-insensitive substring match. Add more as you
# spot them while reviewing the Subsplash feed.
EXCLUDE_TITLE_PATTERNS = [
    "disciple 2 - topic",
]


def title_is_excluded(title: str) -> bool:
    t = title.lower()
    return any(pattern.lower() in t for pattern in EXCLUDE_TITLE_PATTERNS)


def slugify(text: str, date: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"{date}-{s}"[:100]


def clean_title(raw_title: str) -> str:
    """Repairs mojibake in titles - titles come straight from Subsplash's
    <title> tag and can carry the same encoding corruption as blurbs."""
    if not raw_title:
        return ""
    import ftfy
    return ftfy.fix_text(html.unescape(raw_title)).strip()


def clean_blurb(raw_html: str) -> str:
    """Converts Subsplash's HTML summary into plain text paragraphs,
    matching the plain-text style already used in feed_items.json.
    Also repairs mojibake (double/triple-encoded text) that's already
    corrupted in Subsplash's own source data for some older episodes."""
    if not raw_html or not raw_html.strip():
        return ""
    import ftfy
    text = html.unescape(raw_html)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = ftfy.fix_text(text)
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

        if title_is_excluded(title):
            print(f"  SKIPPING (excluded title): {title}")
            continue

        pub_dt = None
        if pub_date_raw:
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"):
                try:
                    pub_dt = datetime.strptime(pub_date_raw, fmt)
                    break
                except ValueError:
                    continue

        if pub_dt is None:
            print(f"  SKIPPING (no usable pubDate: {pub_date_raw!r}): {title}")
            continue

        items.append({
            "title": clean_title(title),
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
        with
