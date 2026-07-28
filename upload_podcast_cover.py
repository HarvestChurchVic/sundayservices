#!/usr/bin/env python3
"""One-time utility: uploads PodCover.png to R2 so the podcast's channel-level
artwork is hosted on our own domain rather than GitHub."""
from pathlib import Path
from pipeline import upload_to_r2

if __name__ == "__main__":
    url = upload_to_r2(Path("PodCover.png"), "images/podcast-cover.png")
    print(f"Uploaded podcast cover: {url}")
