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

Actions tab -> **Process Sermon** -> **Run workflow** -> fill in the YouTube
URL, title, speaker, and sermon date -> **Run workflow**. A summary email
arrives once it's done, with the blurb and links for the two remaining
manual steps.

## First time only: submitting the feed

Once the first episode has been processed, submit the feed URL
(`R2_PUBLIC_BASE_URL/feed.xml`) once to:

- **Apple Podcasts Connect** -- podcastsconnect.apple.com
- **Spotify for Podcasters** -- podcasters.spotify.com

After that, both platforms poll the feed automatically -- no further action
needed for future episodes.
