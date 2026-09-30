# Social posting setup (Spotify and Facebook)

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
   - **Facebook Page post:** the Church Center episode is the main part of the post, shown as a large preview card with its title and artwork. Above it, the blurb, then links in this order: Church Center, Spotify, YouTube. No hashtags.
   - **Completion email:** everything it had before, plus the public Church Center link, the Spotify episode link and the Facebook post link. Its instructions now end with a step to share the Facebook post into the Harvest Church Group, with the link included.
10. Share the Facebook Page post into the Harvest Church Group by hand. Meta doesn't allow any app to post into Groups.

The email goes out after the Facebook post so it can include the post link.

Instagram posting is built but **switched off** for now. See "Turning Instagram back on" at the end.

**Safety nets**

- **Spotify never shows the episode:** after 24 hours the post and email go out anyway, without a Spotify link. The email says when this has happened.
- **The Facebook post fails:** the next run tries again. Nothing is posted twice and only one email is sent. After 6 failed tries (about 3 hours) the email goes out anyway with the failure noted.

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
   - In the permissions list, click **Add** next to `pages_manage_posts` and next to `pages_read_engagement`. (`pages_show_list` and `business_management` are already included.)
9. Leave the app in **Development** mode (it starts that way). Because you're the admin of both the app and the Page, it doesn't need Meta's app review.

**A2. Get a token**

1. Go to https://developers.facebook.com/tools/explorer
2. On the right, set **Meta App** to `Harvest Sermon Poster` and **User or Page** to **User Token**.
3. Under **Permissions**, add all of these:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `business_management`
4. Click **Generate Access Token**. When the Facebook pop-up asks, choose the Harvest Page and allow everything.

**A3. Make the token long-lived**

1. Copy the token from the Explorer's **Access Token** box.
2. Go to https://developers.facebook.com/tools/debug/accesstoken, paste the token and click **Debug**.
3. At the bottom, click **Extend Access Token** (enter your password if it asks). Copy the new, longer token.

**A4. Get the Page token and ID**

1. Back in the Graph API Explorer, paste the long token into the **Access Token** box. Replace what's there.
2. In the query box, enter:
   `me/accounts?fields=id,name,access_token`
3. Click **Submit**. Find the Harvest Page in the results and copy:
   - `id` → this is `META_PAGE_ID`
   - `access_token` → this is `META_PAGE_TOKEN`
4. Optional check: paste the `META_PAGE_TOKEN` into the Access Token Debugger. **Expires** should say **Never**.

Keep `META_PAGE_TOKEN` private. It can post to the Page. Only put it in GitHub secrets. This repo is public and its workflow logs can be seen by anyone, so never paste the token into a workflow input or a file.

### Part B: Add the secrets to GitHub

1. Go to the repo on GitHub, then **Settings** > **Secrets and variables** > **Actions**.
2. For each of the two secrets, click **New repository secret**, enter the name exactly as it appears in the table above, paste the value and click **Add secret**.

### Part C: Test it

1. Go to **Actions** > **Publish Social** > **Run workflow**. Leave **Dry run** ticked and click **Run workflow**.
2. Open the run and expand **Run social publisher**. You should see:
   - `Spotify: connected. Episode link: https://open.spotify.com/episode/...` (for the latest sermon)
   - `Facebook Page: connected to 'Harvest ...'`
   - `Instagram: switched off`
   - A preview of the Facebook post text
3. A dry run posts nothing and sends nothing. The first real post will happen after the next sermon goes through **Process Sermon**.

## Good to know

- If the Page token ever stops working (for example after a Facebook password change or someone being removed as a Page admin), repeat A2 to A4 and update `META_PAGE_TOKEN`.
- GitHub pauses scheduled workflows after 60 days with no commits to the repo. Weekly sermons keep it active. If there's a long break and it's paused, GitHub shows a button on the Actions page to turn it back on.
- To change the link labels ("Watch on Church Center" etc.) or the 24-hour wait, edit the settings near the top of `social_publisher.py`.

## Turning Instagram back on

The Instagram code is still in place. When you want it:

1. Make sure the Harvest Instagram business account is connected to the Facebook Page (Meta Business Suite > Settings > Accounts).
2. Repeat A2 to A4, adding `instagram_basic` and `instagram_content_publish` to the permissions in A2 and choosing the Instagram account in the pop-up. In A4, use `me/accounts?fields=id,name,access_token,instagram_business_account`, then update `META_PAGE_TOKEN` and add `instagram_business_account` > `id` as a new secret called `INSTAGRAM_USER_ID`.
3. In `social_publisher.py`, change `INSTAGRAM_ENABLED = False` to `INSTAGRAM_ENABLED = True`.
4. In `.github/workflows/publish-social.yml`, remove the `# ` from the start of the `INSTAGRAM_USER_ID` line.

Instagram posts would be the thumbnail and blurb, with no links and no hashtags.
