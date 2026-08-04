#!/usr/bin/env python3
"""
Sends a sample of the new email template using real data from "The
Foundation" (the most recent episode through the pipeline), by calling the
actual send_notification_email() function from pipeline.py directly — so
this is a true preview, not a hand-copied approximation.
"""
import os
import sys

import requests

sys.path.insert(0, ".")
from pipeline import send_notification_email, extract_youtube_video_id  # noqa: E402

PCO_BASE = "https://api.planningcenteronline.com/publishing/v2"


def pco_auth():
    return (os.environ["PCO_CLIENT_ID"], os.environ["PCO_SECRET"])


def find_pco_episode(title: str):
    """Looks up the real Planning Center episode for this title, if one
    exists, so the sample email uses genuine links rather than placeholders."""
    resp = requests.get(f"{PCO_BASE}/channels/28229/episodes?per_page=100", auth=pco_auth(), timeout=30)
    if not resp.ok:
        return None
    for ep in resp.json().get("data", []):
        if ep["attributes"]["title"].strip().lower() == title.strip().lower():
            return {
                "episode_url": ep["attributes"]["church_center_url"],
                "edit_url": f"https://publishing.planningcenteronline.com/sermons/episodes/{ep['id']}/edit",
            }
    return None


def main():
    title = "The Foundation"
    speaker = "Ps Andrew Cartledge"
    sermon_date = "2026-08-02"
    youtube_url = "https://youtu.be/aw5sYIL66HE"
    mp3_url = "https://hrvstpdcst.com/audio/2026-08-02-the-foundation.mp3"
    feed_url = "https://hrvstpdcst.com/feed.xml"
    image_url = "https://hrvstpdcst.com/images/1785725508752-The_Guaranteed_Christian_Life___The_Foundation___Ps_Andrew_Cartledge.png"
    blurb_text = """Honestly, some days it is hard to shake the feeling that something in your faith is broken, that everyone else seems to have figured out something you have not, and that you are quietly waiting to be found out.

This sermon digs into a question most people never think to ask: what is actually holding your faith up, and did you have anything to do with building it? Ps Andrew Cartledge takes a surprisingly honest and grounded look at identity, foundation, and what it really means to be a participant in something bigger than yourself. There are moments here that feel like someone finally said the quiet part out loud, especially around who you are, whose you are, and why that distinction matters more than most of us realise. It is the kind of conversation that sneaks up on you.

This one is for anyone who has ever quietly wondered if something is wrong with their faith."""
    hashtags_text = """#Sermon
#ChristianFaith
#HarvestChurch
#Faith
#ChristianIdentity
#BibleTeaching
#2Peter
#AustralianChurch
#SundaySermon
#WhoYouAre
#GodsFaithfulness
#ChurchOnline
#SpiritualGrowth
#NewLife
#Scripture"""
    # Matches pipeline.py's exact full_clean construction
    full_blurb = f"{blurb_text}\n\n{hashtags_text}"

    print(f"Looking up real Planning Center episode for '{title}'...")
    pco = find_pco_episode(title)
    if pco:
        print(f"Found: {pco['episode_url']}")
    else:
        print("No matching Planning Center episode found — will show the fallback text instead.")

    youtube_video_id = extract_youtube_video_id(youtube_url)
    youtube_edit_url = f"https://studio.youtube.com/video/{youtube_video_id}/edit" if youtube_video_id else None

    send_notification_email({
        "title": title,
        "speaker": speaker,
        "sermon_date": sermon_date,
        "youtube_url": youtube_url,
        "youtube_edit_url": youtube_edit_url,
        "mp3_url": mp3_url,
        "feed_url": feed_url,
        "blurb": full_blurb,
        "image_url": image_url,
        "pco_episode_url": pco["episode_url"] if pco else None,
        "pco_edit_url": pco["edit_url"] if pco else None,
    })
    print("Sample email sent.")


if __name__ == "__main__":
    main()
