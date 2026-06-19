import { execFile } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs';
import { log } from './logger.js';

const execFileAsync = promisify(execFile);

/**
 * Download audio from a YouTube video and convert to MP3.
 * Requires yt-dlp and ffmpeg to be installed on the system.
 * Both are available in the GitHub Actions ubuntu-latest runner.
 *
 * @param {Object} video - { id, title }
 * @param {string} tmpDir - Directory to write the output file
 * @returns {Promise<string>} Path to the downloaded MP3 file
 */
export async function downloadAudio(video, tmpDir) {
  const bitrate = process.env.AUDIO_BITRATE || '128';
  const outputTemplate = path.join(tmpDir, `${video.id}.%(ext)s`);
  const expectedOutput = path.join(tmpDir, `${video.id}.mp3`);

  // Remove any leftover file from a previous failed run
  if (fs.existsSync(expectedOutput)) {
    fs.unlinkSync(expectedOutput);
  }

  const args = [
    `https://www.youtube.com/watch?v=${video.id}`,
    '--extract-audio',
    '--audio-format', 'mp3',
    '--audio-quality', bitrate + 'K',
    // Be flexible about source format: prefer audio-only, fall back to best available
    '--format', 'bestaudio/best',
    '--output', outputTemplate,
    '--no-playlist',
    '--no-warnings',
    // Embed thumbnail as cover art
    '--embed-thumbnail',
    // Write metadata
    '--add-metadata',
    // Retry on transient errors
    '--retries', '3',
    '--fragment-retries', '3',
    // Avoid hammering YouTube too fast (reduces bot-detection risk)
    '--sleep-requests', '1',
    // Force the Android client for format extraction — the default web client
    // is currently affected by YouTube's SABR streaming rollout, which causes
    // "Requested format is not available" even when cookies are valid
    '--extractor-args', 'youtube:player_client=android,web',
  ];

  // Use cookies to avoid "Sign in to confirm you're not a bot" blocks on CI runners
  if (process.env.YOUTUBE_COOKIES_FILE && fs.existsSync(process.env.YOUTUBE_COOKIES_FILE)) {
    args.push('--cookies', process.env.YOUTUBE_COOKIES_FILE);
  }

  log('info', `Downloading audio for video ${video.id} at ${bitrate}kbps...`);

  try {
    const result = await execFileAsync('yt-dlp', args, {
      timeout: 10 * 60 * 1000, // 10 minute timeout
    });
    if (result.stdout) log('info', `yt-dlp stdout: ${result.stdout.slice(0, 2000)}`);
  } catch (err) {
    // Surface full stdout/stderr for diagnosis rather than just the generic message
    const details = [err.stdout, err.stderr].filter(Boolean).join('\n').slice(0, 3000);
    throw new Error(`yt-dlp failed for ${video.id}: ${err.message}\n--- DETAILS ---\n${details}`);
  }

  if (!fs.existsSync(expectedOutput)) {
    throw new Error(`Expected output file not found after download: ${expectedOutput}`);
  }

  const sizeMB = (fs.statSync(expectedOutput).size / 1024 / 1024).toFixed(1);
  log('info', `Audio downloaded: ${sizeMB}MB`);

  return expectedOutput;
}
