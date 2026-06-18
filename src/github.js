import fetch from 'node-fetch';
import fs from 'fs';
import path from 'path';
import { log } from './logger.js';

const GITHUB_API = 'https://api.github.com';

function headers(extra = {}) {
  return {
    Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    ...extra,
  };
}

const owner = () => process.env.GITHUB_USERNAME;
const repo = () => process.env.GITHUB_REPO;
const branch = () => process.env.GITHUB_BRANCH || 'main';

/**
 * Get or create a GitHub Release to host audio files.
 * We use a single persistent release called "episodes".
 */
async function getOrCreateRelease() {
  const tag = 'episodes';
  const url = `${GITHUB_API}/repos/${owner()}/${repo()}/releases/tags/${tag}`;

  const res = await fetch(url, { headers: headers() });

  if (res.status === 200) {
    const data = await res.json();
    return data;
  }

  // Create the release
  log('info', 'Creating GitHub Release for audio hosting...');
  const createRes = await fetch(`${GITHUB_API}/repos/${owner()}/${repo()}/releases`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      tag_name: tag,
      name: 'Podcast Episodes',
      body: 'Audio files for the podcast feed. Managed automatically.',
      draft: false,
      prerelease: false,
    }),
  });

  if (!createRes.ok) {
    const err = await createRes.text();
    throw new Error(`Failed to create GitHub Release: ${err}`);
  }

  return createRes.json();
}

/**
 * Upload an MP3 file as a GitHub Release asset.
 * Returns the browser_download_url.
 */
export async function uploadToGitHub(filePath, videoId) {
  const release = await getOrCreateRelease();
  const filename = `${videoId}.mp3`;

  // Delete existing asset with same name if it exists (re-run safety)
  const existingAsset = (release.assets || []).find(a => a.name === filename);
  if (existingAsset) {
    log('info', `Deleting existing asset for ${filename}...`);
    await fetch(`${GITHUB_API}/repos/${owner()}/${repo()}/releases/assets/${existingAsset.id}`, {
      method: 'DELETE',
      headers: headers(),
    });
  }

  const fileBuffer = fs.readFileSync(filePath);
  const uploadUrl = release.upload_url.replace('{?name,label}', `?name=${filename}`);

  log('info', `Uploading ${filename} to GitHub Release...`);

  const uploadRes = await fetch(uploadUrl, {
    method: 'POST',
    headers: headers({
      'Content-Type': 'audio/mpeg',
      'Content-Length': fileBuffer.length.toString(),
    }),
    body: fileBuffer,
  });

  if (!uploadRes.ok) {
    const err = await uploadRes.text();
    throw new Error(`GitHub asset upload failed: ${err}`);
  }

  const asset = await uploadRes.json();
  log('info', `Uploaded: ${asset.browser_download_url}`);
  return asset.browser_download_url;
}

// ─────────────────────────────────────────────
// Processed episodes state — stored as JSON in the repo
// ─────────────────────────────────────────────

const STATE_FILE = 'data/processed.json';
const RSS_FILE = 'feed.xml';

async function getFileSha(filePath) {
  const res = await fetch(
    `${GITHUB_API}/repos/${owner()}/${repo()}/contents/${filePath}?ref=${branch()}`,
    { headers: headers() }
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to check file ${filePath}: ${res.status}`);
  const data = await res.json();
  return data.sha;
}

async function upsertFile(filePath, content, message) {
  const sha = await getFileSha(filePath);
  const body = {
    message,
    content: Buffer.from(content).toString('base64'),
    branch: branch(),
  };
  if (sha) body.sha = sha;

  const res = await fetch(
    `${GITHUB_API}/repos/${owner()}/${repo()}/contents/${filePath}`,
    {
      method: 'PUT',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Failed to upsert ${filePath}: ${err}`);
  }
}

/**
 * Load processed episode state from GitHub repo.
 * Returns { ids: [], episodes: [] }
 */
export async function getProcessedIds() {
  const res = await fetch(
    `${GITHUB_API}/repos/${owner()}/${repo()}/contents/${STATE_FILE}?ref=${branch()}`,
    { headers: headers() }
  );

  if (res.status === 404) {
    log('info', 'No existing state file found — starting fresh');
    return { ids: [], episodes: [] };
  }

  if (!res.ok) throw new Error(`Failed to fetch state: ${res.status}`);

  const data = await res.json();
  const content = Buffer.from(data.content, 'base64').toString('utf8');
  return JSON.parse(content);
}

/**
 * Save updated processed state and RSS feed back to GitHub.
 */
export async function saveProcessedIds(processed, rssXml) {
  log('info', 'Saving state file to GitHub...');
  await upsertFile(
    STATE_FILE,
    JSON.stringify(processed, null, 2),
    `chore: update processed episodes state [skip ci]`
  );

  log('info', 'Saving RSS feed to GitHub...');
  await upsertFile(
    RSS_FILE,
    rssXml,
    `feat: publish ${processed.episodes.length} episode(s) [skip ci]`
  );

  log('info', `RSS feed live at: https://${owner()}.github.io/${repo()}/feed.xml`);
}
