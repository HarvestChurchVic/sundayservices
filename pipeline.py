#!/usr/bin/env python3
"""
Harvest Church sermon repurposing pipeline.

Takes the URL of a finished, edited YouTube clip (intro/outro already added
by hand) and does everything from there automatically:

  1. Download the MP4
  2. Extract MP3 audio
  3. Transcribe with Whisper (replaces manual tactiq.io step)
  4. Generate a YouTube blurb with the Claude API (replaces manual copy/paste
     into Claude chat)
  5. Upload MP4 + MP3 to Cloudflare R2
  6. Add a new <item> to the podcast RSS feed and re-upload it
  7. Email you the blurb, YouTube link, and file locations, so you can
     finish the manual steps (YouTube details, Planning Center entry)

Usage:
    python pipeline.py "https://youtu.be/XXXXXXXXX" \
        --title "The Reality of Grace" \
        --speaker "Andrew Cartledge" \
        --sermon-date 2026-07-19

Requires config.env in the same folder (copy config.example.env and fill it in).
"""

import argparse
import mimetypes
import os
import random
import re
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator

WORKDIR = Path(__file__).parent
DOWNLOADS = WORKDIR / "downloads"
FEED_STATE_FILE = WORKDIR / "feed_items.json"  # local record of published episodes
RUN_HISTORY_FILE = WORKDIR / "run_history.json"  # record of every run attempt, for the status page

load_dotenv(WORKDIR / "config.env")


def env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        sys.exit(f"Missing required config value: {key} (check config.env)")
    return val


# ---------------------------------------------------------------------------
# Step 1-2: download video, extract audio
# ---------------------------------------------------------------------------

def download_and_extract(youtube_url: str, slug: str, source_key: str = None) -> Path:
    """Gets the MP4 either from a manually-uploaded R2 object (source_key) or,
    if not provided, by downloading it from YouTube with yt-dlp. Either way,
    extracts the audio to MP3, deletes the MP4, and returns the MP3 path.
    The MP4 is never uploaded or kept — YouTube is already the permanent
    host for the video itself."""
    DOWNLOADS.mkdir(exist_ok=True)
    mp4_path = DOWNLOADS / f"{slug}.mp4"
    mp3_path = DOWNLOADS / f"{slug}.mp3"

    if source_key:
        print(f"Downloading raw video from R2 ({source_key}) instead of YouTube...")
        client = get_r2_client()
        client.download_file(env("R2_BUCKET_NAME"), source_key, str(mp4_path))
    else:
        print("Downloading MP4 from YouTube...")
        yt_dlp_cmd = ["yt-dlp", "-f", "mp4", "-o", str(mp4_path), "--remote-components", "ejs:github"]
        cookies_path = WORKDIR / "youtube_cookies.txt"
        if cookies_path.exists():
            yt_dlp_cmd += ["--cookies", str(cookies_path)]
        yt_dlp_cmd.append(youtube_url)
        subprocess.run(yt_dlp_cmd, check=True)

    print("Extracting MP3 audio...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(mp4_path),
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            str(mp3_path),
        ],
        check=True,
        capture_output=True,
    )

    print("Deleting MP4 (YouTube already hosts the video permanently)...")
    mp4_path.unlink()

    if source_key:
        print(f"Removing raw upload from R2 ({source_key})...")
        client = get_r2_client()
        client.delete_object(Bucket=env("R2_BUCKET_NAME"), Key=source_key)

    return mp3_path


# ---------------------------------------------------------------------------
# Step 3: transcription (local Whisper — replaces tactiq.io)
# ---------------------------------------------------------------------------

def transcribe(mp3_path: Path) -> str:
    from faster_whisper import WhisperModel

    print("Transcribing (this can take a few minutes)...")
    model_size = env("WHISPER_MODEL_SIZE", default="small")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(mp3_path))
    transcript = " ".join(segment.text.strip() for segment in segments)
    return transcript


# ---------------------------------------------------------------------------
# Step 4: blurb generation via Claude API (replaces manual paste-into-chat)
# ---------------------------------------------------------------------------

