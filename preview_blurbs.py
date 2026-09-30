#!/usr/bin/env python3
"""
Preview the new blurb writer on recent sermons, without changing anything.

For each of the most recent sermons in feed_items.json, this downloads the
hosted MP3, transcribes it, writes a new description with the current
generate_blurb(), and emails you the old and new versions side by side.
Nothing is saved: the feed, YouTube, Planning Center and Facebook are all
left exactly as they are.

Usage:
    python preview_blurbs.py --count 3
"""

import argparse
import json
from pathlib import Path

import requests

from pipeline import DOWNLOADS, generate_blurb, load_episode_log, transcribe
from social_publisher import send_plain_email


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()

    episodes = sorted(load_episode_log(), key=lambda e: e["pub_date"])
    DOWNLOADS.mkdir(exist_ok=True)
    sections = []

    for i in range(len(episodes) - args.count, len(episodes)):
        ep = episodes[i]
        print(f"\n=== {ep['title']} ({ep['pub_date'][:10]}) ===")
        mp3 = DOWNLOADS / f"preview-{i}.mp3"
        with requests.get(ep["mp3_url"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(mp3, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        transcript = transcribe(mp3)
        # Only the sermons BEFORE this one count as "recent", same as a real run
        new = generate_blurb(transcript, ep["title"], ep.get("speaker", ""), recent_episodes=episodes[:i])
        print(f"\nOLD:\n{ep['blurb']}\n\nNEW (starting from: {new['opening_style']}):\n{new['blurb']}")
        sections.append(
            f"{ep['title']} ({ep['pub_date'][:10]}, {ep.get('speaker', '')})\n\n"
            f"OLD\n{ep['blurb']}\n\n"
            f"NEW\n{new['blurb']}\n\n"
            f"Hashtags (YouTube only)\n{new['hashtags']}"
        )
        mp3.unlink(missing_ok=True)

    send_plain_email(
        f"Blurb preview: old vs new for the last {args.count} sermons",
        "Here's how the new blurb writer would have described the last few sermons. "
        "Nothing has been changed anywhere; this is just a preview.\n\n"
        + ("\n\n" + "=" * 60 + "\n\n").join(sections),
    )
    print("\nPreview email sent.")


if __name__ == "__main__":
    main()
