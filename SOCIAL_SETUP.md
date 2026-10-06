# Social posting setup (Spotify and Facebook)

## What changed

The pipeline no longer emails you as soon as it finishes. The process is now:

1. Upload video (through the form)
2. Process audio
3. Transcribe audio
4. Create blurb
5. Create Planning Center episode
6. Update the RSS feed (Spotify, Apple etc.)
7. Queue the episode in `pending_social.json`. The uploaded video is kept in storage (R2) for now.
8. The **Publish Social** workflow runs straight away and uploads the **full sermon video** to the Harvest Church Facebook Page. The post's text is the blurb (no hashtags), ending with "Links to watch or listen on Church Center, Spotify and YouTube are in the comments."
9. Once Facebook has processed the video, it's deleted from storage.
10. The workflow checks Spotify every minute for the first 30 minutes, then every 10 minutes, for the episode (same title, released within 3 days of the sermon date).
11. Once it's on Spotify:
    - **Links comment:** the Page comments on its own video post with the links, in this order: Church Center, Spotify, YouTube.
    - **Completion email:** everything it had before, plus the public Church Center link, the Spotify episode link and the Facebook post link. Its last step is to share the Facebook post into the Harvest Church Group, with the link included.
12. Share the Facebook post into the Harvest Church Group by hand. Meta doesn't allow any app to post into Groups.

**Why a video with the links in a comment?** Facebook now limits many business Pages to 2 posts with links per month. A video post has no links in it, so it doesn't count, and Facebook gives native video far more reach. Links in comments aren't limited.

If a sermon ever arrives without an uploaded video, the thumbnail is posted as a photo instead, with the same text and the same links comment.

Instagram posting is built but **switched off** for now. See "Turning Instagram back on" at the end.

**Safety nets**