BLURB_PROMPT_TEMPLATE = """You are a YouTube channel manager writing copy to \
promote a sermon video from the source content. Your job is not to summarise \
the content but to create curiosity and draw the viewer in so they click and \
watch.

The video is a sermon from Harvest Church, an Australian Christian Churches \
multi-campus church in the Wimmera region of Victoria, preached by {speaker}. \
The transcript below is the sermon itself.

Sermon title: {title}

Write a YouTube video description using this structure:

An opening hook of 2 to 3 sentences related to the video's core theme. Do \
not reveal the answer or resolution. For this hook, use this specific \
approach: {hook_style}

A short paragraph of 3 to 4 sentences that teases what the viewer will \
encounter without spoiling it. Use language that creates anticipation.

A one sentence call to action inviting the viewer to watch. For this line, \
use this specific approach: {closing_style} Do not end with "Hit play and \
find out..." or "Press play and see where this one takes you" — those are \
overused; use the closing technique above instead.

Then on a new line, write exactly the marker "===HASHTAGS===" on its own \
line, followed by a list of 10 to 15 relevant hashtags using title case, \
each on its own line. This marker must appear exactly once and nowhere \
else in your response.

Tone: warm, direct, and conversational. Avoid Christian cliche phrases like \
"life-changing" or "powerful message." Write as if you are talking to someone \
who is spiritually curious but not necessarily a regular churchgoer. Do not \
include timestamps, links, or any placeholder text. Do not use em dashes \
anywhere; use commas, colons, or separate sentences instead. Do not use any \
markdown formatting (no asterisks, no headers), and do not include any \
leading meta text like "Here are the show notes for this episode:" — output \
pure prose ready to publish as-is.
{recent_openings_section}
Transcript:
{transcript}
"""

# Rotated to force variety in the opening hook and closing line, since
# leaving this to chance tends to converge on the same patterns every time
# ("What if...?" openings, "Hit play and find out..." closings).
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


def _pick_style(style_list, last_used):
    """Picks a random style, avoiding whichever one was used last time (if
    known), so two consecutive episodes can't accidentally get the same
    style even by chance."""
    pool = [s for s in style_list if s != last_used] or style_list
    return random.choice(pool)


def generate_blurb(transcript: str, title: str, speaker: str, recent_episodes: list = None) -> dict:
    """Returns {"blurb": ..., "hashtags": ..., "full": ..., "opening_style": ...,
    "closing_style": ...} — "blurb" is the hook/teaser/CTA text with no
    hashtags (used for the podcast feed description), "hashtags" is just
    the hashtag list, and "full" is both combined (used for YouTube, where
    hashtags are wanted). opening_style/closing_style record which style
    was used, so the next run can avoid repeating it immediately.

    recent_episodes: the last several episode dicts (most recent last), used
    to steer the model away from repeating the same opening pattern, and to
    look up the most recently used opening/closing styles."""
    import anthropic

    print("Generating blurb via Claude API...")
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))

    last_opening_style = None
    last_closing_style = None
    if recent_episodes:
        last_ep = recent_episodes[-1]
        last_opening_style = last_ep.get("opening_style")
        last_closing_style = last_ep.get("closing_style")

    hook_style = _pick_style(OPENING_STYLES, last_opening_style)
    closing_style = _pick_style(CLOSING_STYLES, last_closing_style)

    recent_openings_section = ""
    if recent_episodes:
        openings = []
        for ep in recent_episodes[-10:]:
            first_line = ep.get("blurb", "").strip().split("\n")[0].strip()
            if first_line:
                openings.append(f"- {first_line}")
        if openings:
            recent_openings_section = (
                "\nHere are the opening lines from the last several episode "
                "descriptions. Your opening must be clearly different in "
                "structure and wording from every one of these — do not "
                "reuse the same phrasing, sentence pattern, or rhythm:\n"
                + "\n".join(openings) + "\n"
            )

    prompt = BLURB_PROMPT_TEMPLATE.format(
        title=title,
        speaker=speaker,
        transcript=transcript,
        hook_style=hook_style,
        closing_style=closing_style,
        recent_openings_section=recent_openings_section,
    )
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    full_text = message.content[0].text.strip()

    marker = "===HASHTAGS==="
    if marker in full_text:
        blurb_text, hashtags_text = full_text.split(marker, 1)
        blurb_text = blurb_text.strip()
        hashtags_text = hashtags_text.strip()
        full_clean = f"{blurb_text}\n\n{hashtags_text}"
    else:
        # Fallback if the model ever omits the marker: use the whole thing
        # as the blurb and leave hashtags empty rather than guessing.
        blurb_text = full_text
        hashtags_text = ""
        full_clean = full_text

    return {
        "blurb": blurb_text,
        "hashtags": hashtags_text,
        "full": full_clean,
        "opening_style": hook_style,
        "closing_style": closing_style,
    }


