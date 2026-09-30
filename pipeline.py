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
  7. Create the Planning Center Publishing episode
  8. Queue the episode in pending_social.json. social_publisher.py (run
     every 30 minutes by the "Publish Social" workflow) then waits for it
     to appear on Spotify, posts to Facebook and Instagram, and emails you
     the blurb and every link

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

BLURB_PROMPT_TEMPLATE = """Write the YouTube description for this week's \
sermon video from Harvest Church, a multi-campus church in the Wimmera region \
of Victoria, Australia. The preacher was {speaker}. The sermon title is \
"{title}". The full transcript is at the bottom.

WHO IS WRITING
You're someone on the Harvest Church team who was in the room on Sunday and \
is telling a friend why this one is worth their time. Write like a real \
Australian person talking: Australian spelling, contractions (it's, you'll, \
he's), plain everyday words, sentences of different lengths. It should read \
like a person wrote it quickly and well, not like marketing copy.

WHAT TO SAY
Anchor the description in something concrete from the transcript: a story \
the preacher actually told, the Bible passage or character they worked \
from, a line they actually said (a short quote is fine), or a question they \
put to the room. Specific beats vague every time. Name real details instead \
of hinting at them ("there's a story about his dad's old ute" beats "there's \
a moment in here you won't forget"). Mention the preacher by the name given \
above. Don't summarise the whole sermon and don't give away its conclusion, \
but don't be coy either.

For this one, start from: {hook_style}

SHAPE
Two or three short paragraphs, 60 to 120 words in total. You can finish \
with a plain, low-key invitation to watch, or just stop when you've said \
enough. Don't finish on a one-word line, a slogan, or a clever \
fragment.

AVOID, BECAUSE THEY MAKE IT SOUND AI-WRITTEN
- Lists of three (three adjectives, three phrases, three stories in a row). \
Use one or two.
- "Not X, but Y" or "It's not about X, it's about Y" constructions.
- Opening with "What if", "Most people think/assume", "Have you ever", or \
"There's a version of".
- Asking a question and then answering it yourself.
- Symmetrical, echoing sentences, and piles of abstract nouns (identity, \
purpose, belonging, transformation).
- Vague teasers: "there's something here", "there's a moment in this \
sermon", "more than you might expect", "you won't hear X the same way again".
- These words and phrases: {banned_list}.
- Christian cliches ("life-changing", "powerful message", "on fire for God").
- Em dashes. Use commas, full stops or colons instead.
- Markdown, emojis, links, timestamps, or any intro like "Here's the \
description".

The last few descriptions we posted are below. Yours must not sound like \
them: different opening, different rhythm, different closing, none of their \
stock phrases.
{recent_section}

OUTPUT
The description text only. Then a line containing exactly ===HASHTAGS=== \
and then 10 to 15 relevant hashtags in title case, one per line. The marker \
must appear exactly once.

Transcript:
{transcript}
"""

# Words and phrases that had become the house style of the AI blurbs (counted
# across the first 196 episodes: "honest" appeared in 95 of them, "this one"
# in 77, "most people" in 61, "quietly" in 58, "unpack" in 52...). Any draft
# that uses one gets sent back once for a rewrite.
BANNED_PHRASES = [
    "honest", "this one", "most people", "quietly", "unpack", "uncomfortabl",
    "sit with", "sitting with", "let it land", "let it settle", "lands",
    "weave", "journey", "explore", "delve", "dive into", "dives into",
    "digs into", "dig into", "deep dive", "cuts", "powerful", "profound",
    "truly", "deeper", "stop you cold", "in the best way", "overlooked",
    "strangest", "what if", "there's something here", "there is something here",
    "a moment in", "than you might expect", "the same way again", "hit play",
    "press play", "game-changer", "resonate", "invites you", "challenges us",
    "reminds us", "at its core", "tapestry", "navigate", "embrace",
]

# Rotated so consecutive descriptions start from different kinds of material.
# Every option points at something real in the transcript rather than a
# writing trick, which is what makes the copy feel written by a person.
OPENING_STYLES = [
    "a specific story or illustration the preacher told, retold in a "
    "sentence or two in your own words",
    "a short line the preacher actually said, quoted, then why it stuck",
    "the Bible passage or character the sermon works from, described "
    "plainly as if to someone who hasn't read it",
    "a question the preacher asked the congregation, put to the reader",
    "a practical, everyday situation the sermon speaks into, described "
    "simply (at work, at home, in the car, at the footy)",
    "something the preacher admitted about themselves or their own life",
    "a plain, factual statement of what the sermon is about, no hook at all",
    "a detail from the sermon that surprised you, stated matter-of-factly",
    "who this sermon is for, described in one ordinary sentence",
    "what the preacher asked people to actually do this week",
]


