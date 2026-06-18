# YouTube → Podcast (RSS → Spotify / Apple Podcasts)

Automatically converts a YouTube playlist into a podcast feed hosted on GitHub Pages.
Runs weekly via GitHub Actions. No server required. Free.

---

## How it works

```
YouTube Playlist
      │
      ▼ (YouTube Data API — checks for new videos)
  New videos only
      │
      ▼ (yt-dlp + ffmpeg — runs inside GitHub Actions)
  MP3 audio files
      │
      ▼ (GitHub Releases — free file hosting)
  Hosted audio URLs
      │
      ▼ (RSS XML — committed to GitHub Pages)
  feed.xml
      │
      ▼ (Spotify / Apple / any podcast app polls this URL)
  Your podcast
```

---

## One-time setup (~20 minutes)

### Step 1 — Create the GitHub repo

1. Go to https://github.com/new
2. Create a **public** repo (e.g. `my-podcast`)
3. Initialise it with a README so it's not empty

### Step 2 — Enable GitHub Pages

1. Go to your repo → **Settings** → **Pages**
2. Under "Source", select **Deploy from a branch**
3. Select branch: `main`, folder: `/ (root)`
4. Click **Save**
5. Your feed will live at: `https://YOUR_USERNAME.github.io/YOUR_REPO/feed.xml`

https://HarvestChurchVic.github.io/sundayservices/feed.xml

### Step 3 — Get a YouTube Data API key

1. Go to https://console.cloud.google.com
2. Create a new project (or use an existing one)
3. Navigate to **APIs & Services** → **Library**
4. Search for "YouTube Data API v3" and click **Enable**
5. Go to **APIs & Services** → **Credentials**
6. Click **Create Credentials** → **API Key**
7. Copy the key — you'll need it in Step 5

> The free quota is 10,000 units/day. Each playlist poll uses ~2–5 units. You're fine.

### Step 4 — Create a GitHub Personal Access Token

This is separate from the automatic `GITHUB_TOKEN` — it needs permission to create Releases
and upload assets to your podcast repo.

1. Go to https://github.com/settings/tokens?type=beta (Fine-grained tokens)
2. Click **Generate new token**
3. Set expiry to 1 year (or no expiry)
4. Under "Repository access", select **Only select repositories** → choose your podcast repo
5. Under "Permissions", enable:
   - **Contents** → Read and write
   - **Metadata** → Read-only (auto-selected)
6. Click **Generate token** and copy it immediately

### Step 5 — Add secrets to GitHub Actions

In your podcast repo, go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add each of these:

| Secret name              | Value                                                      |
|--------------------------|------------------------------------------------------------|
| `YOUTUBE_PLAYLIST_URL`   | Full YouTube playlist URL                                  |
| `YOUTUBE_API_KEY`        | Your YouTube Data API key from Step 3                      |
| `PODCAST_GITHUB_TOKEN`   | The fine-grained token from Step 4                         |
| `GITHUB_USERNAME`        | Your GitHub username                                       |
| `PODCAST_TITLE`          | e.g. `Harvest Church Sermons`                              |
| `PODCAST_DESCRIPTION`    | A few sentences about the podcast                          |
| `PODCAST_AUTHOR`         | e.g. `Harvest Church`                                      |
| `PODCAST_EMAIL`          | Contact email shown in podcast directories                 |
| `PODCAST_LANGUAGE`       | `en-au`                                                    |
| `PODCAST_CATEGORY`       | e.g. `Religion & Spirituality`                             |
| `PODCAST_SUBCATEGORY`    | e.g. `Christianity` (or leave blank)                       |
| `PODCAST_IMAGE_URL`      | URL to your cover art (square, 1400–3000px, JPG/PNG)       |
| `PODCAST_EXPLICIT`       | `no`                                                       |
| `AUDIO_BITRATE`          | `128` (good quality, keeps file sizes reasonable)          |
| `MAX_DURATION_MINUTES`   | `0` (no limit) or e.g. `120` to skip very long videos      |

### Step 6 — Push this code to your repo

```bash
cd yt-to-podcast
git init
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git add .
git commit -m "Initial setup"
git push -u origin main
```

### Step 7 — Run the first sync manually

1. In your GitHub repo, go to **Actions**
2. Click **Sync YouTube Playlist to Podcast**
3. Click **Run workflow** → **Run workflow**
4. Watch the logs — it will process every video in the playlist on first run

> ⚠️ Large playlists (50+ videos) can take 30–90 minutes on first run. Subsequent weekly runs
> only process new videos and are very fast.

### Step 8 — Add your feed to Spotify

Once the workflow has run and `feed.xml` exists:

1. Go to https://podcasters.spotify.com
2. Click **Get started** → **I already have a podcast**
3. Paste your feed URL: `https://YOUR_USERNAME.github.io/YOUR_REPO/feed.xml`
4. Follow the prompts to verify and publish

That's it. Spotify will poll your RSS feed automatically. Every Monday when the workflow runs
and finds new videos, they appear on Spotify within a few hours.

### Step 8b — Add to Apple Podcasts (optional, same effort)

1. Go to https://podcastsconnect.apple.com
2. Click **Add a Show** → paste your feed URL
3. Submit for review (usually approved within 24 hours)

---

## Changing the schedule

Edit `.github/workflows/sync.yml` and change the cron line:

```yaml
- cron: '0 20 * * 0'   # Every Sunday 8pm UTC = Monday 6am AEST
```

Use https://crontab.guru to build your preferred schedule.

---

## Cover art requirements

Spotify and Apple Podcasts both require:
- Square image (1:1 ratio)
- Minimum 1400 × 1400 pixels, maximum 3000 × 3000 pixels
- JPG or PNG format
- Under 512KB file size

Upload your cover image to the repo root and reference it as:
`https://YOUR_USERNAME.github.io/YOUR_REPO/cover.jpg`

---

## Costs

| Service         | Cost  |
|-----------------|-------|
| GitHub Actions  | Free (2000 min/month on free accounts — a typical weekly run uses ~10–30 min) |
| GitHub Pages    | Free  |
| GitHub Releases | Free (unlimited storage for public repos) |
| YouTube API     | Free (well within the 10,000 unit/day quota) |
| Spotify         | Free  |
| Apple Podcasts  | Free  |

**Total: $0/month.**

---

## Troubleshooting

**yt-dlp fails on some videos**
The script logs errors and continues. Age-restricted or region-blocked videos will be skipped.
Check the Actions log for details.

**Feed doesn't show up on Spotify**
Spotify re-checks your feed every few hours. You can force a refresh from the Spotify for
Podcasters dashboard under **Settings** → **Distribution**.

**Workflow fails with auth error**
Your fine-grained token may have expired. Regenerate it and update the `PODCAST_GITHUB_TOKEN` secret.

**Audio files are too large**
Reduce `AUDIO_BITRATE` to `96` or set `MAX_DURATION_MINUTES` to skip very long videos.












Biblical teaching from Harvest Church, a multi-campus church serving Horsham, Nhill, and Stawell in regional Victoria, Australia. Each week, Senior Pastor Andrew Cartledge brings clear, scripture-grounded preaching that connects ancient text to everyday life, whether you're a longtime believer or just curious about faith.
Expect honest engagement with the Bible's big questions: who God is, what it means to follow Jesus, and how faith shapes the way we live, work, and love one another. Messages range from verse-by-verse exposition to topical series on calling, identity, and Christian living, all delivered with warmth and a genuine pastoral heart for the Wimmera region and beyond.
New episodes released weekly. Subscribe to grow in your faith alongside the Harvest Church family, wherever you're listening from.
