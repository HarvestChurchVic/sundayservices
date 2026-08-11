#!/usr/bin/env python3
"""
Lists every video in the given YouTube playlist (title, upload date, URL),
then compares against feed_items.json to identify which Sundays don't have
a corresponding podcast episode — output in date order, for manual review
and editing.

This is read-only: it never modifies feed_items.json, the podcast feed, or
anything in Planning Center.
"""
import json
import subprocess
from pathlib import Path

PLAYLIST_URL = "https://youtube.com/playlist?list=PLEB-nuL6Dq5Bb6ED8M0Io3P3kAiF4i5pX"


def list_playlist_videos():
    """Uses yt-dlp to list every video in the playlist. Does NOT use
    --flat-playlist since that skips upload dates entirely — this is slower
    (fetches real metadata per video) but the dates actually come back."""
    cmd = [
        "yt-dlp", "--print",
        "%(title)s\t%(upload_date)s\t%(webpage_url)s",
        "--ignore-errors",
    ]
    if Path("youtube_cookies.txt").exists():
        cmd += ["--cookies", "youtube_cookies.txt"]
    cmd.append(PLAYLIST_URL)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    print("--- yt-dlp stderr (for debugging) ---")
    print(result.stderr[-3000:])

    with open("ytdlp_debug.log", "w") as f:
        f.write("=== RETURN CODE ===\n")
        f.write(str(result.returncode) + "\n\n")
        f.write("=== STDOUT ===\n")
        f.write(result.stdout + "\n\n")
        f.write("=== STDERR ===\n")
        f.write(result.stderr)

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        title, upload_date, url = parts
        videos.append({"title": title, "upload_date": upload_date, "url": url})
    return videos


def main():
    print(f"Scanning playlist: {PLAYLIST_URL}\n")
    videos = list_playlist_videos()
    print(f"Found {len(videos)} video(s) in the playlist.\n")

    # Sort by upload date (yt-dlp gives YYYYMMDD strings, or empty if unknown)
    videos_sorted = sorted(videos, key=lambda v: v["upload_date"] or "")

    with open("feed_items.json") as f:
        episodes = json.load(f)

    # Set of Sundays already covered in the podcast feed (YYYY-MM-DD)
    covered_dates = {e["pub_date"][:10] for e in episodes if e.get("pub_date")}

    print("=== All playlist videos, oldest to newest ===")
    for v in videos_sorted:
        date_str = v["upload_date"]
        formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) == 8 else "(unknown date)"
        in_feed = formatted in covered_dates
        marker = "✅ in feed" if in_feed else "❌ MISSING"
        print(f"{formatted}  {marker}  {v['title']}  ({v['url']})")

    missing = [v for v in videos_sorted if not (
        len(v["upload_date"]) == 8 and
        f"{v['upload_date'][:4]}-{v['upload_date'][4:6]}-{v['upload_date'][6:8]}" in covered_dates
    )]

    print(f"\n=== Summary ===")
    print(f"Total in playlist: {len(videos_sorted)}")
    print(f"Already in feed: {len(videos_sorted) - len(missing)}")
    print(f"Missing (candidates to add): {len(missing)}")

    output = {
        "playlist_url": PLAYLIST_URL,
        "total_in_playlist": len(videos_sorted),
        "already_in_feed": len(videos_sorted) - len(missing),
        "missing_candidates": missing,
        "all_videos": videos_sorted,
    }
    with open("missing_episodes_report.json", "w") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    main()
