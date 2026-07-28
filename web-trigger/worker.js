/**
 * Sermon upload trigger Worker.
 *
 * Serves the upload form itself on any GET request, and handles two POST
 * endpoints:
 *   POST /presign  { filename, contentType }  -> { uploadUrl, key }
 *   POST /trigger  { title, speaker, sermonDate, videoKey, thumbnailKey, passphrase }
 *                  -> triggers the "Process Sermon" GitHub Action
 *
 * Since the form is served from this same Worker/domain, form submissions
 * are same-origin and don't need CORS at all. ALLOWED_ORIGIN/CORS headers
 * are kept only as a fallback in case the form is ever also embedded
 * elsewhere (e.g. still on Church Co).
 *
 * Required Worker secrets (set via Cloudflare dashboard -> Worker -> Settings -> Variables):
 *   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME
 *   GITHUB_TOKEN         - the same fine-grained PAT used elsewhere for this repo
 *   FORM_PASSPHRASE      - a simple shared passphrase the form must send, so a
 *                          stumbled-upon URL can't trigger real runs
 *   ALLOWED_ORIGIN       - only needed if embedding the form elsewhere too
 */

const GITHUB_REPO = "HarvestChurchVic/sundayservices";
const GITHUB_WORKFLOW = "process-sermon.yml";

const FORM_HTML = `<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Process a Sermon</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 560px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }
  h1 { font-size: 1.4rem; }
  label { display: block; margin-top: 16px; font-weight: 600; font-size: 0.9rem; }
  input[type="text"], input[type="date"], input[type="password"] {
    width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;
    border: 1px solid #ccc; border-radius: 4px; font-size: 1rem;
  }
  input[type="file"] { margin-top: 6px; }
  button {
    margin-top: 24px; padding: 12px 24px; background: #2b6cb0; color: white;
    border: none; border-radius: 4px; font-size: 1rem; cursor: pointer;
  }
  button:disabled { background: #999; cursor: not-allowed; }
  #status { margin-top: 20px; padding: 12px; border-radius: 4px; display: none; }
  #status.info { background: #ebf8ff; color: #2c5282; display: block; }
  #status.success { background: #f0fff4; color: #22543d; display: block; }
  #status.error { background: #fff5f5; color: #822727; display: block; }
  .hint { font-weight: normal; color: #666; font-size: 0.8rem; }
</style>
</head>
<body>

<h1>Process a Sermon</h1>

<form id="sermonForm">
  <label>Sermon title
    <input type="text" id="title" required>
  </label>

  <label>Speaker
    <input type="text" id="speaker" required value="Andrew Cartledge">
  </label>

  <label>Sermon date
    <input type="date" id="sermonDate" required>
  </label>

  <label>YouTube link <span class="hint">(optional, if already published)</span>
    <input type="text" id="youtubeUrl">
  </label>

  <label>Sermon video file
    <input type="file" id="videoFile" accept="video/*" required>
  </label>

  <label>Thumbnail image <span class="hint">(optional, PNG recommended)</span>
    <input type="file" id="thumbnailFile" accept="image/png,image/jpeg">
  </label>

  <label>Passphrase
    <input type="password" id="passphrase" required>
  </label>

  <button type="submit" id="submitBtn">Upload &amp; Process</button>
</form>

<div id="status"></div>

<script>
// Same-origin since this form is served directly by the Worker
const WORKER_URL = "";

const form = document.getElementById("sermonForm");
const statusDiv = document.getElementById("status");
const submitBtn = document.getElementById("submitBtn");

function setStatus(message, type) {
  statusDiv.textContent = message;
  statusDiv.className = type;
}

async function getPresignedUrl(file) {
  const resp = await fetch(\`\${WORKER_URL}/presign\`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, contentType: file.type }),
  });
  if (!resp.ok) throw new Error(\`Failed to get upload URL: \${await resp.text()}\`);
  return resp.json();
}

async function uploadFile(file, uploadUrl) {
  const resp = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  if (!resp.ok) throw new Error(\`Upload failed: \${resp.status}\`);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  submitBtn.disabled = true;

  const title = document.getElementById("title").value;
  const speaker = document.getElementById("speaker").value;
  const sermonDate = document.getElementById("sermonDate").value;
  const youtubeUrl = document.getElementById("youtubeUrl").value;
  const videoFile = document.getElementById("videoFile").files[0];
  const thumbnailFile = document.getElementById("thumbnailFile").files[0];
  const passphrase = document.getElementById("passphrase").value;

  try {
    setStatus("Requesting upload link for video...", "info");
    const { uploadUrl: videoUploadUrl, key: videoKey } = await getPresignedUrl(videoFile);

    setStatus(\`Uploading video (\${(videoFile.size / 1e6).toFixed(0)} MB)... this may take a while.\`, "info");
    await uploadFile(videoFile, videoUploadUrl);

    let thumbnailKey = null;
    if (thumbnailFile) {
      setStatus("Uploading thumbnail...", "info");
      const { uploadUrl: thumbUploadUrl, key: thumbKey } = await getPresignedUrl(thumbnailFile);
      await uploadFile(thumbnailFile, thumbUploadUrl);
      thumbnailKey = thumbKey;
    }

    setStatus("Starting the processing pipeline...", "info");
    const triggerResp = await fetch(\`\${WORKER_URL}/trigger\`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title, speaker, sermonDate, youtubeUrl,
        videoKey, thumbnailKey, passphrase,
      }),
    });

    if (!triggerResp.ok) {
      throw new Error(\`Failed to start processing: \${await triggerResp.text()}\`);
    }

    setStatus("Success! Processing has started — you'll get an email when it's done (usually 5-15 minutes).", "success");
    form.reset();
  } catch (err) {
    setStatus(\`Error: \${err.message}\`, "error");
  } finally {
    submitBtn.disabled = false;
  }
});
</script>

</body>
</html>
`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const corsHeaders = {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      if (request.method === "GET") {
        return new Response(FORM_HTML, {
          headers: { "Content-Type": "text/html; charset=UTF-8" },
        });
      }
      if (url.pathname === "/presign" && request.method === "POST") {
        return await handlePresign(request, env, corsHeaders);
      }
      if (url.pathname === "/trigger" && request.method === "POST") {
        return await handleTrigger(request, env, corsHeaders);
      }
      return new Response("Not found", { status: 404, headers: corsHeaders });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
  },
};

