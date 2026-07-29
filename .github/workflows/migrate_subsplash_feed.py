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

CHANNEL_TITLE = "Harvest Church"
CHANNEL_LINK = "https://www.harvestchurch.org.au/"
CHANNEL_AUTHOR = "Harvest Church VIC"
CHANNEL_OWNER_EMAIL = "media@harvestchurch.org.au"
CHANNEL_SUBTITLE = "Loving God. Growing Together. Reaching the World."
CHANNEL_DESCRIPTION = (
    "Harvest Church is a multi-campus church family serving communities "
    "across the Wimmera region of Victoria, Australia. Everything we do "
    "flows from three simple convictions: loving God, growing together, "
    "and reaching the world. Each week, Senior Pastors Andrew and Rachel "
    "Cartledge bring biblical teaching that's honest, practical, and "
    "centred on Jesus, alongside guest speakers from within our church "
    "family. This podcast is our Sunday gatherings made available to "
    "anyone who wants to grow in their relationship with God, find "
    "genuine community, and discover what it looks like to carry faith "
    "into everyday life. Wherever you're at in your journey, we're glad "
    "you're here."
)
# Original show cover art from Subsplash - re-hosted on R2 rather than
# linked directly, so the feed doesn't depend on Subsplash staying online.
SUBSPLASH_COVER_ART_URL = (
    "https://images.subsplash.com/base64/"
    "L2ltYWdlLmpwZz9pZD1hZmEwNDJlYi1iNWRlLTQ3ZGUtYjIxYy1jODk4NmM0OTJmOGUmdz0zMDAwJmg9MzAwMCZhbGxvd191cHNjYWxlPXRydWU"
    ".jpg"
)

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
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


def upload_to_r2(client, bucket, local_path: Path, key: str, content_type: str) -> str:
    extra_args = {"ContentType": content_type}
    if key == "feed.xml":
        # Prevent long-lived caching of the feed so updates show up promptly
        # for podcast apps and don't get served stale via Cloudflare's edge cache.
        extra_args["CacheControl"] = "public, max-age=300"
    client.upload_file(str(local_path), bucket, key, ExtraArgs=extra_args)
    base = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")
    return f"{base}/{key}"


def ensure_cover_art(client, bucket) -> str:
    """Downloads the show's cover art once and re-hosts it on R2, returning
    the R2-hosted URL. Safe to call every run - just re-uses the same key."""
    local_path = DOWNLOAD_DIR / "cover.jpg"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    download(SUBSPLASH_COVER_ART_URL, local_path)
    return upload_to_r2(client, bucket, local_path, "cover.jpg", "image/jpeg")


