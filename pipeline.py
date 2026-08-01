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
    return f"{base_url}/{key}"


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


def build_and_upload_feed(episodes: list[dict]) -> str:
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(env("PODCAST_TITLE"))
    fg.author({"name": env("PODCAST_AUTHOR")})
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
    body = f"""New sermon processed and hosted.

Title: {context['title']}
Speaker: {context['speaker']}
Sermon date: {context['sermon_date']}

YouTube clip: {context['youtube_url'] if context['youtube_url'] else 'Not yet published — add the YouTube link when available'}
Hosted MP3: {context['mp3_url']}
Podcast RSS feed: {context['feed_url']}
Episode thumbnail: {context['image_url'] if context.get('image_url') else 'None found — using default podcast artwork. Upload images/<filename>.png to R2 alongside the video to set one.'}

--- Blurb ---
{context['blurb']}

Remaining manual steps:
- Update the YouTube video title, speaker and description with the blurb above
- Create the Planning Center sermon entry (title, speaker, blurb, date, media link)
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
# Orchestration
# ---------------------------------------------------------------------------

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
    args = parser.parse_args()

    slug = f"{args.sermon_date}-{slugify(args.title)}"

    mp3_path = download_and_extract(args.youtube_url, slug, source_key=args.source_file)
    transcript = transcribe(mp3_path)
    episodes = load_episode_log()
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

    send_notification_email({
        "title": args.title,
        "speaker": args.speaker,
        "sermon_date": args.sermon_date,
        "youtube_url": args.youtube_url,
        "mp3_url": mp3_url,
        "feed_url": feed_url,
        "blurb": blurb_parts["full"],  # with hashtags — this is what goes on YouTube
        "image_url": image_url,
    })

    print("\nDone.")
    print(f"Feed URL (submit this once to Apple Podcasts Connect / Spotify for Podcasters): {feed_url}")


if __name__ == "__main__":
    main()
