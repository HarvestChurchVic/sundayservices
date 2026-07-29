#!/usr/bin/env python3
"""
Backfills missing/poor blurbs in feed_items.json by transcribing each
episode's already-hosted R2 audio with faster-whisper, then generating a
podcast-style blurb with Claude - the same two-step process your existing
sermon pipeline uses, just pointed at the historical backlog instead of new
uploads.

Does NOT touch feed.xml or R2 directly - it only edits feed_items.json
locally. After running this, use migrate_subsplash_feed.py's
--fix-existing-blurbs mode to rebuild and re-upload feed.xml from the
updated file (it already handles that cleanly).

Requires: pip install faster-whisper anthropic requests

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...

    # ALWAYS do this first - times one episode on your actual machine so we
    # know real throughput instead of guessing:
    python generate_missing_blurbs.py --benchmark

    # Preview which episodes would be processed, no work done:
    python generate_missing_blurbs.py --dry-run

    # Process a small batch to sanity-check quality before committing:
    python generate_missing_blurbs.py --limit 3

    # Full run - safe to Ctrl+C and resume any time, picks up where it left off:
    python generate_missing_blurbs.py
"""

import argparse
import json
import time
from pathlib import Path

import requests

FEED_ITEMS_PATH = Path("feed_items.json")  # run from your repo root
DOWNLOAD_DIR = Path("./_blurb_downloads")
PROGRESS_LOG_PATH = Path("blurb_progress.log")

# Same criteria used to build the review spreadsheet - keeps the two in sync.
BOILERPLATE_MARKERS = [
    "ccli streaming license", "facebook.com/harvestchurchhorsham",
    "harvestchurchhorsham", "give:", "prayer:", "website:", "instagram:",
]


def needs_blurb(blurb: str) -> bool:
    b = (blurb or "").strip()
    if not b:
        return True
    low = b.lower()
    if sum(1 for m in BOILERPLATE_MARKERS if m in low) >= 2:
        return True
    if b.lower().startswith("title:") and "description:" in low:
        return True
    if len(b.split()) < 12:
        return True
    return False


BLURB_PROMPT_TEMPLATE = """You are a podcast producer writing show notes to \
promote a sermon episode from the source content. Your job is not to summarise \
the content but to create curiosity and draw the listener in so they press play.

The episode is a sermon from Harvest Church, an Australian Christian Churches \
multi-campus church in the Wimmera region of Victoria, preached by {speaker}. \
The transcript below is the sermon itself.

Sermon title: {title}

Write a podcast episode description using this structure:

An opening hook of 2 to 3 sentences that poses a question or tension related \
to the episode's core theme. Do not reveal the answer or resolution.

A short paragraph of 3 to 4 sentences that teases what the listener will \
encounter without spoiling it. Use language that creates anticipation.

A one sentence call to action inviting the listener to press play.

Tone: warm, direct, and conversational. Avoid Christian cliche phrases like \
"life-changing" or "powerful message." Write as if you are talking to someone \
who is spiritually curious but not necessarily a regular churchgoer. Do not \
include timestamps, links, or any placeholder text. Do not use em dashes \
anywhere; use commas, colons, or separate sentences instead.

Transcript:
{transcript}
"""


def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


def transcribe(mp3_path: Path, model) -> str:
    segments, _info = model.transcribe(str(mp3_path), beam_size=5)
    return " ".join(seg.text.strip() for seg in segments)


def generate_blurb(client, transcript: str, title: str, speaker: str) -> str:
    prompt = BLURB_PROMPT_TEMPLATE.format(
        speaker=speaker or "one of our pastors", title=title, transcript=transcript[:20000]
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--benchmark", action="store_true",
                         help="Time transcription on ONE episode, print throughput, then exit.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None,
                         help="Process only this many episodes this run, then stop. "
                              "Just re-run the same command later to continue - "
                              "already-done episodes are automatically skipped.")
    parser.add_argument("--model", default="small",
                         help="Whisper model size: tiny/base/small/medium. "
                              "Smaller = faster but rougher transcript (still fine "
                              "for blurb generation).")
    args = parser.parse_args()

    entries = json.loads(FEED_ITEMS_PATH.read_text())
    todo_all = [e for e in entries if needs_blurb(e.get("blurb", ""))]
    print(f"{len(todo_all)} episodes still need a blurb out of {len(entries)} total.")

    if args.dry_run:
        for e in todo_all:
            print(f"  {e['pub_date'][:10]}  {e['title']}")
        return

    if args.benchmark:
        todo = todo_all[:1]
        if not todo:
            print("Nothing to benchmark.")
            return
    else:
        todo = todo_all
        if args.batch_size:
            todo = todo[:args.batch_size]
            print(f"Processing this batch: {len(todo)} episode(s) "
                  f"({len(todo_all) - len(todo)} will remain after this run).")
        if args.limit:
            todo = todo[:args.limit]

    from faster_whisper import WhisperModel
    import anthropic

    print(f"Loading Whisper '{args.model}' model (CPU, int8)...")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    client = anthropic.Anthropic()

    for i, ep in enumerate(todo, 1):
        title = ep["title"]
        print(f"\n[{i}/{len(todo)}] {ep['pub_date'][:10]}  {title}")

        t0 = time.time()
        slug = title.lower().replace(" ", "-")[:60]
        mp3_path = download(ep["mp3_url"], DOWNLOAD_DIR / f"{slug}.mp3")
        t_download = time.time() - t0

        t0 = time.time()
        transcript = transcribe(mp3_path, model)
        t_transcribe = time.time() - t0
        print(f"  Transcribed {len(transcript.split())} words "
              f"in {t_transcribe/60:.1f} min "
              f"(download took {t_download:.0f}s)")

        t0 = time.time()
        blurb = generate_blurb(client, transcript, title, ep.get("speaker", ""))
        t_blurb = time.time() - t0
        print(f"  Blurb generated in {t_blurb:.0f}s")
        print(f"  Preview: {blurb[:150]}...")

        mp3_path.unlink(missing_ok=True)  # don't fill disk over a long run

        if args.benchmark:
            filesize_mb = ep.get("filesize", 0) / 1_000_000
            print(f"\n--- BENCHMARK RESULT ---")
            print(f"This episode: {filesize_mb:.0f}MB, "
                  f"transcription took {t_transcribe/60:.1f} minutes.")
            print(f"To extrapolate: check the full file for total flagged "
                  f"audio size, divide by this episode's size, multiply by "
                  f"{t_transcribe/60:.1f} min to estimate total time.")
            return

        # Update in place and checkpoint immediately - safe to interrupt.
        ep["blurb"] = blurb
        FEED_ITEMS_PATH.write_text(json.dumps(entries, indent=2))

        with open(PROGRESS_LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')}  DONE  "
                    f"{ep['pub_date'][:10]}  {title}\n")

    remaining = len(todo_all) - len(todo)
    print(f"\nThis batch complete: {len(todo)} blurb(s) written.")
    if remaining > 0:
        print(f"{remaining} episode(s) still remaining. "
              f"Just re-run the same command again (any time - today, "
              f"next week, whenever) to keep going from here.")
    else:
        print("All flagged episodes now have blurbs.")
        print("Next: commit feed_items.json, then run migrate_subsplash_feed.py "
              "--fix-existing-blurbs to rebuild and re-upload feed.xml.")


if __name__ == "__main__":
    main()