def build_feed_xml(items, out_path: Path, cover_art_url: str):
    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(CHANNEL_TITLE)
    fg.link(href=CHANNEL_LINK, rel="alternate")
    fg.description(CHANNEL_DESCRIPTION)
    fg.podcast.itunes_summary(CHANNEL_DESCRIPTION)
    fg.podcast.itunes_subtitle(CHANNEL_SUBTITLE)
    fg.image(url=cover_art_url, title=CHANNEL_TITLE, link=CHANNEL_LINK)
    fg.podcast.itunes_image(cover_art_url)
    fg.podcast.itunes_author(CHANNEL_AUTHOR)
    fg.podcast.itunes_owner(name=CHANNEL_AUTHOR, email=CHANNEL_OWNER_EMAIL)
    fg.language("en")
    fg.podcast.itunes_category("Religion & Spirituality", "Christianity")
    fg.podcast.itunes_explicit("no")

    for ep in sorted(items, key=lambda e: e["pub_date"]):
        fe = fg.add_entry()
        fe.id(ep["mp3_url"])
        fe.title(ep["title"])
        fe.description(ep["blurb"] or "")
        fe.enclosure(ep["mp3_url"], str(ep.get("filesize", 0)), "audio/mpeg")
        fe.pubDate(ep["pub_date"])
        fe.podcast.itunes_author(ep.get("speaker", ""))
        if ep.get("image_url"):
            fe.podcast.itunes_image(ep["image_url"])

    fg.rss_file(str(out_path), pretty=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N episodes (for testing)")
    parser.add_argument("--fix-existing-blurbs", action="store_true",
                         help="Repair mojibake/garbled text in blurbs already in "
                              "feed_items.json, rebuild feed.xml, and re-upload. "
                              "Does NOT re-download or re-upload any audio/images.")
    args = parser.parse_args()

    if args.fix_existing_blurbs:
        import ftfy
        existing = json.loads(FEED_ITEMS_PATH.read_text())
        changed = 0
        for e in existing:
            new_blurb = ftfy.fix_text(e.get("blurb", "") or "")
            new_title = ftfy.fix_text(e.get("title", "") or "")
            if new_blurb != e.get("blurb", ""):
                print(f"  BLURB FIXED [{e.get('title')}]:")
                print(f"    before: {e.get('blurb', '')[:80]!r}")
                print(f"    after:  {new_blurb[:80]!r}")
                changed += 1
            if new_title != e.get("title", ""):
                print(f"  TITLE FIXED: {e.get('title')!r} -> {new_title!r}")
                changed += 1
            e["blurb"] = new_blurb
            e["title"] = new_title
        print(f"Repaired {changed} field(s) across {len(existing)} total episodes.")

        deduped = []
        seen = set()
        removed = 0
        for e in existing:
            key = (e["title"], e["pub_date"][:10])
            if key in seen:
                print(f"  REMOVING DUPLICATE: {e['title']} ({e['pub_date'][:10]})")
                removed += 1
                continue
            seen.add(key)
            deduped.append(e)
        if removed:
            print(f"Removed {removed} duplicate episode(s), {len(deduped)} remain.")
        existing = deduped

        FEED_ITEMS_PATH.write_text(json.dumps(existing, indent=2))

        feed_items_for_xml = []
        for e in existing:
            e2 = dict(e)
            e2["pub_date"] = datetime.fromisoformat(e["pub_date"])
            feed_items_for_xml.append(e2)

        feed_xml_path = Path("feed.xml")
        client = get_r2_client()
        bucket = os.environ["R2_BUCKET"]
        cover_art_url = ensure_cover_art(client, bucket)
        build_feed_xml(feed_items_for_xml, feed_xml_path, cover_art_url)

        feed_public_url = upload_to_r2(client, bucket, feed_xml_path, "feed.xml", "application/rss+xml")
        upload_to_r2(client, bucket, FEED_ITEMS_PATH, "feed_items.json", "application/json")
        print(f"Uploaded repaired feed.xml -> {feed_public_url}")
        print("Done. Commit the updated feed_items.json back to the repo.")
        return

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
        existing_keys.add((ep["title"], date_str))

    FEED_ITEMS_PATH.write_text(json.dumps(new_entries, indent=2))
    print(f"Wrote {len(new_entries)} total episodes to {FEED_ITEMS_PATH}")

    feed_items_for_xml = []
    for e in new_entries:
        e2 = dict(e)
        e2["pub_date"] = datetime.fromisoformat(e["pub_date"])
        feed_items_for_xml.append(e2)

    feed_xml_path = Path("feed.xml")
    cover_art_url = ensure_cover_art(client, bucket)
    build_feed_xml(feed_items_for_xml, feed_xml_path, cover_art_url)
    feed_public_url = upload_to_r2(client, bucket, feed_xml_path, "feed.xml", "application/rss+xml")
    print(f"Uploaded feed.xml -> {feed_public_url}")

    # Also re-upload feed_items.json to R2 for reference/backup, matching pattern
    upload_to_r2(client, bucket, FEED_ITEMS_PATH, "feed_items.json", "application/json")
    print("Done. Remember to also commit the updated feed_items.json back to the repo.")


if __name__ == "__main__":
    main()
