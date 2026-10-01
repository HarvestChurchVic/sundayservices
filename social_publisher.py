#!/usr/bin/env python3
"""
Harvest Church: Facebook video, links comment, Spotify check and completion email.

pipeline.py doesn't send the completion email straight away. It adds the new
episode to pending_social.json instead, and leaves the uploaded sermon video
in R2. This script is run by the "Publish Social" GitHub Actions workflow
(straight after each Process Sermon run, then every 30 minutes). For each
queued episode it:

  1. Uploads the full sermon video to the Facebook Page straight away, with
     the blurb as its description (no hashtags, no links in the post, so it
     never counts towards Facebook's limit on link posts)
  2. Waits for Facebook to finish processing the video, then deletes the
     video from R2
  3. Looks the episode up on Spotify (title match, released within a few
     days of the sermon date). After SPOTIFY_WAIT_HOURS it carries on
     without the Spotify link, so nothing waits forever.
  4. Adds a comment from the Page with the links: Church Center, then
     Spotify, then YouTube
  5. Sends the completion email with every link, including the Facebook
     post so it can be shared into the Facebook Group
  6. Moves the episode from pending_social.json to social_history.json

If an episode has no video (say it was processed some other way), step 1
posts the thumbnail as a photo instead, and everything else is the same.

Each step is recorded separately, so if one fails the next run only retries
that step: nobody gets two emails or a doubled-up post. After MAX_FAILURES
failed runs the episode is dropped from the queue and a warning email is sent.

Usage:
    python social_publisher.py                     # normal run (what the schedule does)
    python social_publisher.py --dry-run           # checks connections, previews the post
                                                   # and comment, posts/sends nothing
    python social_publisher.py --test-last-sermon  # real end-to-end test with a short clip
                                                   # (hidden post unless --public)
"""

import argparse
import io
import json
import re
import smtplib
import subprocess
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests
from PIL import Image

from pipeline import (
    WORKDIR,
    env,
    get_pco_public_url,
    get_r2_client,
    load_episode_log,
    send_notification_email,
)

PENDING_FILE = WORKDIR / "pending_social.json"
HISTORY_FILE = WORKDIR / "social_history.json"

GRAPH = "https://graph.facebook.com/v26.0"
GRAPH_VIDEO = "https://graph-video.facebook.com/v26.0"   # Facebook's host for video uploads
SPOTIFY_SHOW_ID = "4c3QXWv8nOjf2KIaAB91MU"  # Harvest Church Sunday Sermons
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
SPOTIFY_WAIT_HOURS = 24      # after this, post anyway without the Spotify link
SPOTIFY_DATE_WINDOW_DAYS = 3  # Spotify release date must be this close to the sermon date
MAX_FAILURES = 6              # failed runs (30 min apart) before giving up on an episode

# Instagram posting is switched off for now. All the Instagram code is still
# here. To turn it back on: change this to True, add the INSTAGRAM_USER_ID
# secret on GitHub, and uncomment the INSTAGRAM_USER_ID line in
# .github/workflows/publish-social.yml.
INSTAGRAM_ENABLED = False

LINK_LABELS = (
    "Watch on Church Center",
    "Listen on Spotify",
    "Watch on YouTube",
)

# Last line of the video's description, pointing people to the comment
COMMENT_POINTER = "Links to watch or listen on Church Center, Spotify and YouTube are in the comments."

VIDEO_READY_WAIT_MINUTES = 20   # how long the test waits for Facebook to process its clip


# ---------------------------------------------------------------------------
# Queue files
# ---------------------------------------------------------------------------

def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------

def normalise_title(text: str) -> str:
    """Lower-case, straighten curly quotes/dashes, drop punctuation and extra
    spaces, so "God's Provision" on Spotify matches "God’s provision" here."""
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return " ".join(text.split())


def spotify_recent_episodes() -> list[dict]:
    """Reads the newest episode from the show's public Spotify embed page.
    No Spotify account or login is needed: this is the same page that shows
    when a Spotify player is embedded on a website. It only lists the newest
    episode, which is all we need since the sermon we're waiting for is the
    newest one."""
    resp = requests.get(
        f"https://open.spotify.com/embed/show/{SPOTIFY_SHOW_ID}",
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-AU,en;q=0.9"},
        timeout=30,
    )
    resp.raise_for_status()
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not match:
        raise RuntimeError("Spotify's embed page has changed layout (no __NEXT_DATA__ found).")
    entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    if entity.get("type") != "episode" or not entity.get("id"):
        raise RuntimeError(f"Spotify's embed page has changed layout (unexpected entity: {entity.get('type')}).")
    return [{
        "name": entity.get("name") or entity.get("title", ""),
        "release_date": (entity.get("releaseDate") or {}).get("isoString", ""),
        "external_urls": {"spotify": f"https://open.spotify.com/episode/{entity['id']}"},
    }]


