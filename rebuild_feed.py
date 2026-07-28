#!/usr/bin/env python3
"""
Rebuilds and re-uploads feed.xml from the current feed_items.json, without
processing a new episode. Useful after manually editing episode data (e.g.
fixing URLs after switching to a custom domain).
"""
from pipeline import load_episode_log, build_and_upload_feed

if __name__ == "__main__":
    episodes = load_episode_log()
    feed_url = build_and_upload_feed(episodes)
    print(f"Feed rebuilt with {len(episodes)} episode(s): {feed_url}")