def _pick_style(style_list, last_used):
    """Picks a random style, avoiding whichever one was used last time (if
    known), so two consecutive episodes can't accidentally get the same
    style even by chance."""
    pool = [s for s in style_list if s != last_used] or style_list
    return random.choice(pool)


def find_banned_phrases(text: str) -> list[str]:
    lower = text.lower().replace("’", "'")
    # \b so "lands" doesn't catch "islands", while stems like "uncomfortabl"
    # still catch "uncomfortable" and "uncomfortably"
    found = [p for p in BANNED_PHRASES if re.search(r"\b" + re.escape(p), lower)]
    if "—" in text or "–" in text:
        found.append("em dashes")
    return found


# Newest model first. If Anthropic ever retires a model name, the next one
# in the list is used, so a Sunday run never fails over the model.
BLURB_MODELS = ["claude-sonnet-5-5", "claude-sonnet-4-6"]


def _ask_claude(client, messages) -> str:
    import anthropic
    last_error = None
    for model in BLURB_MODELS:
        try:
            msg = client.messages.create(model=model, max_tokens=1200, messages=messages)
            return msg.content[0].text.strip()
        except anthropic.NotFoundError as e:
            print(f"Model {model} isn't available ({e}); trying the next one.")
            last_error = e
    raise last_error


def generate_blurb(transcript: str, title: str, speaker: str, recent_episodes: list = None) -> dict:
    """Returns {"blurb": ..., "hashtags": ..., "full": ..., "opening_style": ...,
    "closing_style": ...}. "blurb" has no hashtags (podcast feed and
    Facebook), "full" has them (YouTube). opening_style records which
    starting point was used so the next run avoids repeating it.

    recent_episodes: the episode log (most recent last). The last five
    blurbs are shown to the model as examples NOT to sound like.

    If the draft uses any banned phrase, it's sent back once with the
    specific phrases named, and the rewrite is used."""
    import anthropic

    print("Generating blurb via Claude API...")
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))

    last_opening_style = recent_episodes[-1].get("opening_style") if recent_episodes else None
    hook_style = _pick_style(OPENING_STYLES, last_opening_style)

    recent_section = ""
    if recent_episodes:
        recent = [ep.get("blurb", "").strip() for ep in recent_episodes[-5:] if ep.get("blurb")]
        if recent:
            recent_section = "\n" + "\n\n---\n\n".join(recent) + "\n"

    prompt = BLURB_PROMPT_TEMPLATE.format(
        title=title,
        speaker=speaker,
        transcript=transcript,
        hook_style=hook_style,
        banned_list=", ".join(f'"{p}"' for p in BANNED_PHRASES),
        recent_section=recent_section,
    )
    messages = [{"role": "user", "content": prompt}]
    full_text = _ask_claude(client, messages)

    problems = find_banned_phrases(full_text.split("===HASHTAGS===")[0])
    if problems:
        print(f"Draft used: {', '.join(problems)}. Asking for a rewrite...")
        messages += [
            {"role": "assistant", "content": full_text},
            {"role": "user", "content": (
                "That draft uses these, which are on the avoid list: "
                + ", ".join(problems)
                + ". Rewrite it without them, rephrasing naturally rather than "
                "swapping in synonyms. Keep the same output format, including "
                "the ===HASHTAGS=== marker and hashtags.")},
        ]
        full_text = _ask_claude(client, messages)
        still = find_banned_phrases(full_text.split("===HASHTAGS===")[0])
        if still:
            print(f"Note: rewrite still uses: {', '.join(still)}")

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
        "closing_style": None,
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
    pco_edit_line = context.get("pco_edit_url") or "(Planning Center episode was NOT created automatically, so add this one manually)"
    pco_public_line = context.get("pco_episode_url") or "(not available yet)"

    spotify_line = context.get("spotify_url") or "(not available yet)"
    if context.get("spotify_fallback"):
        spotify_line = ("(The episode hadn't appeared on Spotify after 24 hours, so the social "
                        "posts went out without a Spotify link.)")

    social_lines = []
    if "facebook_result" in context:
        social_lines.append(f"Facebook Page post: {context['facebook_result']}")
    if "instagram_result" in context:
        social_lines.append(f"Instagram post: {context['instagram_result']}")
    social_block = "\n".join(social_lines) + "\n\n" if social_lines else ""
    facebook_line = context.get("facebook_result") or "(the Facebook post wasn't created, so share the sermon into the Group by hand)"

    body = f"""CONGRATULATIONS!!!

Your recent upload has succeeded

Title: {context['title']}
Speaker: {context['speaker']}
Sermon date: {context['sermon_date']}

Church Center (Planning Center) episode: {pco_public_line}

Spotify episode: {spotify_line}

YouTube clip: {context['youtube_url']}

Hosted MP3: {context['mp3_url']}

Podcast RSS feed: {context['feed_url']}

Episode thumbnail: {context['image_url'] if context.get('image_url') else '(none found — using default podcast artwork)'}

{social_block}BUT THERE IS STILL WORK TO DO!

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

4. Then go to the Facebook post below, hit Share and share it into the Harvest Church Group (Facebook doesn't let us automate this one)


FACEBOOK POST URL
{facebook_line}


WELL DONE!!

Now go and grab yourself a sweet treat as a reward and pat Ps Andrew on the back for making your life easier.

Yours Truly,


Claude
"""
    msg = MIMEText(body)
    msg["Subject"] = f"{context.get('subject_prefix', '')}Sermon processed: {context['title']}"
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

    # The public Church Center link. It isn't always filled in on the
    # create response, so ask Planning Center for it again if it's missing.
    # (If it's still missing, social_publisher.py tries again later.)
    public_url = episode["attributes"].get("church_center_url")
    if not public_url:
        public_url = get_pco_public_url(episode_id)

    return {
        "episode_id": episode_id,
        "episode_url": public_url,
        "edit_url": f"https://publishing.planningcenteronline.com/sermons/episodes/{episode_id}/edit",
        "speaker_name": speaker,
    }