- **Spotify never shows the episode:** after 24 hours the links comment and email go out anyway, without a Spotify link. The email says when this has happened.
- **Something fails** (upload, Facebook's processing, or the comment): the next run tries that step again. Nothing is posted twice and only one email is sent. After 18 failed tries (about 3 hours) the email goes out anyway with the failure noted, and the video is removed from storage.

## One-off setup

Spotify needs no setup. The workflow reads the newest episode from the podcast's public Spotify page, the same way anyone can without logging in.

You'll end up with two new GitHub secrets:

| Secret | Where it comes from |
|---|---|
| `META_PAGE_ID` | Graph API Explorer (step A4) |
| `META_PAGE_TOKEN` | Graph API Explorer (step A4) |

### Part A: Facebook

You need to be an admin of the Harvest Facebook Page.

**A1. Create the Meta app**

1. **Register as a Meta developer (first time only).** Go to https://developers.facebook.com and log in with the Facebook account that is an admin of the Harvest Page. If you see a **Get started** button at the top right, click it and follow the prompts (accept the terms, verify your account, choose a role such as "Developer"). If you see **My Apps** instead, you're already registered, so skip ahead.
2. Go straight to the app creation page: https://developers.facebook.com/apps/creation/
3. **App details:** enter the app name `Harvest Sermon Poster` and your contact email. Click **Next**.
4. **Use cases:** tick **Manage everything on your Page**. (If you don't see it straight away, look under the "Content management" filter on the left.) Click **Next**.
5. **Business:** choose the Harvest business portfolio if it's listed. If not, choose **I don't want to connect a business portfolio yet**. Click **Next**.
6. **Requirements:** nothing to do here. Click **Next**.
7. **Overview:** click **Go to dashboard**.
8. **Add the posting permissions:**
   - On the dashboard, click **Use cases** in the left menu (or find the use case on the **Dashboard** page).
   - Next to **Manage everything on your Page**, click **Customize**.
   - In the permissions list, click **Add** next to `pages_manage_posts`, `pages_read_engagement`, `pages_manage_engagement` and `pages_read_user_content`. (`pages_show_list` and `business_management` are already included.) `pages_manage_engagement` is what lets the Page add the links comment, and it needs `pages_read_user_content` alongside it.
9. Switch the app to **Live** mode. This matters: while an app is in Development mode, anything it posts can only be seen by people who have a role on the app, so the rest of the world (including other Page admins) won't see the posts.
   - In the left menu, go to **App settings** > **Basic**. Fill in **Privacy policy URL** (the privacy page on harvestchurch.org.au), choose a **Category** (for example "Business and pages"), and click **Save changes**.
   - At the top of the dashboard, flip the **App mode** toggle from **Development** to **Live**. If it lists anything else it needs first, complete those items and try again.
   - Because the app only ever posts to a Page you manage, it doesn't need Meta's App Review.

**A2 to A4: Get the Page token**

You'll handle three different tokens here. Only the third one goes into GitHub.

**A2. Token 1: short-lived personal token**

1. Go to https://developers.facebook.com/tools/explorer
2. On the right, set **Meta App** to `Harvest Sermon Poster` and **User or Page** to **User Token**.
3. Under **Permissions**, make sure all of these are listed:
   - `pages_show_list`
   - `business_management`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `pages_manage_engagement`
   - `pages_read_user_content`
4. Click **Generate Access Token** and approve the pop-up for the Harvest Church Page.
5. Copy the token from the **Access Token** box.

**A3. Token 2: long-lived personal token (still not the one for GitHub)**

1. Go to https://developers.facebook.com/tools/debug/accesstoken, paste token 1 and click **Debug**.
2. At the bottom, click **Extend Access Token** (enter your password if it asks). Copy the new, longer token.

**A4. Token 3: the Page token (this is the one for GitHub)**

1. Back in the Graph API Explorer, click in the **Access Token** box, delete what's there and paste token 2. This step is easy to miss: clicking **Generate Access Token** again would overwrite it with a short-lived one.
2. Leave the method as **GET**, enter `me/accounts?fields=id,name,access_token` in the query box and click **Submit**.
3. In the results, find the block with `"name": "Harvest Church"`. Inside that block copy:
   - `id` → this is `META_PAGE_ID`
   - `access_token` → this is `META_PAGE_TOKEN`
4. **Check it before saving:** paste token 3 into the Access Token Debugger. It must show **Type: Page** (if it says User, it's the wrong token), **Expires: Never** (if it shows a date, token 2 wasn't used in step 1), and **Scopes** including `pages_manage_engagement`.

Keep `META_PAGE_TOKEN` private. It can post to the Page. Only put it in GitHub secrets. This repo is public and its workflow logs can be seen by anyone, so never paste the token into a workflow input or a file.

### Part B: Add the secrets to GitHub

1. Go to the repo on GitHub, then **Settings** > **Secrets and variables** > **Actions**.
2. For each of the two secrets, click **New repository secret**, enter the name exactly as it appears in the table above, paste the value and click **Add secret**.

### Part C: Test it

**Dry run (checks connections, posts nothing):**

1. Go to **Actions** > **Publish Social** > **Run workflow**. Leave **Dry run** ticked and click **Run workflow**.
2. Open the run and expand **Run social publisher**. You should see:
   - `Spotify: connected. Episode link: https://open.spotify.com/episode/...` (for the latest sermon)
   - `Facebook Page: connected to 'Harvest ...'` and `Facebook token: correct type`
   - `Instagram: switched off`
   - A preview of the video post's text and of the links comment

**Full-length test from the upload form (best for testing Facebook):**

1. Open the sermon upload form and tick **Test only** at the top. The sermon details disappear, because the test uses last Sunday's.
2. Tick **Make the test post public** if you want to see it exactly as a Sunday post looks. Otherwise only Page admins can see it.
3. Choose a full-length sermon video, enter the passphrase and click **Upload & Test**.
4. The video uploads to storage, then the **Publish Social** workflow uploads it to Facebook, waits for processing (often 10 to 40 minutes for a full sermon), adds the links comment, deletes the video from storage and sends a [TEST] email. No sermon is processed and nothing is added to the podcast feed.
5. Delete the test post from the Page afterwards (it's titled "[TEST] ...").

**Quick test without uploading anything (a 20-second clip of last week's sermon):**

1. **Run workflow** again with **Dry run** unticked and **Real test with last week's sermon** ticked. Leave **PUBLIC** unticked for a hidden post, or tick it to see exactly what a Sunday post looks like (then delete it afterwards).
2. The log shows the clip being made, uploaded, processed by Facebook, the links comment being added and the clip being removed from storage. A [TEST] completion email follows.

## Good to know

- If the Page token ever stops working (for example after a Facebook password change or someone being removed as a Page admin), repeat A2 to A4 and update `META_PAGE_TOKEN`. The Dry run says `Facebook token: correct type` when it's right.
- **If the links comment fails with a permissions error,** the token is missing `pages_manage_engagement`. Add it and `pages_read_user_content` (A1 step 8 and A2), repeat A2 to A4 and update `META_PAGE_TOKEN`.
- The video's music must be cleared for Facebook (for example from Facebook's or YouTube's royalty-free libraries), or Facebook may mute or block it.
- GitHub pauses scheduled workflows after 60 days with no commits to the repo. Weekly sermons keep it active. If there's a long break and it's paused, GitHub shows a button on the Actions page to turn it back on.
- To change the link labels ("Watch on Church Center" etc.) or the 24-hour wait, edit the settings near the top of `social_publisher.py`.

## Turning Instagram back on

The Instagram code is still in place. When you want it:

1. Make sure the Harvest Instagram business account is connected to the Facebook Page (Meta Business Suite > Settings > Accounts).
2. Repeat A2 to A4, adding `instagram_basic` and `instagram_content_publish` to the permissions in A2 and choosing the Instagram account in the pop-up. In A4, use `me/accounts?fields=id,name,access_token,instagram_business_account`, then update `META_PAGE_TOKEN` and add `instagram_business_account` > `id` as a new secret called `INSTAGRAM_USER_ID`.
3. In `social_publisher.py`, change `INSTAGRAM_ENABLED = False` to `INSTAGRAM_ENABLED = True`.
4. In `.github/workflows/publish-social.yml`, remove the `# ` from the start of the `INSTAGRAM_USER_ID` line.

Instagram posts would be the thumbnail and blurb, with no links and no hashtags.
