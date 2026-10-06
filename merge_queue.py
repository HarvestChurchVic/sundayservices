#!/usr/bin/env python3
"""
Saves the social queue safely when GitHub has moved on during a run.

The Publish Social run can stay on for a few hours after an upload. If
another sermon is processed meanwhile, GitHub's copy of pending_social.json
gains a new entry while this run's copy records progress on the old one, and
a plain "git pull --rebase" would stop on a conflict.

Used by the workflow's "Commit queue changes" step: it keeps this run's
copies of the two files, resets to GitHub's latest main, then writes back a
merge of both:

  - pending: this run's entries, plus any entries GitHub has that this run
    didn't know about (and hasn't finished)
  - history: everything from both, without duplicates

Usage: python merge_queue.py <our_pending.json> <our_history.json>
(run from the repo, after "git reset --hard origin/main")
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def key(item: dict) -> str:
    return item.get("slug") or item.get("title") or json.dumps(item, sort_keys=True)


def load(path) -> list:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return []


def main():
    ours_pending, ours_history = load(sys.argv[1]), load(sys.argv[2])
    theirs_pending = load(HERE / "pending_social.json")
    theirs_history = load(HERE / "social_history.json")

    history, seen = [], set()
    for item in theirs_history + ours_history:
        if key(item) not in seen:
            seen.add(key(item))
            history.append(item)

    pending = [i for i in ours_pending if key(i) not in seen]
    known = {key(i) for i in pending} | seen
    for item in theirs_pending:
        if key(item) not in known:
            print(f"Keeping '{item.get('title')}', queued on GitHub during this run.")
            pending.append(item)

    (HERE / "pending_social.json").write_text(json.dumps(pending, indent=2) + "\n")
    (HERE / "social_history.json").write_text(json.dumps(history, indent=2) + "\n")


if __name__ == "__main__":
    main()