def get_pco_public_url(episode_id: str) -> str | None:
    """Fetches the public Church Center URL for a Planning Center episode.
    Returns None if Planning Center hasn't assigned one (or the call fails)."""
    import requests

    try:
        resp = requests.get(f"{PCO_BASE}/episodes/{episode_id}", auth=pco_auth(), timeout=30)
        resp.raise_for_status()
        return resp.json()["data"]["attributes"].get("church_center_url")
    except Exception as e:
        print(f"Warning: couldn't fetch the Church Center URL for episode {episode_id} ({e}).")
        return None


# ---------------------------------------------------------------------------
# Step: queue the episode for the Spotify check, social posts and email
# ---------------------------------------------------------------------------

PENDING_SOCIAL_FILE = WORKDIR / "pending_social.json"


def queue_for_social(context: dict) -> None:
    pending = json.loads(PENDING_SOCIAL_FILE.read_text()) if PENDING_SOCIAL_FILE.exists() else []
    context = dict(context)
    context["queued_at"] = datetime.now(timezone.utc).isoformat()
    context["done"] = {"facebook": False, "instagram": False, "email": False}
    context["failures"] = 0
    pending.append(context)
    PENDING_SOCIAL_FILE.write_text(json.dumps(pending, indent=2))
    print("Queued for Spotify check, social posts and completion email (pending_social.json).")


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
        r"youtube\.com/live/([A-Za-z0-9_-]{11})",
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

    # The completion email is NOT sent here any more. Spotify takes a while
    # to pick up a new episode from the RSS feed, so the episode is queued in
    # pending_social.json instead. The "Publish Social" workflow checks the
    # queue every 30 minutes, and once the episode shows up on Spotify it
    # posts to Facebook and Instagram and sends the completion email with
    # every link included (see social_publisher.py).
    queue_for_social({
        "title": args.title,
        "speaker": args.speaker,
        "sermon_date": args.sermon_date,
        "slug": slug,
        "youtube_url": args.youtube_url,
        "youtube_edit_url": youtube_edit_url,
        "mp3_url": mp3_url,
        "feed_url": feed_url,
        "blurb": blurb_parts["blurb"],        # no hashtags: Facebook / Instagram
        "blurb_full": blurb_parts["full"],    # with hashtags: YouTube (goes in the email)
        "image_url": image_url,
        "pco_episode_id": pco_result["episode_id"] if pco_result else None,
        "pco_episode_url": pco_result["episode_url"] if pco_result else None,
        "pco_edit_url": pco_result["edit_url"] if pco_result else None,
    })

    print("\nDone.")
    print(f"Feed URL (submit this once to Apple Podcasts Connect / Spotify for Podcasters): {feed_url}")

    record_run("success", args.title, args.sermon_date, args.speaker, detail=mp3_url)


if __name__ == "__main__":
    main()
