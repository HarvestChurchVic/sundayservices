# Harvest Church Sermon Pipeline

Turns a finished, edited YouTube sermon clip into a hosted podcast episode:
transcribes it, writes the blurb, hosts the audio, updates the RSS feed, and
emails a summary. Triggered manually from the GitHub Actions tab, from any
device — nothing needs to be installed locally.

## What's automated vs still manual

| Step | Automated? |
|---|---|
| Clip sermon in YouTube, add intro/outro | Manual — creative editing decision |
| Download MP4, extract MP3, delete MP4 | **Automated** |
| Transcribe | **Automated** — local Whisper |
| Generate blurb | **Automated** — direct Claude API call |
| Host MP3 | **Automated** — uploaded to Cloudflare R2 |
| Build + host RSS feed | **Automated** |
| Notify with blurb + links | **Automated** — email sent automatically |
| Update YouTube title/speaker | Manual for now |
| Planning Center sermon entry | Manual for now |

## One-time setup

1. Cloudflare R2 bucket created and enabled (done)
2. Generate an R2 API token (Cloudflare dashboard -> R2 -> Manage API tokens)
   for Object Read & Write on the bucket -- gives you the Access Key ID and
   Secret Access Key
3. In this repo's Settings -> Secrets and variables -> Actions, add every
   value listed in `config.example.env` as a matching secret
4. For `PODCAST_IMAGE_URL`, use `PodCover.png` already in this repo --
   easiest is to upload it to the R2 bucket too and use its public URL

## Running it

There are two ways to get the video in:

**Option A — manual upload (recommended, most reliable):** download the
finished YouTube clip yourself (any method you like), then drag it into the
R2 bucket's `raw-uploads/` folder via the Cloudflare dashboard (R2 →
`harvestchurch-sermons` → Upload). Then run the workflow with the `source_file`
field set to that path, e.g. `raw-uploads/2026-07-27-sermon.mp4`. The pipeline
pulls it from R2, processes it, then deletes the raw upload once done.

**Option B — automatic via yt-dlp:** leave `source_file` blank and the
pipeline downloads the video from YouTube itself. This depends on YouTube
cookies (`YOUTUBE_COOKIES` secret) staying valid, which needs periodic
refreshing — option A avoids this entirely.

Either way: Actions tab → **Process Sermon** → **Run workflow** → fill in the
YouTube URL (for reference), title, speaker, sermon date, and optionally
`source_file` → **Run workflow**. A summary email arrives once it's done.

## Episode thumbnails (optional)

To give a specific episode its own artwork (instead of the default podcast
cover), upload a PNG to R2 at `images/<same-filename-as-video>.png`. For
example, if the raw video is `20260725.mp4`, the thumbnail goes at
`images/20260725.png`. The pipeline checks for this automatically — if
found, it's used as that episode's artwork in the feed; if not, the episode
falls back to the podcast's default cover art. This only applies when using
the manual R2 upload path (source_file), not the yt-dlp path.

## Automatic triggering via SharePoint (for the whole team)

Instead of running the workflow by hand each time, anyone on the team can
just drop a file into a designated SharePoint folder, named like:

    2026-07-25_Strong-and-Courageous_Andrew-Cartledge.mp4

(date, then title with dashes instead of spaces, then speaker with dashes,
same pattern every time). An hourly scheduled GitHub Action
(`.github/workflows/watch_sharepoint.yml`) checks that folder, and if it
finds a new file matching that pattern, it automatically copies it to R2 and
runs the full pipeline — no one needs to touch GitHub Actions at all.

To also give that sermon its own thumbnail, drop a PNG in the same
SharePoint folder with the exact same filename (just `.png` instead of
`.mp4`) — the watcher picks it up automatically and copies it to the right
place in R2.

**One-time setup required** (needs your Microsoft 365 admin):
1. Register an app in Entra ID (Azure AD) → App registrations → New
   registration
2. Note the Application (client) ID and Directory (tenant) ID
3. Certificates & secrets → New client secret → copy the value
4. API permissions → Add a permission → Microsoft Graph → Application
   permissions → add `Sites.Read.All` → Grant admin consent
5. Add `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`,
   `SHAREPOINT_HOSTNAME`, `SHAREPOINT_SITE_PATH`, and
   `SHAREPOINT_FOLDER_PATH` as repo secrets (see `config.example.env` for
   the exact format of the site/folder values)

Files that don't match the naming convention are skipped and logged rather
than causing an error — a stray file dropped in the folder won't break
anything.

## First time only: submitting the feed

Once the first episode has been processed, submit the feed URL
(`R2_PUBLIC_BASE_URL/feed.xml`) once to:

- **Apple Podcasts Connect** -- podcastsconnect.apple.com
- **Spotify for Podcasters** -- podcasters.spotify.com

After that, both platforms poll the feed automatically -- no further action
needed for future episodes.