async function handlePresign(request, env, corsHeaders) {
  const { filename, contentType } = await request.json();
  if (!filename) {
    return jsonResponse({ error: "filename is required" }, 400, corsHeaders);
  }

  const key = `raw-uploads/${Date.now()}-${sanitizeFilename(filename)}`;
  const uploadUrl = await presignR2PutUrl(env, key, contentType || "application/octet-stream");

  return jsonResponse({ uploadUrl, key }, 200, corsHeaders);
}

async function handleTrigger(request, env, corsHeaders) {
  const body = await request.json();
  const { title, speaker, sermonDate, videoKey, thumbnailKey, passphrase } = body;

  if (env.FORM_PASSPHRASE && passphrase !== env.FORM_PASSPHRASE) {
    return jsonResponse({ error: "Incorrect passphrase" }, 401, corsHeaders);
  }
  if (!title || !speaker || !sermonDate || !videoKey) {
    return jsonResponse({ error: "title, speaker, sermonDate, and videoKey are required" }, 400, corsHeaders);
  }

  // If a thumbnail was uploaded, copy it into the images/ folder under the
  // same base name as the video, which is what the pipeline looks for.
  if (thumbnailKey) {
    const videoBaseName = videoKey.split("/").pop().replace(/\.[^.]+$/, "");
    await copyR2Object(env, thumbnailKey, `images/${videoBaseName}.png`);
  }

  const dispatchUrl = `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/${GITHUB_WORKFLOW}/dispatches`;
  const resp = await fetch(dispatchUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "sermon-upload-worker",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: {
        youtube_url: body.youtubeUrl || "",
        title,
        speaker,
        sermon_date: sermonDate,
        source_file: videoKey,
      },
    }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    return jsonResponse({ error: `GitHub dispatch failed: ${resp.status} ${text}` }, 500, corsHeaders);
  }

  return jsonResponse({ ok: true }, 200, corsHeaders);
}

function jsonResponse(obj, status, corsHeaders) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function sanitizeFilename(name) {
  return name.replace(/[^A-Za-z0-9._-]/g, "_");
}

// ---------------------------------------------------------------------------
// R2 object copy (server-side, small metadata operation — not the file bytes
// passing through more than once; used only for the small thumbnail image)
// ---------------------------------------------------------------------------

async function copyR2Object(env, sourceKey, destKey) {
  const host = `${env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`;
  const copySource = `/${env.R2_BUCKET_NAME}/${encodeURIComponent(sourceKey)}`;
  const { url, headers } = await signRequest(env, "PUT", destKey, {
    "x-amz-copy-source": copySource,
  });
  const resp = await fetch(url, { method: "PUT", headers });
  if (!resp.ok) {
    throw new Error(`R2 copy failed: ${resp.status} ${await resp.text()}`);
  }
}

