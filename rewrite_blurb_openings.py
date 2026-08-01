#!/usr/bin/env python3
"""
Rewrites the openings AND closing call-to-action lines of AI-generated
blurbs so they don't all sound the same ("What if..." openings, "Hit play
and find out..." endings). Targets ONLY episodes listed in
blurb_progress.log (i.e. blurbs generate_missing_blurbs.py actually wrote)
- manually-edited blurbs and original Subsplash text are left alone.

Each targeted episode gets assigned one of 20 opening techniques and one
of 20 closing techniques (independently shuffled, so they don't correlate).
Episodes that already had their opening fixed in an earlier run only get
their closing touched up this time - the opening is left as-is rather than
re-rolled, to avoid wasted work and unnecessary drift from a version
that's already good.

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

CLOSING_STYLES = [
    "A short two or three word fragment, not a full sentence ('Worth the "
    "listen.')",
    "A quiet, understated invitation with no urgency ('Settle in and let "
    "this one land.')",
    "Naming what the listener will walk away with, without saying 'hit "
    "play'",
    "A statement of confidence about how the episode will affect them "
    "('You won't hear [theme] the same way again.')",
    "An open-ended prompt that leaves something unresolved on purpose",
    "A line acknowledging hesitation, low-pressure tone ('No pressure. "
    "Just press play when you're ready.')",
    "Framing it as something to revisit, not just a one-time listen",
    "A direct address naming who this episode is especially for",
    "A simple, grounded sign-off with no embellishment",
    "A line about timing or relevance to right now",
    "A challenge phrased gently, daring the listener to sit with it",
    "Referencing a specific detail or phrase from the episode itself as a "
    "teaser for the ending",
    "A short rhetorical question that isn't answered",
    "An observation about what listening might cost or ask of them",
    "A line suggesting they might want to talk about it with someone after",
    "A warm, plain statement instead of an instruction ('This one's for "
    "anyone who needs to hear it.')",
    "A callback to the opening line or image, bringing it full circle",
    "A single evocative word or short phrase standing alone as the final "
    "line",
    "A line about curiosity rather than urgency ('See where it takes you.')",
    "A plainly stated instruction using a verb other than 'hit play' or "
    "'press play' (e.g. 'Have a listen', 'Tune in', 'Give it a go')",
]

CLEANUP_INSTRUCTIONS = """Also strip out anything that isn't actual blurb content: remove any \
markdown formatting (bold asterisks, headers), remove any leading meta \
text like "Here are the show notes for this episode:" or similar preamble, \
and remove the episode title if it's been repeated at the top. The output \
should be pure prose ready to publish as-is."""

SHARED_CONSTRAINTS = """Keep the warm, direct, conversational tone. Avoid Christian cliches like \
"life-changing" or "powerful message." Do not use em dashes; use commas, \
colons, or separate sentences instead."""


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


def migrate_old_state(state: dict) -> dict:
    """Converts old {"style":..., "done":...} entries to the new
    opening/closing structure, treating old "done" as opening_done."""
    for title, v in state.items():
        if "opening_style" not in v:
            v["opening_style"] = v.pop("style", None)
            v["opening_done"] = v.pop("done", False)
            v["closing_style"] = None
            v["closing_done"] = False
    return state


def assign_styles(state: dict, titles: list):
    """Assigns opening and closing styles (independently shuffled) to any
    titles that don't have one yet."""
    for style_key, style_list in (("opening_style", OPENING_STYLES),
                                    ("closing_style", CLOSING_STYLES)):
        need = [t for t in titles if state.get(t, {}).get(style_key) is None]
        if not need:
            continue
        pool = []
        while len(pool) < len(need):
            batch = style_list[:]
            random.shuffle(batch)
            pool.extend(batch)
        pool = pool[:len(need)]
        for t, style in zip(need, pool):
            state.setdefault(t, {"opening_done": False, "closing_done": False,
                                  "opening_style": None, "closing_style": None})
            state[t][style_key] = style


def rewrite_full(client, old_blurb: str, opening_style: str, closing_style: str,
                  title: str, speaker: str) -> str:
    prompt = f"""Here is a podcast episode blurb for a sermon titled "{title}" \
by {speaker}:

{old_blurb}

Rewrite this blurb, keeping the same information, tone, and roughly the \
same length and structure (hook, then a teasing middle section, then a \
closing line). Change how it OPENS - use this technique:

{opening_style}

And change how it CLOSES (the final call-to-action line) - use this \
technique instead of whatever it currently uses:

{closing_style}

Do not start with "What if" or any rhetorical question phrased that way. \
Do not end with "Hit play and find out..." or "Press play and see where \
this one takes you" - those are overused; use the closing technique above \
instead.

{SHARED_CONSTRAINTS}

{CLEANUP_INSTRUCTIONS}

Return ONLY the rewritten blurb text, nothing else - no preamble, no \
explanation."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()


def rewrite_closing_only(client, old_blurb: str, closing_style: str,
                          title: str, speaker: str) -> str:
    prompt = f"""Here is a podcast episode blurb for a sermon titled "{title}" \
by {speaker}. The opening is already good and must NOT change:

{old_blurb}

Rewrite ONLY the final call-to-action line (the closing sentence). Leave \
every other sentence exactly as it is, word for word. Replace just the \
closing line using this technique:

{closing_style}

Do not end with "Hit play and find out..." or "Press play and see where \
this one takes you" - those are overused; use the closing technique above \
instead.

{SHARED_CONSTRAINTS}

Return the full blurb with only the closing line changed - nothing else, \
no preamble, no explanation."""

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
    state = migrate_old_state(state)

    entries_by_title = {e["title"]: e for e in entries}
    targets = [t for t in ai_titles if t in entries_by_title]

    assign_styles(state, targets)
    STATE_PATH.write_text(json.dumps(state, indent=2))

    todo = [t for t in targets
            if not (state[t]["opening_done"] and state[t]["closing_done"])]
    print(f"{len(todo)} blurb(s) still need work "
          f"out of {len(targets)} AI-generated total.")

    if args.dry_run:
        for t in todo[:20]:
            s = state[t]
            mode = "full rewrite" if not s["opening_done"] else "closing only"
            print(f"  {t}  ->  [{mode}]")
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
        s = state[title]
        print(f"\n[{i}/{len(todo)}] {title}")

        if not s["opening_done"]:
            print(f"  Full rewrite - opening: {s['opening_style'][:55]}...")
            print(f"                 closing: {s['closing_style'][:55]}...")
            new_blurb = rewrite_full(client, ep["blurb"], s["opening_style"],
                                      s["closing_style"], title, ep.get("speaker", ""))
        else:
            print(f"  Closing only: {s['closing_style'][:60]}...")
            new_blurb = rewrite_closing_only(client, ep["blurb"], s["closing_style"],
                                              title, ep.get("speaker", ""))

        print(f"  Result: {new_blurb[:100]}...")

        ep["blurb"] = new_blurb
        s["opening_done"] = True
        s["closing_done"] = True

        FEED_ITEMS_PATH.write_text(json.dumps(entries, indent=2))
        STATE_PATH.write_text(json.dumps(state, indent=2))

    remaining = len(targets) - sum(
        1 for t in targets if state[t]["opening_done"] and state[t]["closing_done"])
    print(f"\nBatch complete. {remaining} remaining.")
    if remaining == 0:
        print("All AI-generated blurbs now have varied openings and closings.")
    print("Next: commit feed_items.json and blurb_rewrite_state.json, then "
          "run migrate_subsplash_feed.py --fix-existing-blurbs to rebuild "
          "and re-upload feed.xml.")


if __name__ == "__main__":
    main()
