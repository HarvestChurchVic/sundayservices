/**
 * Generates a valid podcast RSS 2.0 feed with iTunes namespace extensions.
 * Compatible with Spotify for Podcasters, Apple Podcasts, and all major directories.
 */

function escapeXml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function formatRFC2822(dateStr) {
  return new Date(dateStr).toUTCString();
}

function formatDuration(seconds) {
  if (!seconds) return '00:00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
}

export function generateRSS(episodes) {
  const {
    PODCAST_TITLE,
    PODCAST_DESCRIPTION,
    PODCAST_AUTHOR,
    PODCAST_EMAIL,
    PODCAST_LANGUAGE,
    PODCAST_CATEGORY,
    PODCAST_SUBCATEGORY,
    PODCAST_IMAGE_URL,
    PODCAST_EXPLICIT,
    GITHUB_USERNAME,
    GITHUB_REPO,
  } = process.env;

  const feedUrl = `https://${GITHUB_USERNAME}.github.io/${GITHUB_REPO}/feed.xml`;
  const siteUrl = `https://${GITHUB_USERNAME}.github.io/${GITHUB_REPO}/`;
  const lastBuild = new Date().toUTCString();

  const subcategoryTag = PODCAST_SUBCATEGORY
    ? `\n        <itunes:category text="${escapeXml(PODCAST_SUBCATEGORY)}" />`
    : '';

  const items = episodes.map(ep => {
    // Truncate description to 4000 chars to stay within RSS limits
    const desc = ep.description
      ? ep.description.slice(0, 4000) + (ep.description.length > 4000 ? '...' : '')
      : `Originally published on YouTube: ${ep.youtubeUrl}`;

    return `
    <item>
      <title>${escapeXml(ep.title)}</title>
      <description><![CDATA[${desc}\n\nOriginal video: ${ep.youtubeUrl}]]></description>
      <itunes:summary><![CDATA[${desc}]]></itunes:summary>
      <enclosure url="${escapeXml(ep.audioUrl)}" length="${ep.fileSize || 0}" type="audio/mpeg" />
      <guid isPermaLink="false">${escapeXml(ep.id)}</guid>
      <pubDate>${formatRFC2822(ep.pubDate)}</pubDate>
      <itunes:duration>${formatDuration(ep.durationSeconds)}</itunes:duration>
      <itunes:explicit>${PODCAST_EXPLICIT === 'yes' ? 'true' : 'false'}</itunes:explicit>
      <itunes:episodeType>full</itunes:episodeType>
      <link>${escapeXml(ep.youtubeUrl)}</link>
    </item>`;
  }).join('\n');

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:atom="http://www.w3.org/2005/Atom">

  <channel>
    <title>${escapeXml(PODCAST_TITLE)}</title>
    <description>${escapeXml(PODCAST_DESCRIPTION)}</description>
    <link>${siteUrl}</link>
    <language>${PODCAST_LANGUAGE || 'en'}</language>
    <lastBuildDate>${lastBuild}</lastBuildDate>
    <atom:link href="${feedUrl}" rel="self" type="application/rss+xml" />

    <itunes:title>${escapeXml(PODCAST_TITLE)}</itunes:title>
    <itunes:author>${escapeXml(PODCAST_AUTHOR)}</itunes:author>
    <itunes:summary>${escapeXml(PODCAST_DESCRIPTION)}</itunes:summary>
    <itunes:explicit>${PODCAST_EXPLICIT === 'yes' ? 'true' : 'false'}</itunes:explicit>
    <itunes:owner>
      <itunes:name>${escapeXml(PODCAST_AUTHOR)}</itunes:name>
      <itunes:email>${escapeXml(PODCAST_EMAIL)}</itunes:email>
    </itunes:owner>
    <itunes:image href="${escapeXml(PODCAST_IMAGE_URL)}" />
    <image>
      <url>${escapeXml(PODCAST_IMAGE_URL)}</url>
      <title>${escapeXml(PODCAST_TITLE)}</title>
      <link>${siteUrl}</link>
    </image>
    <itunes:category text="${escapeXml(PODCAST_CATEGORY)}">${subcategoryTag}
    </itunes:category>
    <itunes:type>episodic</itunes:type>
    ${items}
  </channel>
</rss>`;
}