def find_spotify_episode(item: dict, spotify_episodes: list[dict]) -> str | None:
    """Returns the Spotify episode link for this sermon, or None if Spotify
    hasn't picked it up yet. Needs the title to match AND the release date to
    be near the sermon date, so a repeated title (e.g. two different
    sermons both called "My Church") can't match the older episode."""
    wanted = normalise_title(item["title"])
    sermon_day = date.fromisoformat(item["sermon_date"])
    for ep in spotify_episodes:
        if normalise_title(ep.get("name", "")) != wanted:
            continue
        try:
            released = date.fromisoformat(ep.get("release_date", "")[:10])
        except ValueError:
            continue
        if abs((released - sermon_day).days) <= SPOTIFY_DATE_WINDOW_DAYS:
            return ep["external_urls"]["spotify"]
    return None


# ---------------------------------------------------------------------------
# Post text
# ---------------------------------------------------------------------------

def strip_hashtags(text: str) -> str:
    """The stored blurb has no hashtags, but this makes sure of it."""
    text = re.sub(r"(?<!\w)#\w+", "", text)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def facebook_description(item: dict) -> str:
    """The text on the Facebook post itself: the blurb, then a pointer to the
    links comment. No links here, so the post isn't a "link post"."""
    return strip_hashtags(item["blurb"]) + "\n\n" + COMMENT_POINTER


def links_comment(item: dict) -> str:
    """The first comment: Church Center, Spotify, YouTube, in that order.
    Any link we don't have (e.g. Spotify after 24 hours) is left out."""
    links = [item.get("pco_episode_url"), item.get("spotify_url"), item.get("youtube_url")]
    lines = [f"{label}: {url}" for label, url in zip(LINK_LABELS, links)
             if url and str(url).startswith("http")]
    return "\n".join(lines)


def instagram_caption(item: dict) -> str:
    return strip_hashtags(item["blurb"])


# ---------------------------------------------------------------------------
# Thumbnail: Instagram only accepts JPEG, in a 4:5 to 1.91:1 shape
# ---------------------------------------------------------------------------

def thumbnail_url(item: dict) -> str:
    return item.get("image_url") or env("PODCAST_IMAGE_URL")


