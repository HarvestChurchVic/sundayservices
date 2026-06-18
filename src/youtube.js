import fetch from 'node-fetch';
import { log } from './logger.js';

const YT_API_BASE = 'https://www.googleapis.com/youtube/v3';

/**
 * Extract playlist ID from a full YouTube playlist URL or bare ID
 */
function extractPlaylistId(input) {
  if (!input) throw new Error('YOUTUBE_PLAYLIST_URL is not set in .env');
  try {
    const url = new URL(input);
    const list = url.searchParams.get('list');
    if (list) return list;
  } catch {
    // Not a URL — treat as bare playlist ID
  }
  if (/^PL[A-Za-z0-9_-]+$/.test(input)) return input;
  throw new Error(`Could not extract playlist ID from: ${input}`);
}

/**
 * Parse ISO 8601 duration (PT1H2M3S) to seconds
 */
function parseDuration(iso) {
  if (!iso) return 0;
  const match = iso.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
  if (!match) return 0;
  const h = parseInt(match[1] || '0', 10);
  const m = parseInt(match[2] || '0', 10);
  const s = parseInt(match[3] || '0', 10);
  return h * 3600 + m * 60 + s;
}

/**
 * Fetch all videos from a YouTube playlist using the Data API v3
 */
export async function fetchPlaylistVideos(playlistUrl) {
  const apiKey = process.env.YOUTUBE_API_KEY;
  if (!apiKey) throw new Error('YOUTUBE_API_KEY is not set in .env');

  const playlistId = extractPlaylistId(playlistUrl);
  log('info', `Playlist ID: ${playlistId}`);

  // Step 1: Fetch all playlist items (paged)
  const videoIds = [];
  const itemMap = {}; // videoId -> { title, description, publishedAt }
  let pageToken = '';

  do {
    const params = new URLSearchParams({
      part: 'snippet',
      playlistId,
      maxResults: '50',
      key: apiKey,
      ...(pageToken ? { pageToken } : {}),
    });

    const res = await fetch(`${YT_API_BASE}/playlistItems?${params}`);
    const data = await res.json();

    if (data.error) {
      throw new Error(`YouTube API error: ${data.error.message}`);
    }

    for (const item of data.items || []) {
      const vid = item.snippet?.resourceId?.videoId;
      if (!vid) continue;
      // Skip private/deleted videos
      if (item.snippet.title === 'Private video' || item.snippet.title === 'Deleted video') continue;

      videoIds.push(vid);
      itemMap[vid] = {
        title: item.snippet.title,
        description: item.snippet.description || '',
        publishedAt: item.snippet.publishedAt,
      };
    }

    pageToken = data.nextPageToken || '';
  } while (pageToken);

  log('info', `Fetched ${videoIds.length} public video IDs`);

  // Step 2: Fetch video details in batches of 50 to get durations
  const videos = [];
  const maxDuration = parseInt(process.env.MAX_DURATION_MINUTES || '0', 10) * 60;

  for (let i = 0; i < videoIds.length; i += 50) {
    const batch = videoIds.slice(i, i + 50);
    const params = new URLSearchParams({
      part: 'contentDetails',
      id: batch.join(','),
      key: apiKey,
    });

    const res = await fetch(`${YT_API_BASE}/videos?${params}`);
    const data = await res.json();

    if (data.error) {
      throw new Error(`YouTube API error (video details): ${data.error.message}`);
    }

    for (const item of data.items || []) {
      const durationSeconds = parseDuration(item.contentDetails?.duration);
      const meta = itemMap[item.id];
      if (!meta) continue;

      if (maxDuration > 0 && durationSeconds > maxDuration) {
        log('warn', `Skipping "${meta.title}" — duration ${Math.round(durationSeconds / 60)}m exceeds limit`);
        continue;
      }

      videos.push({
        id: item.id,
        title: meta.title,
        description: meta.description,
        publishedAt: meta.publishedAt,
        durationSeconds,
      });
    }
  }

  // Return newest first
  return videos.sort((a, b) => new Date(b.publishedAt) - new Date(a.publishedAt));
}