# ---------------------------------------------------------------------------
# Step 5: upload to Cloudflare R2
# ---------------------------------------------------------------------------

def get_r2_client():
    account_id = env("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def upload_to_r2(local_path: Path, key: str) -> str:
    client = get_r2_client()
    bucket = env("R2_BUCKET_NAME")
    content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    print(f"Uploading {local_path.name} to R2...")
    client.upload_file(
        str(local_path), bucket, key,
        ExtraArgs={"ContentType": content_type, "ACL": "public-read"},
    )
    base_url = env("R2_PUBLIC_BASE_URL").rstrip("/")
    return f"{base_url}/{key}"


def find_episode_image_url(source_key: str) -> str | None:
    """Looks for a thumbnail PNG uploaded alongside the raw video: same
    filename (minus extension) as source_key, inside an images/ folder in
    the bucket. Returns its public URL if found, or None if there isn't one
    (the feed will fall back to the podcast's default cover art)."""
    if not source_key:
        return None
    base_name = Path(source_key).stem
    image_key = f"images/{base_name}.png"
    client = get_r2_client()
    bucket = env("R2_BUCKET_NAME")
    try:
        client.head_object(Bucket=bucket, Key=image_key)
    except Exception:
        print(f"No episode thumbnail found at {image_key} — using default podcast artwork.")
        return None
    base_url = env("R2_PUBLIC_BASE_URL").rstrip("/")
    print(f"Found episode thumbnail: {image_key}")
    return f"{base_url}/{image_key}"


# ---------------------------------------------------------------------------
# Step 6: RSS feed — build from scratch each run using the local episode log
# ---------------------------------------------------------------------------

import json


def load_episode_log() -> list[dict]:
    if FEED_STATE_FILE.exists():
        return json.loads(FEED_STATE_FILE.read_text())
    return []


def save_episode_log(episodes: list[dict]) -> None:
    FEED_STATE_FILE.write_text(json.dumps(episodes, indent=2))


def load_run_history() -> list[dict]:
    if RUN_HISTORY_FILE.exists():
        return json.loads(RUN_HISTORY_FILE.read_text())
    return []


def record_run(status: str, title: str, sermon_date: str, speaker: str = None,
                detail: str = None, run_url: str = None) -> None:
    """Appends one entry to the run history, used by the status page.
    status is one of: "success", "duplicate_skipped". (Failures are recorded
    separately at the workflow level, since a failure can happen before this
    script even starts, e.g. if pip install itself fails.)"""
    history = load_run_history()
    history.append({
        "status": status,
        "title": title,
        "sermon_date": sermon_date,
        "speaker": speaker,
        "detail": detail,
        "run_url": run_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    RUN_HISTORY_FILE.write_text(json.dumps(history, indent=2))


def find_duplicate_episode(episodes: list[dict], title: str, sermon_date: str) -> dict | None:
    """Checks whether an episode with the same title and date has already
    been processed, so the same sermon never gets published twice even if
    the form is accidentally submitted more than once."""
    normalized_title = title.strip().lower()
    for ep in episodes:
        if ep.get("title", "").strip().lower() == normalized_title and \
                ep.get("pub_date", "").startswith(sermon_date):
            return ep
    return None


def send_duplicate_notice_email(title: str, speaker: str, sermon_date: str, existing_mp3_url: str) -> None:
    print("Duplicate detected — sending notice email instead of processing.")
    body = f"""This sermon looks like it may already have been processed, so nothing new was uploaded or added to the feed.

Title: {title}
Speaker: {speaker}
Sermon date: {sermon_date}

An episode with this same title and date is already in the feed:
{existing_mp3_url}

If this really is a new, different sermon, try submitting again with a
slightly different title (e.g. include the campus or series name), since
the duplicate check matches on title and date together.
"""
    msg = MIMEText(body)
    msg["Subject"] = f"Skipped (looks like a duplicate): {title}"
    msg["From"] = env("EMAIL_FROM")
    msg["To"] = env("EMAIL_TO")
    with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", default="587"))) as server:
        server.starttls()
        server.login(env("SMTP_USERNAME"), env("SMTP_PASSWORD"))
        server.send_message(msg)


def build_and_upload_feed(episodes: list[dict]) -> str:
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(env("PODCAST_TITLE"))
    fg.author({"name": env("PODCAST_AUTHOR")})
    fg.podcast.itunes_author(env("PODCAST_AUTHOR"))
    fg.description(env("PODCAST_DESCRIPTION"))
    fg.link(href=env("PODCAST_WEBSITE"), rel="alternate")
    fg.language(env("PODCAST_LANGUAGE", default="en-au"))
    fg.podcast.itunes_image(env("PODCAST_IMAGE_URL"))
    fg.podcast.itunes_category(env("PODCAST_CATEGORY", default="Religion & Spirituality"))
    fg.podcast.itunes_explicit(
        "yes" if env("PODCAST_EXPLICIT", default="false").lower() == "true" else "no"
    )

    for ep in sorted(episodes, key=lambda e: e["pub_date"]):
        fe = fg.add_entry()
        fe.id(ep["mp3_url"])
        fe.title(ep["title"])
        fe.description(ep["blurb"])
        fe.enclosure(ep["mp3_url"], str(ep["filesize"]), "audio/mpeg")
        fe.pubDate(ep["pub_date"])
        fe.podcast.itunes_author(ep.get("speaker", env("PODCAST_AUTHOR")))
        if ep.get("image_url"):
            fe.podcast.itunes_image(ep["image_url"])

    feed_path = WORKDIR / "feed.xml"
    fg.rss_file(str(feed_path))

    feed_url = upload_to_r2(feed_path, "feed.xml")
    return feed_url


# ---------------------------------------------------------------------------
# Step 7: email notification
# ---------------------------------------------------------------------------

def send_notification_email(context: dict) -> None:
    print("Sending notification email...")

    youtube_edit_line = context.get("youtube_edit_url") or context.get("youtube_url") or "(not yet published)"
    pco_edit_line = context.get("pco_edit_url") or context.get("pco_episode_url") or "(Planning Center episode was NOT created automatically — add this one manually)"

    body = f"""CONGRATULATIONS!!!

Your recent upload has succeeded

Title: {context['title']}
Speaker: {context['speaker']}
Sermon date: {context['sermon_date']}

YouTube clip: {context['youtube_url']}

Hosted MP3: {context['mp3_url']}

Podcast RSS feed: {context['feed_url']}

Episode thumbnail: {context['image_url'] if context.get('image_url') else '(none found — using default podcast artwork)'}

BUT THERE IS STILL WORK TO DO!

1. First grab this blurb and copy it


--- Blurb ---
{context['blurb']}

2. Then go to the YouTube link below and paste it in the description (while you are there, please make sure the video is in all the right playlists)


YOUTUBE EDIT URL
{youtube_edit_line}

3. Then go to the Planning Center link below and enter the speaker name (we will automate this one day but for now it's on you!)


PLANNING CENTER EPISODE EDIT URL
{pco_edit_line}

(And just in case you forgot) - {context['speaker']}


WELL DONE!!

Now go and grab yourself a sweet treat as a reward and pat Ps Andrew on the back for making your life easier.

Yours Truly,


Claude
"""
    msg = MIMEText(body)
    msg["Subject"] = f"Sermon processed: {context['title']}"
    msg["From"] = env("EMAIL_FROM")
    msg["To"] = env("EMAIL_TO")

    with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", default="587"))) as server:
        server.starttls()
        server.login(env("SMTP_USERNAME"), env("SMTP_PASSWORD"))
        server.send_message(msg)


# ---------------------------------------------------------------------------
# Step: create the Planning Center Publishing episode
# ---------------------------------------------------------------------------

PCO_BASE = "https://api.planningcenteronline.com/publishing/v2"
PCO_UPLOAD_URL = "https://upload.planningcenteronline.com/v2/files"
PCO_CHANNEL_ID = "28229"  # Sunday Sermons

SPEAKER_IDS = {
    "Ps Andrew Cartledge": "180515070",
    "Ps Rachel Cartledge": "180594564",
    "Ps Keith Ainge": "185233127",
    "Ps Caleb McLaughlin": "180595325",
    "Ps Ruth Emmerson": "185233241",
    "Ps Ron Spence": "185233554",
    "Ps Greg McKinnon": "180592588",
    "Guest Speaker": "190362143",
}


def pco_auth():
    return (env("PCO_CLIENT_ID"), env("PCO_SECRET"))


def upload_file_to_pco(file_url: str) -> str | None:
    """Downloads a file from a public URL (e.g. the thumbnail sitting on R2)
    and re-uploads it to Planning Center's Uploads API, returning the file
    UUID needed to attach it as episode art. Returns None on any failure —
    a missing thumbnail shouldn't block the whole episode from being
    created."""
    import requests

    try:
        img_resp = requests.get(file_url, timeout=30)
        img_resp.raise_for_status()
        upload_resp = requests.post(
            PCO_UPLOAD_URL,
            auth=pco_auth(),
            files={"file": ("thumbnail.png", img_resp.content, "image/png")},
            timeout=30,
        )
        upload_resp.raise_for_status()
        return upload_resp.json()["data"][0]["id"]
    except Exception as e:
        print(f"Warning: failed to upload thumbnail to Planning Center ({e}). Continuing without it.")
        return None


def create_planning_center_episode(title: str, speaker: str, sermon_date: str,
                                    blurb: str, video_url: str, audio_url: str,
                                    image_url: str = None, series_id: str = None) -> dict:
    """Creates the episode in Planning Center Publishing (Sunday Sermons
    channel), matching every field verified in testing: title, description,
    series, video/audio links, and correct prerecorded availability at 12pm
    on the sermon date. The channel's auto-assigned default live time is
    deleted (not replaced — replacing it was found to silently reset
    stream_type and never actually carried the real video anyway).

    Speaker assignment is NOT done here — Planning Center's API does not
    support creating that link (confirmed: no POST endpoint exists for it),
    so it stays a one-click manual step, called out in the notification
    email instead.

    Returns {"episode_url": ..., "speaker_name": ...} on success. Raises on
    failure — the caller decides whether that should be fatal to the whole
    run (it shouldn't be, since the podcast side already succeeded by this
    point)."""
    import requests

    print("Creating Planning Center Publishing episode...")

    art_uuid = upload_file_to_pco(image_url) if image_url else None

    published_at = f"{sermon_date}T12:00:00+10:00"

    attributes = {
        "title": title,
        "description": blurb,
        "video_url": video_url,
        "library_video_url": video_url,
        "library_audio_url": audio_url,
        "published_to_library_at": published_at,
        "stream_type": "prerecorded",
    }
    if series_id:
        attributes["series_id"] = series_id
    if art_uuid:
        attributes["art"] = art_uuid

    payload = {
        "data": {
            "type": "Episode",
            "attributes": attributes,
            "relationships": {
                "channel": {"data": {"type": "Channel", "id": PCO_CHANNEL_ID}}
            },
        }
    }

    resp = requests.post(
        f"{PCO_BASE}/episodes",
        auth=pco_auth(),
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    episode = resp.json()["data"]
    episode_id = episode["id"]

    # New episodes get an auto-assigned live time pointing at the channel's
    # generic livestream. Testing confirmed creating a replacement here does
    # two bad things: it silently resets stream_type back to
    # "channel_default_livestream" as a side effect, and it never actually
    # carries our specific video_url anyway (it always inherits the
    # channel's generic livestream embed regardless of what's submitted).
    # So: delete the auto-assigned one and stop there — don't create a
    # replacement — then re-assert stream_type defensively, since that's
    # the only thing that reliably keeps it correct.
    existing_times = requests.get(f"{PCO_BASE}/episodes/{episode_id}/episode_times", auth=pco_auth(), timeout=30)
    if existing_times.ok:
        for item in existing_times.json().get("data", []):
            requests.delete(f"{PCO_BASE}/episodes/{episode_id}/episode_times/{item['id']}", auth=pco_auth(), timeout=30)

    requests.patch(
        f"{PCO_BASE}/episodes/{episode_id}",
        auth=pco_auth(),
        headers={"Content-Type": "application/json"},
        json={"data": {"type": "Episode", "id": episode_id, "attributes": {"stream_type": "prerecorded"}}},
        timeout=30,
    )

    return {
        "episode_url": episode["attributes"]["church_center_url"],
        "edit_url": f"https://publishing.planningcenteronline.com/sermons/episodes/{episode_id}/edit",
        "speaker_name": speaker,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def extract_youtube_video_id(url: str) -> str | None:
    """Handles the common YouTube URL formats: youtu.be/ID, youtube.com/watch?v=ID,
    youtube.com/shorts/ID, m.youtube.com/watch?v=ID. Returns None if it can't
    confidently extract an ID, rather than guessing."""
    if not url:
        return None
    patterns = [
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def slugify(text: str) -> str:
    return "-".join(text.lower().split())[:60]


def main():
    parser = argparse.ArgumentParser(description="Sermon repurposing pipeline")
    parser.add_argument("youtube_url", help="URL of the finished, edited YouTube clip (for reference/notification)")
    parser.add_argument("--title", required=True, help="Sermon title")
    parser.add_argument("--speaker", required=True, help="Speaker name")
    parser.add_argument("--sermon-date", required=True, help="Sunday date, YYYY-MM-DD")
    parser.add_argument("--source-file", default=None,
                         help="R2 object key of a manually-uploaded raw video "
                              "(e.g. raw-uploads/sermon.mp4). If given, this is "
                              "used instead of downloading via yt-dlp.")
    parser.add_argument("--series-id", default=None,
                         help="Planning Center Publishing series ID, or blank for no series.")
    args = parser.parse_args()

    slug = f"{args.sermon_date}-{slugify(args.title)}"

    episodes = load_episode_log()
    existing = find_duplicate_episode(episodes, args.title, args.sermon_date)
    if existing:
        send_duplicate_notice_email(args.title, args.speaker, args.sermon_date, existing.get("mp3_url", "unknown"))
        record_run("duplicate_skipped", args.title, args.sermon_date, args.speaker,
                   detail="Matching title and date already in feed_items.json")
        print("Duplicate detected. Nothing was processed. Exiting cleanly.")
        return

    mp3_path = download_and_extract(args.youtube_url, slug, source_key=args.source_file)
    transcript = transcribe(mp3_path)
    blurb_parts = generate_blurb(transcript, args.title, args.speaker, recent_episodes=episodes)

    mp3_url = upload_to_r2(mp3_path, f"audio/{slug}.mp3")
    image_url = find_episode_image_url(args.source_file)

    episodes.append({
        "title": args.title,
        "speaker": args.speaker,
        "blurb": blurb_parts["blurb"],  # no hashtags — this is what podcast apps show
        "mp3_url": mp3_url,
        "filesize": mp3_path.stat().st_size,
        "pub_date": datetime.strptime(args.sermon_date, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc).isoformat(),
        "image_url": image_url,
        "opening_style": blurb_parts["opening_style"],
        "closing_style": blurb_parts["closing_style"],
    })
    save_episode_log(episodes)
    feed_url = build_and_upload_feed(episodes)

    pco_result = None
    try:
        pco_result = create_planning_center_episode(
            title=args.title,
            speaker=args.speaker,
            sermon_date=args.sermon_date,
            blurb=blurb_parts["blurb"],
            video_url=args.youtube_url,
            audio_url=mp3_url,
            image_url=image_url,
            series_id=args.series_id,
        )
        print(f"Planning Center episode created: {pco_result['episode_url']}")
    except Exception as e:
        print(f"Warning: Planning Center episode creation failed ({e}). "
              f"The podcast episode is still live — this just needs to be added "
              f"to Planning Center manually.")

    youtube_video_id = extract_youtube_video_id(args.youtube_url)
    youtube_edit_url = f"https://studio.youtube.com/video/{youtube_video_id}/edit" if youtube_video_id else None

    send_notification_email({
        "title": args.title,
        "speaker": args.speaker,
        "sermon_date": args.sermon_date,
        "youtube_url": args.youtube_url,
        "youtube_edit_url": youtube_edit_url,
        "mp3_url": mp3_url,
        "feed_url": feed_url,
        "blurb": blurb_parts["full"],  # with hashtags — this is what goes on YouTube
        "image_url": image_url,
        "pco_episode_url": pco_result["episode_url"] if pco_result else None,
        "pco_edit_url": pco_result["edit_url"] if pco_result else None,
    })

    print("\nDone.")
    print(f"Feed URL (submit this once to Apple Podcasts Connect / Spotify for Podcasters): {feed_url}")

    record_run("success", args.title, args.sermon_date, args.speaker, detail=mp3_url)


if __name__ == "__main__":
    main()