def social_jpeg_url(item: dict) -> str:
    """Downloads the thumbnail, converts it to a JPEG Instagram will accept
    (padding with black if the shape is outside Instagram's limits), uploads
    it to R2 and returns its public URL. Made once per episode. Only used by
    Instagram."""
    if item.get("jpeg_url"):
        return item["jpeg_url"]
    resp = requests.get(thumbnail_url(item), timeout=60)
    resp.raise_for_status()
    img = Image.open(io.BytesIO(resp.content)).convert("RGB")

    w, h = img.size
    ratio = w / h
    if ratio > 1.91:        # too wide: pad top and bottom
        canvas = Image.new("RGB", (w, round(w / 1.91)), "black")
        canvas.paste(img, (0, (canvas.height - h) // 2))
        img = canvas
    elif ratio < 0.8:       # too tall: pad left and right
        canvas = Image.new("RGB", (round(h * 0.8), h), "black")
        canvas.paste(img, ((canvas.width - w) // 2, 0))
        img = canvas
    if img.width > 1440:
        img = img.resize((1440, round(img.height * 1440 / img.width)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    key = f"social/{item['slug']}.jpg"
    get_r2_client().put_object(
        Bucket=env("R2_BUCKET_NAME"), Key=key, Body=buf.getvalue(),
        ContentType="image/jpeg", ACL="public-read",
    )
    item["jpeg_url"] = f"{env('R2_PUBLIC_BASE_URL').rstrip('/')}/{key}"
    return item["jpeg_url"]


# ---------------------------------------------------------------------------
# Facebook and Instagram (Meta Graph API)
# ---------------------------------------------------------------------------

def graph_call(method: str, path: str, **params) -> dict:
    params["access_token"] = env("META_PAGE_TOKEN")
    resp = requests.request(method, f"{GRAPH}/{path}", data=params if method == "POST" else None,
                            params=params if method == "GET" else None, timeout=60)
    body = resp.json() if resp.content else {}
    if not resp.ok or "error" in body:
        message = body.get("error", {}).get("message", resp.text[:300])
        raise RuntimeError(f"Meta API error on {path}: {message}")
    return body


def check_page_token() -> bool:
    """True if META_PAGE_TOKEN is the Page's own token. With a Page token,
    'me' is the Page itself; with a personal token, 'me' is the person."""
    return str(graph_call("GET", "me", fields="id").get("id")) == str(env("META_PAGE_ID"))


def _video_call(data: dict, files: dict = None, attempts: int = 3) -> dict:
    """One call to the Page's video upload endpoint, retried on network
    hiccups (a 2 GB upload makes hundreds of these)."""
    url = f"{GRAPH_VIDEO}/{env('META_PAGE_ID')}/videos"
    data = dict(data, access_token=env("META_PAGE_TOKEN"))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, data=data, files=files, timeout=300)
            body = resp.json() if resp.content else {}
            if resp.ok and "error" not in body:
                return body
            last = RuntimeError("Meta video upload error: "
                                + body.get("error", {}).get("message", resp.text[:300]))
            if resp.status_code < 500:
                break   # a real error, not worth retrying
        except requests.RequestException as e:
            last = e
        time.sleep(5 * attempt)
    raise last


def upload_video_to_facebook(video_key: str, title: str, description: str,
                             published: bool = True) -> str:
    """Uploads the sermon video from R2 to the Facebook Page in chunks
    (Facebook's resumable upload, which handles multi-GB files), streaming
    each chunk straight from R2 so the whole file never has to fit in
    memory. Returns the Facebook video ID."""
    client = get_r2_client()
    bucket = env("R2_BUCKET_NAME")
    size = client.head_object(Bucket=bucket, Key=video_key)["ContentLength"]
    print(f"Uploading video to Facebook ({size / 1e6:.0f} MB)...")

    start = _video_call({"upload_phase": "start", "file_size": str(size)})
    session, video_id = start["upload_session_id"], start["video_id"]
    s_off, e_off = int(start["start_offset"]), int(start["end_offset"])
    last_report = 0
    while s_off < e_off:
        chunk = client.get_object(Bucket=bucket, Key=video_key,
                                  Range=f"bytes={s_off}-{e_off - 1}")["Body"].read()
        r = _video_call({"upload_phase": "transfer", "upload_session_id": session,
                         "start_offset": str(s_off)},
                        files={"video_file_chunk": ("chunk.mp4", chunk, "application/octet-stream")})
        s_off, e_off = int(r["start_offset"]), int(r["end_offset"])
        if s_off - last_report >= size // 10 or s_off >= size:
            print(f"  {min(s_off, size) / size:.0%} uploaded")
            last_report = s_off

    fin = _video_call({"upload_phase": "finish", "upload_session_id": session,
                       "title": title, "description": description,
                       "published": "true" if published else "false"})
    if not fin.get("success"):
        raise RuntimeError(f"Facebook didn't confirm the video upload finished: {fin}")
    return str(video_id)


def facebook_video_status(video_id: str) -> tuple[str, str | None]:
    """Returns (status, link to the post). status is "ready", "processing"
    or "error"."""
    info = graph_call("GET", video_id, fields="status,permalink_url")
    status = (info.get("status") or {}).get("video_status", "processing")
    link = info.get("permalink_url")
    if link and link.startswith("/"):
        link = "https://www.facebook.com" + link
    if status == "ready":
        return "ready", link
    if status in ("error", "expired"):
        return "error", link
    return "processing", link


def post_photo_to_facebook(item: dict, published: bool = True) -> tuple[str, str]:
    """Fallback when there's no video: the thumbnail as a photo post, with
    the same description. Returns (post id, link to the post)."""
    params = {"url": thumbnail_url(item), "caption": facebook_description(item)}
    if not published:
        params["published"] = "false"
    result = graph_call("POST", f"{env('META_PAGE_ID')}/photos", **params)
    post_id = result.get("post_id") or result["id"]
    return post_id, f"https://www.facebook.com/{post_id}"


def post_links_comment(target_id: str, item: dict) -> None:
    """Adds the links as a comment from the Page on the video or photo post."""
    message = links_comment(item)
    if not message:
        print("No links to comment with, skipping the comment.")
        return
    graph_call("POST", f"{target_id}/comments", message=message)


def delete_source_video(item: dict) -> None:
    """Removes the uploaded sermon video from R2 once Facebook has its own
    copy. YouTube is the permanent home for the video."""
    key = item.get("video_key")
    if key:
        get_r2_client().delete_object(Bucket=env("R2_BUCKET_NAME"), Key=key)
        print(f"Removed the video from R2 ({key}).")
    item["video_deleted"] = True


def post_to_instagram(item: dict) -> str:
    """Two-step Instagram publish: create a media container from the JPEG,
    wait for Instagram to finish processing it, then publish it. Returns a
    link to the post."""
    ig_user = env("INSTAGRAM_USER_ID")
    container = graph_call("POST", f"{ig_user}/media",
                           image_url=social_jpeg_url(item), caption=instagram_caption(item))["id"]

    for _ in range(20):
        status = graph_call("GET", container, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram couldn't process the image (status {status}).")
        time.sleep(6)
    else:
        raise RuntimeError("Instagram took too long to process the image.")

    media_id = graph_call("POST", f"{ig_user}/media_publish", creation_id=container)["id"]
    try:
        return graph_call("GET", media_id, fields="permalink").get("permalink") or f"Published (media ID {media_id})"
    except Exception:
        return f"Published (media ID {media_id})"


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def send_plain_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = env("EMAIL_FROM")
    msg["To"] = env("EMAIL_TO")
    with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", default="587"))) as server:
        server.starttls()
        server.login(env("SMTP_USERNAME"), env("SMTP_PASSWORD"))
        server.send_message(msg)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def hours_since(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600


def _record_failure(item: dict, what: str, error: Exception) -> bool:
    """Counts a failed step. Returns True if we should give up on the step."""
    item["failures"] = item.get("failures", 0) + 1
    print(f"[{item['title']}] {what} failed ({item['failures']}/{MAX_FAILURES}): {error}")
    return item["failures"] >= MAX_FAILURES


def process_item(item: dict, spotify_episodes: list[dict] | None) -> bool:
    """Works through one queued episode. Returns True when it's finished
    (everything done), False if it should stay in the queue."""
    title = item["title"]
    done = item.setdefault("done", {})

    # 1. The Facebook post: the video, straight away
    if not done.get("facebook_post"):
        try:
            if item.get("video_key"):
                item["facebook_video_id"] = upload_video_to_facebook(
                    item["video_key"], title, facebook_description(item))
                item["facebook_comment_target"] = item["facebook_video_id"]
                print(f"[{title}] Video uploaded to Facebook (video {item['facebook_video_id']}). "
                      f"Facebook is processing it.")
            else:
                post_id, link = post_photo_to_facebook(item)
                item["facebook_comment_target"] = post_id
                item["facebook_result"] = link
                done["facebook_ready"] = True
                print(f"[{title}] No video for this sermon, posted the thumbnail instead: {link}")
            done["facebook_post"] = True
        except Exception as e:
            item["facebook_result"] = f"FAILED ({e})"
            if not _record_failure(item, "Facebook post", e):
                return False
            print(f"[{title}] Giving up on the Facebook post. Sending the email anyway.")
            done["facebook_post"] = done["facebook_ready"] = done["facebook_comment"] = True
            done["gave_up_facebook"] = True

    # 2. Wait for Facebook to finish processing the video
    if done.get("facebook_post") and not done.get("facebook_ready"):
        status, link = facebook_video_status(item["facebook_video_id"])
        if link:
            item["facebook_result"] = link
        if status == "ready":
            done["facebook_ready"] = True
            print(f"[{title}] Facebook has finished processing the video: {link}")
        elif status == "error":
            # Start the upload again next run
            e = RuntimeError("Facebook couldn't process the video")
            done["facebook_post"] = False
            if not _record_failure(item, "Facebook video processing", e):
                return False
            item["facebook_result"] = "FAILED (Facebook couldn't process the video)"
            done["facebook_post"] = done["facebook_ready"] = done["facebook_comment"] = True
            done["gave_up_facebook"] = True
        else:
            print(f"[{title}] Facebook is still processing the video. Will check again.")
            return False

    # Facebook has its own copy now, so the video can come out of R2
    if done.get("facebook_ready") and item.get("video_key") and not item.get("video_deleted"):
        try:
            delete_source_video(item)
        except Exception as e:
            print(f"[{title}] Couldn't remove the video from R2 ({e}). Will try again next run.")

    # 3. Spotify link
    if not item.get("spotify_url") and not item.get("spotify_fallback"):
        found = find_spotify_episode(item, spotify_episodes) if spotify_episodes is not None else None
        if found:
            item["spotify_url"] = found
            print(f"[{title}] Found on Spotify: {found}")
        elif hours_since(item["queued_at"]) >= SPOTIFY_WAIT_HOURS:
            item["spotify_url"] = None
            item["spotify_fallback"] = True
            print(f"[{title}] Not on Spotify after {SPOTIFY_WAIT_HOURS} hours, carrying on without the Spotify link.")
        else:
            print(f"[{title}] Not on Spotify yet (queued {hours_since(item['queued_at']):.1f} hours ago). Will check again.")
            return False

    # Public Church Center link (fetch again if it was missing at creation)
    if not item.get("pco_episode_url") and item.get("pco_episode_id"):
        item["pco_episode_url"] = get_pco_public_url(item["pco_episode_id"])

    # 4. The links comment
    if not done.get("facebook_comment"):
        try:
            post_links_comment(item["facebook_comment_target"], item)
            done["facebook_comment"] = True
            print(f"[{title}] Links comment added.")
        except Exception as e:
            if not _record_failure(item, "Links comment", e):
                return False
            print(f"[{title}] Giving up on the links comment. Sending the email anyway.")
            item["facebook_comment_failed"] = str(e)
            done["facebook_comment"] = True

    # Instagram (switched off for now, see INSTAGRAM_ENABLED at the top)
    if INSTAGRAM_ENABLED and not done.get("instagram"):
        try:
            item["instagram_result"] = post_to_instagram(item)
            done["instagram"] = True
            print(f"[{title}] Instagram post: {item['instagram_result']}")
        except Exception as e:
            item["instagram_result"] = f"FAILED ({e})"
            if not _record_failure(item, "Instagram post", e):
                return False
            done["instagram"] = True

    # 5. Completion email (once, after the links comment, so the post it
    #    links to is complete when people share it into the Group)
    if not done.get("email"):
        # The email's blurb is the YouTube one, which keeps its hashtags
        send_notification_email(dict(item, blurb=item.get("blurb_full") or item["blurb"]))
        done["email"] = True
        print(f"[{title}] Completion email sent.")

    return True


def dry_run() -> None:
    """Checks every connection and prints what would be posted for the most
    recent episode in the feed. Posts nothing and sends nothing."""
    ok = True
    latest = sorted(load_episode_log(), key=lambda e: e["pub_date"])[-1]
    item = {
        "title": latest["title"],
        "sermon_date": latest["pub_date"][:10],
        "blurb": latest["blurb"],
        "image_url": latest.get("image_url"),
        "pco_episode_url": "(Church Center link goes here)",
        "youtube_url": "(YouTube link goes here)",
    }
    print(f"DRY RUN using the latest episode: {item['title']} ({item['sermon_date']})\n")

    try:
        found = find_spotify_episode(item, spotify_recent_episodes())
        print(f"Spotify: connected. Episode link: {found or 'NOT FOUND (title/date did not match)'}")
        item["spotify_url"] = found
    except Exception as e:
        ok = False
        print(f"Spotify: FAILED ({e})")

    try:
        page = graph_call("GET", env("META_PAGE_ID"), fields="name")
        print(f"Facebook Page: connected to '{page.get('name')}'")
        if check_page_token():
            print("Facebook token: correct type (a Page token, so posts go out as the Page)")
        else:
            ok = False
            print("Facebook token: WRONG TYPE. META_PAGE_TOKEN is a personal (user) token, not the "
                  "Page's token, so posting will fail. Redo step A4 of SOCIAL_SETUP.md and use the "
                  "access_token shown inside the Harvest Church entry.")
    except Exception as e:
        ok = False
        print(f"Facebook Page: FAILED ({e})")

    if INSTAGRAM_ENABLED:
        try:
            ig = graph_call("GET", env("INSTAGRAM_USER_ID"), fields="username")
            print(f"Instagram: connected to @{ig.get('username')}")
        except Exception as e:
            ok = False
            print(f"Instagram: FAILED ({e})")
    else:
        print("Instagram: switched off (INSTAGRAM_ENABLED = False)")

    print("\n----- Facebook post -----")
    print(f"Video: the full sermon video, titled \"{item['title']}\"")
    print("Description:\n" + facebook_description(item))
    print("\n----- Links comment (added once the Spotify link is found) -----")
    print(links_comment(item) or "(no links)")
    if INSTAGRAM_ENABLED:
        print(f"\nInstagram image: {thumbnail_url(item)}")
        print("----- Instagram caption -----\n" + instagram_caption(item))
    if not ok:
        sys.exit("\nOne or more connections failed. See above.")


def find_pco_episode(title: str) -> dict | None:
    """Finds a Sunday Sermons episode in Planning Center by title. Pages
    through the whole channel (Planning Center doesn't reliably sort this
    list), and if the same title appears more than once, returns the one
    most recently made available."""
    from pipeline import PCO_BASE, PCO_CHANNEL_ID, pco_auth
    wanted = normalise_title(title)
    matches = []
    url = f"{PCO_BASE}/channels/{PCO_CHANNEL_ID}/episodes?per_page=100"
    pages = 0
    while url and pages < 20:
        resp = requests.get(url, auth=pco_auth(), timeout=30)
        resp.raise_for_status()
        body = resp.json()
        matches += [ep for ep in body.get("data", [])
                    if normalise_title(ep["attributes"].get("title") or "") == wanted]
        url = (body.get("links") or {}).get("next")
        pages += 1
    if not matches:
        return None
    return max(matches, key=lambda ep: ep["attributes"].get("published_to_library_at") or "")


def make_test_clip(item: dict) -> str:
    """Builds a short test video (the sermon thumbnail over the first 20
    seconds of the sermon audio), uploads it to R2 like a real sermon video
    and returns its R2 key. Used so the test runs through exactly the same
    upload code as a real Sunday, without needing the original video."""
    clip = WORKDIR / "test_clip.mp4"
    thumb = WORKDIR / "test_thumb.png"
    r = requests.get(thumbnail_url(item), timeout=60)
    r.raise_for_status()
    Image.open(io.BytesIO(r.content)).convert("RGB").save(thumb)
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(thumb), "-t", "20", "-i", item["mp3_url"],
        "-t", "20", "-vf", "scale=1280:-2", "-c:v", "libx264", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip),
    ], check=True, capture_output=True)
    key = f"raw-uploads/test-{int(time.time())}.mp4"
    get_r2_client().upload_file(str(clip), env("R2_BUCKET_NAME"), key,
                                ExtraArgs={"ContentType": "video/mp4"})
    clip.unlink(missing_ok=True)
    thumb.unlink(missing_ok=True)
    return key


def test_last_sermon(public: bool = False) -> None:
    """Real end-to-end test using the most recent sermon that has already
    been through the pipeline. Uses a 20-second clip instead of the full
    video (the original has been deleted), but otherwise runs the same
    steps as a real Sunday: video upload to Facebook (HIDDEN unless
    --public), wait for processing, links comment, remove the clip from R2,
    and the completion email marked [TEST]. Doesn't touch the queue files."""
    latest = sorted(load_episode_log(), key=lambda e: e["pub_date"])[-1]
    print(f"TEST using the latest sermon: {latest['title']} ({latest['pub_date'][:10]})\n")

    item = {
        "title": latest["title"],
        "speaker": latest.get("speaker", ""),
        "sermon_date": latest["pub_date"][:10],
        "slug": "test-" + re.sub(r"[^a-z0-9]+", "-", latest["title"].lower()).strip("-"),
        "blurb": latest["blurb"],
        "image_url": latest.get("image_url"),
        "mp3_url": latest["mp3_url"],
        "feed_url": f"{env('R2_PUBLIC_BASE_URL').rstrip('/')}/feed.xml",
        "subject_prefix": "[TEST] ",
    }

    ep = find_pco_episode(latest["title"])
    if ep:
        attrs = ep["attributes"]
        item["pco_episode_id"] = ep["id"]
        item["pco_episode_url"] = attrs.get("church_center_url") or get_pco_public_url(ep["id"])
        item["pco_edit_url"] = f"https://publishing.planningcenteronline.com/sermons/episodes/{ep['id']}/edit"
        item["youtube_url"] = attrs.get("library_video_url") or attrs.get("video_url")
        print(f"Planning Center: found episode {ep['id']}")
    else:
        sys.exit("Planning Center: couldn't find this sermon's episode by title, so the test has "
                 "stopped before posting anything.")
    print(f"Church Center link: {item.get('pco_episode_url')}")
    print(f"YouTube link: {item.get('youtube_url') or 'NOT FOUND'}")

    item["spotify_url"] = find_spotify_episode(item, spotify_recent_episodes())
    print(f"Spotify link: {item['spotify_url'] or 'NOT FOUND'}")

    if not check_page_token():
        sys.exit("\nMETA_PAGE_TOKEN is a personal (user) token, not the Page's token, so Facebook "
                 "won't let it post as the Page. Redo step A4 of SOCIAL_SETUP.md and use the "
                 "access_token shown inside the Harvest Church entry. Nothing was posted or emailed.")

    print("\nMaking a 20-second test clip...")
    item["video_key"] = make_test_clip(item)
    try:
        video_id = upload_video_to_facebook(item["video_key"], "[TEST] " + item["title"],
                                            facebook_description(item), published=public)
        print(f"Uploaded (video {video_id}). Waiting for Facebook to process it...")
        deadline = time.time() + VIDEO_READY_WAIT_MINUTES * 60
        while True:
            status, link = facebook_video_status(video_id)
            if status == "ready":
                break
            if status == "error":
                sys.exit("Facebook couldn't process the test video.")
            if time.time() > deadline:
                sys.exit(f"Facebook was still processing after {VIDEO_READY_WAIT_MINUTES} minutes. "
                         f"On a real Sunday the workflow just checks again next run.")
            time.sleep(20)
        item["facebook_result"] = link or f"https://www.facebook.com/{video_id}"
        print(f"{'PUBLIC' if public else 'Hidden'} Facebook video post ready: {item['facebook_result']}")

        post_links_comment(video_id, item)
        print("Links comment added:\n" + links_comment(item))
    finally:
        delete_source_video(item)

    send_notification_email(item)
    print("\nTest completion email sent (subject starts with [TEST]).")
    if public:
        print("The test post is PUBLIC. Check it in a private window, then delete it from the Page.")


def main():
    parser = argparse.ArgumentParser(description="Spotify check, social posts and completion email")
    parser.add_argument("--dry-run", action="store_true", help="Check connections and preview posts only")
    parser.add_argument("--test-last-sermon", action="store_true",
                        help="Real test with a 20-second clip of the latest sermon: Facebook video + links "
                             "comment + [TEST] email (hidden unless --public)")
    parser.add_argument("--public", action="store_true",
                        help="With --test-last-sermon: make the test post public, like a real run")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return
    if args.test_last_sermon:
        test_last_sermon(public=args.public)
        return

    pending = load_json(PENDING_FILE, [])
    if not pending:
        print("Nothing queued.")
        return

    try:
        spotify_episodes = spotify_recent_episodes()
    except Exception as e:
        print(f"Warning: Spotify check failed this run ({e}).")
        spotify_episodes = None

    history = load_json(HISTORY_FILE, [])
    still_pending = []
    for item in pending:
        try:
            finished = process_item(item, spotify_episodes)
        except Exception as e:
            print(f"[{item.get('title')}] Unexpected error: {e}")
            item["failures"] = item.get("failures", 0) + 1
            finished = False
            if item["failures"] >= MAX_FAILURES:
                send_plain_email(
                    f"Social posting gave up: {item.get('title')}",
                    f"The Spotify check / social posts / completion email for "
                    f"'{item.get('title')}' failed {MAX_FAILURES} times in a row and has been "
                    f"taken out of the queue.\n\nLast error: {e}\n\n"
                    f"Everything else (podcast feed, Planning Center) is already done. "
                    f"Check the 'Publish Social' workflow runs on GitHub for details.",
                )
                item["gave_up_at"] = datetime.now(timezone.utc).isoformat()
                finished = True
                if item.get("video_key") and not item.get("video_deleted"):
                    try:
                        delete_source_video(item)
                    except Exception:
                        pass

        if finished:
            item["finished_at"] = datetime.now(timezone.utc).isoformat()
            history.append(item)
        else:
            still_pending.append(item)

    save_json(PENDING_FILE, still_pending)
    save_json(HISTORY_FILE, history)


if __name__ == "__main__":
    main()