// ---------------------------------------------------------------------------
// AWS SigV4 presigned URL generation (no external dependencies, so this
// pastes directly into the Cloudflare dashboard's Worker editor)
// ---------------------------------------------------------------------------

async function presignR2PutUrl(env, key, contentType) {
  const { url } = await signRequest(env, "PUT", key, {}, contentType);
  return url;
}

async function signRequest(env, method, key, extraSignedHeaders = {}, contentType = null) {
  const accessKeyId = env.R2_ACCESS_KEY_ID;
  const secretAccessKey = env.R2_SECRET_ACCESS_KEY;
  const accountId = env.R2_ACCOUNT_ID;
  const bucket = env.R2_BUCKET_NAME;
  const host = `${accountId}.r2.cloudflarestorage.com`;
  const region = "auto";
  const service = "s3";

  const now = new Date();
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  const dateStamp = amzDate.slice(0, 8);
  const credentialScope = `${dateStamp}/${region}/${service}/aws4_request`;

  const encodedKey = key.split("/").map(encodeURIComponent).join("/");
  const canonicalUri = `/${bucket}/${encodedKey}`;

  const isPresigned = Object.keys(extraSignedHeaders).length === 0;

  if (isPresigned) {
    // Query-string presigned URL (used for the direct browser upload)
    const expires = 900; // 15 minutes
    const queryParams = {
      "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
      "X-Amz-Credential": `${accessKeyId}/${credentialScope}`,
      "X-Amz-Date": amzDate,
      "X-Amz-Expires": String(expires),
      "X-Amz-SignedHeaders": "host",
    };
    const canonicalQuerystring = Object.keys(queryParams)
      .sort()
      .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(queryParams[k])}`)
      .join("&");

    const canonicalHeaders = `host:${host}\n`;
    const signedHeaders = "host";
    const payloadHash = "UNSIGNED-PAYLOAD";

    const canonicalRequest = [
      method,
      canonicalUri,
      canonicalQuerystring,
      canonicalHeaders,
      signedHeaders,
      payloadHash,
    ].join("\n");

    const stringToSign = [
      "AWS4-HMAC-SHA256",
      amzDate,
      credentialScope,
      await sha256Hex(canonicalRequest),
    ].join("\n");

    const signature = await hmacHex(
      await getSigningKey(secretAccessKey, dateStamp, region, service),
      stringToSign
    );

    const url = `https://${host}${canonicalUri}?${canonicalQuerystring}&X-Amz-Signature=${signature}`;
    return { url, headers: {} };
  }

  // Header-based signing (used for the small server-side copy operation)
  const headersToSign = { host, ...extraSignedHeaders };
  if (contentType) headersToSign["content-type"] = contentType;
  const sortedHeaderKeys = Object.keys(headersToSign).sort();
  const canonicalHeaders = sortedHeaderKeys.map((k) => `${k}:${headersToSign[k]}\n`).join("");
  const signedHeaders = sortedHeaderKeys.join(";");
  const payloadHash = "UNSIGNED-PAYLOAD";

  const canonicalRequest = [
    method,
    canonicalUri,
    "",
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");

  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    await sha256Hex(canonicalRequest),
  ].join("\n");

  const signature = await hmacHex(
    await getSigningKey(secretAccessKey, dateStamp, region, service),
    stringToSign
  );

  const authHeader = `AWS4-HMAC-SHA256 Credential=${accessKeyId}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;

  const responseHeaders = {
    Authorization: authHeader,
    "x-amz-date": amzDate,
    "x-amz-content-sha256": payloadHash,
    ...extraSignedHeaders,
  };
  if (contentType) responseHeaders["content-type"] = contentType;

  return { url: `https://${host}${canonicalUri}`, headers: responseHeaders };
}

async function sha256Hex(message) {
  const data = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return bufferToHex(hashBuffer);
}

async function hmac(key, message) {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    typeof key === "string" ? new TextEncoder().encode(key) : key,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return crypto.subtle.sign("HMAC", cryptoKey, new TextEncoder().encode(message));
}

async function hmacHex(key, message) {
  const sig = await hmac(key, message);
  return bufferToHex(sig);
}

async function getSigningKey(secretAccessKey, dateStamp, region, service) {
  const kDate = await hmac(`AWS4${secretAccessKey}`, dateStamp);
  const kRegion = await hmac(kDate, region);
  const kService = await hmac(kRegion, service);
  const kSigning = await hmac(kService, "aws4_request");
  return kSigning;
}

function bufferToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
