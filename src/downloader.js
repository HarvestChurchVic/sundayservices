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
    '--output', outputTemplate,
    '--no-playlist',
    '--quiet',
    '--no-warnings',
    // Embed thumbnail as cover art
    '--embed-thumbnail',
    // Write metadata
    '--add-metadata',
    // Retry on transient errors
    '--retries', '3',
    '--fragment-retries', '3',
  ];

  log('info', `Downloading audio for video ${video.id} at ${bitrate}kbps...`);

  try {
    await execFileAsync('yt-dlp', args, {
      timeout: 10 * 60 * 1000, // 10 minute timeout
    });
  } catch (err) {
    throw new Error(`yt-dlp failed for ${video.id}: ${err.message}`);
  }

  if (!fs.existsSync(expectedOutput)) {
    throw new Error(`Expected output file not found after download: ${expectedOutput}`);
  }

  const sizeMB = (fs.statSync(expectedOutput).size / 1024 / 1024).toFixed(1);
  log('info', `Audio downloaded: ${sizeMB}MB`);

  return expectedOutput;
}
