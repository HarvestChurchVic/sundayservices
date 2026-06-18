import 'dotenv/config';
import { fetchPlaylistVideos } from './youtube.js';
import { downloadAudio } from './downloader.js';
import { uploadToGitHub, getProcessedIds, saveProcessedIds } from './github.js';
import { generateRSS } from './rss.js';
import { log } from './logger.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TMP_DIR = path.join(__dirname, '..', 'tmp');

async function run() {
  log('info', '━━━ YouTube → Podcast sync starting ━━━');

  // Ensure tmp dir exists
  if (!fs.existsSync(TMP_DIR)) fs.mkdirSync(TMP_DIR, { recursive: true });

  // 1. Fetch current playlist from YouTube
  log('info', 'Fetching playlist from YouTube...');
  const videos = await fetchPlaylistVideos(process.env.YOUTUBE_PLAYLIST_URL);
  log('info', `Found ${videos.length} videos in playlist`);

  // 2. Load already-processed video IDs from GitHub
  log('info', 'Loading processed episode list from GitHub...');
  const processed = await getProcessedIds();
  const newVideos = videos.filter(v => !processed.ids.includes(v.id));

  if (newVideos.length === 0) {
    log('info', 'No new videos found. Nothing to do.');
    return;
  }

  log('info', `${newVideos.length} new video(s) to process`);

  const newEpisodes = [];

  for (const video of newVideos) {
    log('info', `Processing: "${video.title}" (${video.id})`);

    try {
      // 3. Download and convert audio
      const audioPath = await downloadAudio(video, TMP_DIR);
      const audioStats = fs.statSync(audioPath);

      // 4. Upload MP3 to GitHub Releases
      const audioUrl = await uploadToGitHub(audioPath, video.id);

      // Build episode object
      newEpisodes.push({
        id: video.id,
        title: video.title,
        description: video.description,
        audioUrl,
        fileSize: audioStats.size,
        durationSeconds: video.durationSeconds,
        pubDate: video.publishedAt,
        youtubeUrl: `https://www.youtube.com/watch?v=${video.id}`,
      });

      // Mark as processed
      processed.ids.push(video.id);
      processed.episodes.unshift(newEpisodes[newEpisodes.length - 1]);

      // Clean up tmp file
      fs.unlinkSync(audioPath);
      log('info', `✓ Done: "${video.title}"`);

    } catch (err) {
      log('error', `✗ Failed to process "${video.title}": ${err.message}`);
      // Continue with other videos rather than aborting
    }
  }

  if (newEpisodes.length === 0) {
    log('warn', 'All new videos failed to process.');
    return;
  }

  // 5. Regenerate RSS feed and push to GitHub Pages
  log('info', 'Regenerating RSS feed...');
  const rssXml = generateRSS(processed.episodes);
  await saveProcessedIds(processed, rssXml);

  log('info', `━━━ Sync complete. ${newEpisodes.length} new episode(s) published. ━━━`);
}

run().catch(err => {
  log('error', `Fatal error: ${err.message}`);
  console.error(err);
  process.exit(1);
});
