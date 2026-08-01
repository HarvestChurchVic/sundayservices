/**
 * Sermon upload trigger Worker.
 *
 * Serves the upload form itself on any GET request, and handles two POST
 * endpoints:
 *   POST /presign  { filename, contentType, passphrase }  -> { uploadUrl, key }
 *   POST /trigger  { title, speaker, sermonDate, videoKey, thumbnailKey, passphrase }
 *                  -> triggers the "Process Sermon" GitHub Action
 *
 * Since the form is served from this same Worker/domain, form submissions
 * are same-origin and don't need CORS at all. ALLOWED_ORIGIN/CORS headers
 * are kept only as a fallback in case the form is ever also embedded
 * elsewhere.
 *
 * Both /presign and /trigger require the passphrase — /presign hands out
 * real upload permissions to the bucket, so it needs to be gated too, not
 * just the final trigger step.
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
  h1 { font-size: 1.4rem; text-align: center; }
  .logo { display: block; margin: 0 auto 12px; width: 80px; height: 80px; }
  label { display: block; margin-top: 16px; font-weight: 600; font-size: 0.9rem; }
  input[type="text"], input[type="date"], input[type="password"], select {
    width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;
    border: 1px solid #ccc; border-radius: 4px; font-size: 1rem;
    background: white;
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

<img class="logo" src="data:image/png;base64,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" alt="Logo">
<h1>Process a Sermon</h1>

<form id="sermonForm">
  <label>Sermon title
    <input type="text" id="title" required>
  </label>

  <label>Speaker
    <select id="speaker" required>
      <option value="Ps Andrew Cartledge">Ps Andrew Cartledge</option>
      <option value="Ps Rachel Cartledge">Ps Rachel Cartledge</option>
      <option value="Ps Keith Ainge">Ps Keith Ainge</option>
      <option value="Ps Caleb McLaughlin">Ps Caleb McLaughlin</option>
      <option value="Ps Ruth Emmerson">Ps Ruth Emmerson</option>
      <option value="Ps Ron Spence">Ps Ron Spence</option>
      <option value="Ps Greg McKinnon">Ps Greg McKinnon</option>
      <option value="Guest Speaker">Guest Speaker</option>
    </select>
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

  <label>Thumbnail image <span class="hint">(optional, PNG only)</span>
    <input type="file" id="thumbnailFile" accept="image/png">
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

async function getPresignedUrl(file, passphrase) {
  const resp = await fetch(\`\${WORKER_URL}/presign\`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, contentType: file.type, passphrase }),
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

  if (thumbnailFile && thumbnailFile.type !== "image/png") {
    setStatus("Error: the thumbnail must be a PNG file.", "error");
    submitBtn.disabled = false;
    return;
  }
  if (!passphrase) {
    setStatus("Error: passphrase is required.", "error");
    submitBtn.disabled = false;
    return;
  }

  try {
    setStatus("Requesting upload link for video...", "info");
    const { uploadUrl: videoUploadUrl, key: videoKey } = await getPresignedUrl(videoFile, passphrase);

    setStatus(\`Uploading video (\${(videoFile.size / 1e6).toFixed(0)} MB)... this may take a while.\`, "info");
    await uploadFile(videoFile, videoUploadUrl);

    let thumbnailKey = null;
    if (thumbnailFile) {
      setStatus("Uploading thumbnail...", "info");
      const { uploadUrl: thumbUploadUrl, key: thumbKey } = await getPresignedUrl(thumbnailFile, passphrase);
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

const STATUS_HTML = `<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sermon Pipeline Status</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 720px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }
  h1 { font-size: 1.4rem; text-align: center; }
  #gate { text-align: center; margin-top: 40px; }
  #gate input { padding: 10px; font-size: 1rem; border: 1px solid #ccc; border-radius: 4px; width: 220px; }
  #gate button { padding: 10px 20px; margin-left: 8px; background: #2b6cb0; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 24px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; font-size: 0.9rem; }
  th { color: #666; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }
  .status-success { color: #22543d; }
  .status-failure { color: #822727; }
  .status-duplicate_skipped { color: #975a16; }
  a { color: #2b6cb0; }
  #error { color: #822727; text-align: center; margin-top: 16px; }
  #empty { text-align: center; color: #999; margin-top: 40px; }
</style>
</head>
<body>

<h1>Sermon Pipeline Status</h1>

<div id="gate">
  <input type="password" id="passphrase" placeholder="Passphrase">
  <button id="loadBtn">View Status</button>
  <div id="error"></div>
</div>

<div id="results" style="display:none;">
  <table>
    <thead>
      <tr><th>Date</th><th>Title</th><th>Speaker</th><th>Status</th><th>Run</th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <div id="empty" style="display:none;">No runs recorded yet.</div>
</div>

<script>
document.getElementById("loadBtn").addEventListener("click", async () => {
  const passphrase = document.getElementById("passphrase").value;
  const errorDiv = document.getElementById("error");
  errorDiv.textContent = "";
  try {
    const resp = await fetch("/status-data", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      errorDiv.textContent = data.error || "Something went wrong.";
      return;
    }
    const rows = document.getElementById("rows");
    rows.innerHTML = "";
    const runs = (data.runs || []).slice().reverse();
    if (runs.length === 0) {
      document.getElementById("empty").style.display = "block";
    } else {
      document.getElementById("empty").style.display = "none";
      for (const run of runs) {
        const tr = document.createElement("tr");
        const icon = run.status === "success" ? "✅" : run.status === "duplicate_skipped" ? "⚠️" : "❌";
        const label = run.status === "success" ? "Success" : run.status === "duplicate_skipped" ? "Skipped (duplicate)" : "Failed";
        tr.innerHTML = \`
          <td>\${run.sermon_date || ""}</td>
          <td>\${run.title || ""}</td>
          <td>\${run.speaker || ""}</td>
          <td class="status-\${run.status}">\${icon} \${label}</td>
          <td>\${run.run_url ? \`<a href="\${run.run_url}" target="_blank">View</a>\` : ""}</td>
        \`;
        rows.appendChild(tr);
      }
    }
    document.getElementById("gate").style.display = "none";
    document.getElementById("results").style.display = "block";
  } catch (err) {
    errorDiv.textContent = "Error: " + err.message;
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
      if (request.method === "GET" && url.pathname === "/status") {
        return new Response(STATUS_HTML, {
          headers: { "Content-Type": "text/html; charset=UTF-8" },
        });
      }
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
      if (url.pathname === "/status-data" && request.method === "POST") {
        return await handleStatusData(request, env, corsHeaders);
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
  const { filename, contentType, passphrase } = await request.json();
  if (env.FORM_PASSPHRASE && passphrase !== env.FORM_PASSPHRASE) {
    return jsonResponse({ error: "Incorrect passphrase" }, 401, corsHeaders);
  }
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
    await deleteR2Object(env, thumbnailKey);
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

async function handleStatusData(request, env, corsHeaders) {
  const { passphrase } = await request.json();
  if (env.FORM_PASSPHRASE && passphrase !== env.FORM_PASSPHRASE) {
    return jsonResponse({ error: "Incorrect passphrase" }, 401, corsHeaders);
  }

  const historyUrl = `https://raw.githubusercontent.com/${GITHUB_REPO}/main/run_history.json`;
  const resp = await fetch(historyUrl, { cf: { cacheTtl: 0 } });
  if (!resp.ok) {
    // No runs recorded yet is not an error — just means an empty list
    if (resp.status === 404) {
      return jsonResponse({ runs: [] }, 200, corsHeaders);
    }
    return jsonResponse({ error: `Failed to load run history: ${resp.status}` }, 500, corsHeaders);
  }
  const runs = await resp.json();
  return jsonResponse({ runs }, 200, corsHeaders);
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

async function deleteR2Object(env, key) {
  const { url, headers } = await signRequest(env, "DELETE", key, { "x-delete-marker": "true" });
  const resp = await fetch(url, { method: "DELETE", headers });
  if (!resp.ok) {
    // Not fatal — a leftover raw file is harmless clutter, not a broken run
    console.log(`Warning: failed to delete ${key}: ${resp.status}`);
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
