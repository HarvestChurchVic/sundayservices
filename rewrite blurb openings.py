#!/usr/bin/env python3
"""
Rewrites the openings of AI-generated blurbs so they don't all sound the
same ("What if...", "Have you ever...", etc.). Targets ONLY episodes listed
in blurb_progress.log (i.e. blurbs generate_missing_blurbs.py actually
wrote) - manually-edited blurbs and original Subsplash text are left alone.

Each targeted episode gets assigned one of 20 distinct opening techniques,
shuffled so styles don't repeat in a predictable pattern across the batch.
Assignments and completion state are saved to blurb_rewrite_state.json so
this is resumable/batchable exactly like generate_missing_blurbs.py.

Requires: pip install anthropic

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...

    python rewrite_blurb_openings.py --dry-run        # preview assignments
    python rewrite_blurb_openings.py --batch-size 15   # process a batch
    python rewrite_blurb_openings.py                   # process everything
"""

import argparse
import json
import random
import time
from pathlib import Path

FEED_ITEMS_PATH = Path("feed_items.json")
PROGRESS_LOG_PATH = Path("blurb_progress.log")
STATE_PATH = Path("blurb_rewrite_state.json")

OPENING_STYLES = [
    "A direct question aimed at the listener's own life (not 'What if...' - "
    "something more like 'Have you ever caught yourself...')",
    "A bold, flat statement of fact or claim, no question mark",
    "A vivid, specific image or scene from everyday life",
    "A short paraphrase or echo of a line from the passage itself",
    "A first-person-style admission, as if the preacher is confessing "
    "something ('There's a version of faith that...')",
    "A contrast or tension stated plainly ('On one side... on the other...')",
    "A relatable, mundane everyday scenario that leads into the theme",
    "A provocative one-line claim that sounds almost like a challenge",
    "Naming a common misconception people have, then setting it up to be "
    "addressed",
    "A scene-setting description of a moment in Scripture, present tense",
    "Starting mid-thought, as though continuing a conversation already "
    "underway",
    "A confession of doubt, struggle, or uncertainty",
    "Directly naming a paradox or tension in the theme",
    "An invitation to imagine a specific situation",
    "A short, punchy sentence fragment rather than a full sentence",
    "A callback to a well-known saying or verse, reframed unexpectedly",
    "Describing what it feels like to be in the situation the sermon "
    "addresses, without naming the theme yet",
    "A statement about what most people assume, setting up to challenge it",
    "An observation about a small, specific detail from the sermon that "
    "hints at the bigger theme",
    "A statement addressed to a specific kind of listener ('If you've ever "
    "felt like...')",
]


def load_ai_generated_titles() -> set:
    if not PROGRESS_LOG_PATH.exists():
        return set()
    titles = set()
    for line in PROGRESS_LOG_PATH.read_text().splitlines():
        # format: "YYYY-MM-DD HH:MM  DONE  YYYY-MM-DD  Title"
        parts = line.split("  ")
        if len(parts) >= 4 and parts[1].strip() == "DONE":
            titles.add("  ".join(parts[3:]).strip())
    return titles


def rewrite_opening(client, old_blurb: str, style: str, title: str, speaker: str) -> str:
    prompt = f"""Here is a podcast episode blurb for a sermon titled "{title}" \
by {speaker}:

{old_blurb}

Rewrite this blurb, keeping the same information, tone, and roughly the \
same length and structure (hook, then a teasing middle section, then a \
one-sentence call to action). Change ONLY the way it opens - use this \
specific opening technique instead of whatever it currently uses:

{style}

Do not start with "What if" or any rhetorical question phrased that way. \
Keep the warm, direct, conversational tone. Avoid Christian cliches like \
"life-changing" or "powerful message." Do not use em dashes; use commas, \
colons, or separate sentences instead.

Also strip out anything that isn't actual blurb content: remove any \
markdown formatting (bold asterisks, headers), remove any leading meta \
text like "Here are the show notes for this episode:" or similar preamble, \
and remove the episode title if it's been repeated at the top. The output \
should be pure prose ready to publish as-is, starting directly with the \
new opening.

Return ONLY the rewritten blurb text, nothing else - no preamble, no \
explanation."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()

    entries = json.loads(FEED_ITEMS_PATH.read_text())
    ai_titles = load_ai_generated_titles()
    print(f"Found {len(ai_titles)} AI-generated blurbs in {PROGRESS_LOG_PATH}.")

    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}

    # Assign a style to every AI-generated episode up front (once), so the
    # same episode always gets the same style even across resumed runs.
    entries_by_title = {e["title"]: e for e in entries}
    targets = [t for t in ai_titles if t in entries_by_title]

    unassigned = [t for t in targets if t not in state]
    if unassigned:
        # Shuffle a repeating cycle of styles so assignment is varied and
        # doesn't cluster the same style together.
        pool = []
        while len(pool) < len(unassigned):
            batch = OPENING_STYLES[:]
            random.shuffle(batch)
            pool.extend(batch)
        pool = pool[:len(unassigned)]
        for t, style in zip(unassigned, pool):
            state[t] = {"style": style, "done": False}
        STATE_PATH.write_text(json.dumps(state, indent=2))

    todo = [t for t in targets if not state[t]["done"]]
    print(f"{len(todo)} blurb(s) still need their opening rewritten "
          f"out of {len(targets)} AI-generated total.")

    if args.dry_run:
        for t in todo[:20]:
            print(f"  {t}  ->  {state[t]['style'][:60]}...")
        return

    if not todo:
        print("Nothing to do.")
        return

    if args.batch_size:
        todo = todo[:args.batch_size]

    import anthropic
    client = anthropic.Anthropic()

    for i, title in enumerate(todo, 1):
        ep = entries_by_title[title]
        style = state[title]["style"]
        print(f"\n[{i}/{len(todo)}] {title}")
        print(f"  Style: {style[:70]}...")

        new_blurb = rewrite_opening(client, ep["blurb"], style, title, ep.get("speaker", ""))
        print(f"  New opening: {new_blurb[:100]}...")

        ep["blurb"] = new_blurb
        state[title]["done"] = True

        FEED_ITEMS_PATH.write_text(json.dumps(entries, indent=2))
        STATE_PATH.write_text(json.dumps(state, indent=2))

    remaining = len(targets) - sum(1 for t in targets if state[t]["done"])
    print(f"\nBatch complete. {remaining} remaining.")
    if remaining == 0:
        print("All AI-generated blurbs now have varied openings.")
    print("Next: commit feed_items.json and blurb_rewrite_state.json, then "
          "run migrate_subsplash_feed.py --fix-existing-blurbs to rebuild "
          "and re-upload feed.xml.")


if __name__ == "__main__":
    main()
