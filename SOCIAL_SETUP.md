# Social posting setup (Spotify, Facebook, Instagram)

## What changed

The pipeline no longer emails you as soon as it finishes. The new process is:

1. Upload video
2. Process audio
3. Transcribe audio
4. Create blurb
5. Create Planning Center episode
6. Update the RSS feed (Spotify, Apple etc.)
7. Queue the episode in `pending_social.json`
8. The **Publish Social** workflow checks every 30 minutes for the episode on Spotify (same title, released within 3 days of the sermon date)
9. Once it's on Spotify:
   - **Facebook Page post:** thumbnail, blurb, then links in this order: Church Center, Spotify, YouTube. No hashtags.
   - **Instagram post:** thumbnail and blurb. No links, no hashtags.
   - **Completion email:** everything it had before, plus the public Church Center link, the Spotify episode link, and links to the Facebook and Instagram posts
10. Share the Facebook Page post into the Facebook Group by hand. Meta doesn't allow any app to post into Groups.

The email goes out after the posts so it can include the Facebook post link for sharing into the Group.

**Safety nets**

- **Spotify never shows the episode:** after 24 hours the posts and email go out anyway, using the link to the Spotify show. The email says when this has happened.
- **A post fails** (for example Instagram is down): the next run retries only that post. Nothing is posted twice and only one email is sent. After 6 failed tries (about 3 hours) the email goes out anyway with the failure noted.

## One-off setup

You'll end up with six new GitHub secrets:

| Secret | Where it comes from |
|---|---|
| `SPOTIFY_CLIENT_ID` | Spotify developer app (Part A) |
| `SPOTIFY_CLIENT_SECRET` | Spotify developer app (Part A) |
| `SPOTIFY_SHOW_ID` | Your podcast's Spotify link (Part A) |
| `META_PAGE_ID` | Graph API Explorer (Part B) |
| `META_PAGE_TOKEN` | Graph API Explorer (Part B) |
| `INSTAGRAM_USER_ID` | Graph API Explorer (Part B) |

### Part A: Spotify

**Important:** since February 2026 Spotify requires the account that owns a developer app to have **Spotify Premium**. Use an account that has it.

1. Go to https://developer.spotify.com/dashboard and log in with that account.
2. Click **Create app**.
   - App name: `Harvest Sermon Checker`
   - App description: `Checks when new sermon episodes appear on Spotify`
   - Redirect URI: `https://harvestchurch.org.au` (it's required but never used), then click **Add**
   - Under "Which API/SDKs are you planning to use?", tick **Web API**
   - Tick the terms box and click **Save**
3. Open the app and click **Settings**. Copy the **Client ID**. Click **View client secret** and copy the **Client secret**.
4. For the show ID, open Spotify, go to the Harvest Sunday Sermons podcast, then **...** > **Share** > **Copy link to show**. The link looks like `https://open.spotify.com/show/4rOoJ6Egrf8K2IrywzwOMk?si=...`. The show ID is the part between `/show/` and `?`, so `4rOoJ6Egrf8K2IrywzwOMk` in this example.

### Part B: Facebook and Instagram

You need to be an admin of the Harvest Facebook Page, and the Instagram business account must be connected to that Page. You can check this in Meta Business Suite > Settings > Accounts.

**B1. Create the Meta app**

1. Go to https://developers.facebook.com/apps and click **Create app**.
2. Name it `Harvest Sermon Poster` and use your email.
3. When asked for a use case, choose **Manage everything on your Page**. If you're offered an option to add more use cases, also add the Instagram one that mentions **content** or **publishing**.
4. Pick the Harvest business portfolio if it asks, then finish creating the app.
5. Leave the app in **Development** mode. Because you're the admin of both the app and the Page, it doesn't need Meta's app review.

**B2. Get a token**

1. Go to https://developers.facebook.com/tools/explorer
2. On the right, set **Meta App** to `Harvest Sermon Poster` and **User or Page** to **User Token**.
3. Under **Permissions**, add all of these:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `instagram_basic`
   - `instagram_content_publish`
   - `business_management`
4. Click **Generate Access Token**. When the Facebook pop-up asks, choose the Harvest Page **and** the Harvest Instagram account, and allow everything.

**B3. Make the token long-lived**

1. Copy the token from the Explorer's **Access Token** box.
2. Go to https://developers.facebook.com/tools/debug/accesstoken, paste the token and click **Debug**.
3. At the bottom, click **Extend Access Token** (enter your password if it asks). Copy the new, longer token.

**B4. Get the Page token and IDs**

1. Back in the Graph API Explorer, paste the long token into the **Access Token** box. Replace what's there.
2. In the query box, enter:
   `me/accounts?fields=id,name,access_token,instagram_business_account`
3. Click **Submit**. Find the Harvest Page in the results and copy:
   - `id` → this is `META_PAGE_ID`
   - `access_token` → this is `META_PAGE_TOKEN`
   - `instagram_business_account` > `id` → this is `INSTAGRAM_USER_ID`
4. Optional check: paste the `META_PAGE_TOKEN` into the Access Token Debugger. **Expires** should say **Never**.

Keep `META_PAGE_TOKEN` private. It can post to the Page. Only put it in GitHub secrets. This repo is public and its workflow logs can be seen by anyone, so never paste the token into a workflow input or a file.

### Part C: Add the secrets to GitHub

1. Go to the repo on GitHub, then **Settings** > **Secrets and variables** > **Actions**.
2. For each of the six secrets, click **New repository secret**, enter the name exactly as it appears in the table above, paste the value and click **Add secret**.

### Part D: Test it

1. Go to **Actions** > **Publish Social** > **Run workflow**. Leave **Dry run** ticked and click **Run workflow**.
2. Open the run and expand **Run social publisher**. You should see:
   - `Spotify: connected. Episode link: https://open.spotify.com/episode/...` (for the latest sermon)
   - `Facebook Page: connected to 'Harvest ...'`
   - `Instagram: connected to @...`
   - Previews of both captions
3. A dry run posts nothing and sends nothing. The first real posts will happen after the next sermon goes through **Process Sermon**.

## Good to know

- If the Page token ever stops working (for example after a Facebook password change or someone being removed as a Page admin), repeat B2 to B4 and update `META_PAGE_TOKEN`.
- GitHub pauses scheduled workflows after 60 days with no commits to the repo. Weekly sermons keep it active. If there's a long break and it's paused, GitHub shows a button on the Actions page to turn it back on.
- To change the link labels ("Watch on Church Center" etc.) or the 24-hour wait, edit the settings near the top of `social_publisher.py`.
