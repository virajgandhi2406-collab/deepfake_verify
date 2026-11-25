const API_BASE = window.location.origin;

const state = {
  page: location.hash.slice(1) || "verify",
  selectedFile: null,
  mediaKind: "image",
  lastResult: null,
  stats: null,
  history: [],
};

const $ = (id) => document.getElementById(id);

function escapeHtml(val) {
  return String(val ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[ch]));
}

function showToast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    el.style.display = "none";
  }, 3400);
}

function verdictPill(verdict) {
  const v = String(verdict || "Inconclusive");
  let cls = "info";
  if (v === "Low Evidence" || v === "Authentic") cls = "good";
  else if (v === "Review Required" || v === "Potentially Manipulated") cls = "warn";
  else if (v === "Elevated Evidence of Manipulation" || v === "Manipulated" || v === "AI-Generated") cls = "risk";
  return `<span class="pill ${cls}">${escapeHtml(v)}</span>`;
}

function anchorPill(status) {
  const s = String(status || "unknown");
  let cls = "info";
  if (s === "anchored" || s === "already_anchored" || s === "verified") cls = "good";
  else if (s.includes("pending") || s === "local_only_legacy" || s === "unanchored") cls = "warn";
  else if (s.includes("mismatch") || s.includes("truncation") || s.includes("rewrite") || s === "error") cls = "risk";
  return `<span class="pill ${cls}">${escapeHtml(s)}</span>`;
}

function shortHex(val, n = 16) {
  if (!val) return "N/A";
  const s = String(val);
  return s.length > n + 6 ? `${s.slice(0, n)}...${s.slice(-4)}` : s;
}

async function fetchJson(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, opts);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    throw new Error(data?.detail || `HTTP ${res.status}`);
  }
  return data;
}

async function refreshTopStatus() {
  try {
    const [bc, health] = await Promise.all([
      fetchJson("/blockchain/status"),
      fetchJson("/health"),
    ]);
    $("topDetectorVersion").textContent = `${health.detector_version}:${health.config_id}`;
    const dot = $("topChainDot");
    const txt = $("topChainText");
    if (bc.connected && bc.contract_deployed) {
      dot.className = "dot online";
      txt.textContent = `${bc.network_name} (Chain ${bc.actual_chain_id}) | Block #${bc.latest_block}`;
    } else if (bc.connected) {
      dot.className = "dot warn";
      txt.textContent = `${bc.network_name} connected | Contract not deployed`;
    } else {
      dot.className = "dot offline";
      txt.textContent = "Blockchain offline (local ledger active)";
    }
  } catch {
    $("topChainDot").className = "dot offline";
    $("topChainText").textContent = "API unreachable";
  }
}

function setActiveNav(page) {
  document.querySelectorAll(".nav-btn[data-page]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });
}

function navigate(page) {
  state.page = page;
  if (location.hash.slice(1) !== page) {
    location.hash = page;
  }
  renderCurrentPage();
}

function renderCurrentPage() {
  const page = state.page;
  setActiveNav(page);
  const renderers = {
    dashboard: renderDashboardView,
    verify: renderVerifyView,
    explorer: renderExplorerView,
    history: renderHistoryView,
    integrity: renderIntegrityView,
    research: renderResearchView,
    about: renderAboutView,
  };
  const fn = renderers[page] || renderDashboardView;
  fn();
}

/* ==================== A. DASHBOARD ==================== */
async function renderDashboardView() {
  const root = $("pageContent");
  root.innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Live Telemetry &amp; Audit Overview</div>
        <h1>VERIFY Verification Dashboard</h1>
        <p class="view-sub">Real-time metrics derived directly from the canonical audit ledger and deployed Ethereum smart contract.</p>
      </div>
      <div style="display:flex;gap:10px">
        <button type="button" class="btn primary" id="goVerifyBtn">Verify Media</button>
        <button type="button" class="btn" id="refreshDashBtn">Refresh</button>
      </div>
    </div>
    <div id="dashBody"><div class="panel">Loading live verification telemetry...</div></div>
  `;
  $("goVerifyBtn").addEventListener("click", () => navigate("verify"));
  $("refreshDashBtn").addEventListener("click", renderDashboardView);

  try {
    const [stats, history] = await Promise.all([fetchJson("/stats"), fetchJson("/history")]);
    state.stats = stats;
    state.history = history;
    const bc = stats.blockchain || {};
    const cp = stats.latest_checkpoint;
    const vc = stats.verdict_counts || {};

    $("dashBody").innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Total Verification Events</div><div class="kpi-value">${stats.total_verification_events}</div><div class="kpi-foot">${stats.unique_content_files} unique content SHA-256 files</div></div>
        <div class="kpi-card"><div class="kpi-label">Media Breakdown</div><div class="kpi-value">${stats.images_analyzed} / ${stats.videos_analyzed}</div><div class="kpi-foot">Images analyzed / Videos analyzed</div></div>
        <div class="kpi-card"><div class="kpi-label">Low Evidence</div><div class="kpi-value" style="color:#4ade80">${vc.low_evidence ?? 0}</div><div class="kpi-foot">Baseline score &lt; 0.35</div></div>
        <div class="kpi-card"><div class="kpi-label">Review Required</div><div class="kpi-value" style="color:#fbbf24">${vc.review_required ?? 0}</div><div class="kpi-foot">Baseline score 0.35–0.65</div></div>
        <div class="kpi-card"><div class="kpi-label">Elevated Evidence</div><div class="kpi-value" style="color:#fb7185">${vc.elevated_evidence ?? 0}</div><div class="kpi-foot">Score &ge; 0.65 or provenance marker</div></div>
        <div class="kpi-card"><div class="kpi-label">Blockchain Records</div><div class="kpi-value" style="color:#60a5fa">${bc.total_on_chain_records ?? 0}</div><div class="kpi-foot">${stats.pending_blockchain_anchors} local pending anchor(s)</div></div>
      </div>

      <div class="grid-2">
        <section class="panel">
          <div class="panel-head">
            <h2>Recent Verification Activity</h2>
            <button type="button" class="btn" id="viewAllHistBtn">Open History</button>
          </div>
          <div class="table-wrapper">
            <table class="data-table">
              <thead><tr><th>Seq</th><th>ID / Label</th><th>Type</th><th>Verdict</th><th>Blockchain</th></tr></thead>
              <tbody>
                ${history.slice().reverse().slice(0, 8).map((r) => {
                  const seq = r.sequence_number ?? r.index;
                  const verId = r.verification_id || (r.block_hash || "").slice(0, 16);
                  const media = r.media_type || r.ai_result?.media_type || "image";
                  const verdict = r.verdict || r.ai_result?.authenticity_flag;
                  const bcStatus = r.blockchain?.anchoring_status || "legacy_local";
                  return `<tr>
                    <td class="mono">#${seq}</td>
                    <td><div class="mono">${escapeHtml(verId)}</div><small style="color:var(--text-dim)">${escapeHtml(r.filename)}</small></td>
                    <td>${escapeHtml(media)}</td>
                    <td>${verdictPill(verdict)}</td>
                    <td>${anchorPill(bcStatus)}</td>
                  </tr>`;
                }).join("")}
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>Latest Blockchain Checkpoint &amp; Node</h2>
            <button type="button" class="btn" id="openExplorerBtn">Explorer</button>
          </div>
          <div class="kv-list">
            <div class="kv-row"><span class="kv-key">Network</span><span class="kv-val">${escapeHtml(bc.network_name)} (Chain ID: ${escapeHtml(bc.actual_chain_id ?? bc.configured_chain_id)})</span></div>
            <div class="kv-row"><span class="kv-key">Node Connection</span><span class="kv-val">${bc.connected ? '<span class="pill good">Connected</span>' : '<span class="pill risk">Disconnected</span>'}</span></div>
            <div class="kv-row"><span class="kv-key">Contract Address</span><span class="kv-val mono">${escapeHtml(bc.contract_address || "Not deployed")}</span></div>
            <div class="kv-row"><span class="kv-key">Latest EVM Block</span><span class="kv-val mono">${bc.latest_block ?? "N/A"}</span></div>
            <div class="kv-row"><span class="kv-key">Total Checkpoints</span><span class="kv-val">${bc.total_checkpoints ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Latest Checkpoint</span><span class="kv-val">${cp ? `#${cp.checkpoint_id} (${cp.record_count} records)` : "None anchored yet"}</span></div>
            <div class="kv-row"><span class="kv-key">Anchored Root Hash</span><span class="kv-val mono">${escapeHtml(shortHex(cp?.ledger_root_hash || bc.latest_anchored_ledger_hash, 22))}</span></div>
          </div>
        </section>
      </div>
    `;
    $("viewAllHistBtn").addEventListener("click", () => navigate("history"));
    $("openExplorerBtn").addEventListener("click", () => navigate("explorer"));
  } catch (err) {
    $("dashBody").innerHTML = `<div class="notice-box risk">Failed to load dashboard telemetry: ${escapeHtml(err.message)}</div>`;
  }
}

/* ==================== B & C. VERIFY CONTENT + QUICK CHECK ==================== */
function renderVerifyView() {
  const root = $("pageContent");
  root.innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Simple 1-Step Media Verification</div>
        <h1>Verify or Check Image &amp; Video</h1>
        <p class="view-sub">Upload any image or video to check for manipulation, see if it was verified before, and save a tamper-evident record.</p>
      </div>
    </div>

    <div class="grid-2">
      <section class="panel">
        <div class="panel-head">
          <h2>1. Upload Image or Video to Verify</h2>
          <div style="display:flex;gap:8px" aria-label="Detected media type">
            <button type="button" class="btn ${state.mediaKind === "image" ? "primary" : ""}" id="modeImageBtn">Image</button>
            <button type="button" class="btn ${state.mediaKind === "video" ? "primary" : ""}" id="modeVideoBtn">Video</button>
          </div>
        </div>

        <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Upload image or video for verification">
          <div>
            <h3 id="dropTitle">Drop any image or video here, or click to choose</h3>
            <p id="dropHint" style="color:var(--text-muted);margin:6px 0">Images: JPG, PNG, WEBP, BMP, TIFF (max 25 MB) &bull; Videos: MP4, MOV, AVI, MKV, WEBM (max 100 MB)</p>
            <img id="imgPreview" class="preview-media" alt="Uploaded image preview">
            <video id="vidPreview" class="preview-media" controls></video>
          </div>
        </div>

        <div style="margin-top:16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <button type="button" class="btn primary" id="runVerifyBtn" ${state.selectedFile ? "" : "disabled"}>Verify Media Now</button>
          <button type="button" class="btn" id="clearFileBtn">Clear</button>
        </div>

        <details class="advanced-box">
          <summary>Advanced: Detector Version / Re-analysis Settings (Optional)</summary>
          <div class="form-grid" style="margin-top:12px">
            <div class="field">
              <label for="detectorVersionInput">Detector Version</label>
              <input class="input mono" id="detectorVersionInput" placeholder="Default: heuristic-v2.0.0">
            </div>
            <div class="field">
              <label for="configIdInput">Analysis Config ID</label>
              <input class="input mono" id="configIdInput" placeholder="Default: cfg-2026.11-baseline">
            </div>
          </div>
        </details>

        <div id="verifyProgressWrap" style="display:none;margin-top:16px">
          <div id="verifyStageText" style="font-size:12px;color:var(--blue-bright);margin-bottom:6px">Analyzing media...</div>
          <progress class="verity-progress" id="verifyProgress"></progress>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>2. Quick Check: Was This Already Verified?</h2>
        </div>
        <p style="color:var(--text-muted);margin-top:4px">
          Check if an image, video, SHA-256 hash, or Verification ID (<code>VRT-...</code>) already exists in VERIFY without creating a new record.
        </p>

        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">
          <button type="button" class="btn" id="quickCheckFileBtn">Choose File to Check (No Upload)</button>
        </div>

        <div class="field" style="margin-top:14px">
          <label for="quickCheckInput">Or paste Verification ID (VRT-...) or SHA-256 hash</label>
          <div style="display:flex;gap:8px">
            <input class="input mono" id="quickCheckInput" placeholder="e.g. VRT-0001-ABCD1234 or 64-char SHA-256">
            <button type="button" class="btn primary" id="quickCheckBtn">Check</button>
          </div>
        </div>

        <div id="quickCheckResult" style="margin-top:14px"></div>
      </section>
    </div>

    <div id="verificationResultArea">${state.lastResult ? renderResultHtml(state.lastResult) : ""}</div>

    <details class="advanced-box">
      <summary>How VERIFY Works &amp; Interpretation Notes</summary>
      <div class="kv-list" style="margin-top:10px">
        <div class="kv-row"><span class="kv-key">1. Manipulation Check</span><span class="kv-val">Noise residual R = I &minus; G&sigma;(I), 4&times;4 edge density, EXIF inspection (samples up to 12 frames for videos)</span></div>
        <div class="kv-row"><span class="kv-key">2. File Identity</span><span class="kv-val">Exact SHA-256 on raw bytes + 64-bit DCT perceptual hash for visual similarity</span></div>
        <div class="kv-row"><span class="kv-key">3. Record Integrity</span><span class="kv-val">Saved in canonical local ledger and anchored on Ethereum/Ganache smart contract when connected</span></div>
      </div>
      <div class="notice-box warn" style="margin-top:12px">
        <strong>Note:</strong> Heuristic scores are baseline forensic indicators, not absolute proof of manipulation. Blockchain anchoring proves record existence and tamper-evidence, not that an original camera capture was truthful.
      </div>
    </details>
  `;

  $("modeImageBtn").addEventListener("click", () => {
    state.mediaKind = "image";
    renderVerifyView();
  });
  $("modeVideoBtn").addEventListener("click", () => {
    state.mediaKind = "video";
    renderVerifyView();
  });

  const fileInput = $("fileInput");
  const dropzone = $("dropzone");
  fileInput.accept = "image/*,video/*";

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
    if (e.dataTransfer.files[0]) handleSelectedFile(e.dataTransfer.files[0]);
  });
  fileInput.onchange = (e) => {
    if (e.target.files[0]) handleSelectedFile(e.target.files[0]);
  };
  $("clearFileBtn").addEventListener("click", () => {
    state.selectedFile = null;
    state.lastResult = null;
    fileInput.value = "";
    renderVerifyView();
  });
  $("runVerifyBtn").addEventListener("click", executeVerification);

  const checkFileInput = $("checkFileInput");
  $("quickCheckFileBtn").addEventListener("click", () => checkFileInput.click());
  checkFileInput.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const out = $("quickCheckResult");
    out.innerHTML = `<div class="notice-box">Computing SHA-256 locally in browser for <strong>${escapeHtml(file.name)}</strong>...</div>`;
    try {
      const buf = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", buf);
      const hex = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
      $("quickCheckInput").value = hex;
      await runQuickCheck(hex);
    } catch (err) {
      out.innerHTML = `<div class="notice-box risk">Failed to hash file: ${escapeHtml(err.message)}</div>`;
    } finally {
      checkFileInput.value = "";
    }
  };
  $("quickCheckBtn").addEventListener("click", () => {
    const val = $("quickCheckInput").value.trim();
    if (val) runQuickCheck(val);
    else showToast("Please select a file or enter a Verification ID / SHA-256 hash.");
  });

  if (state.selectedFile) {
    showSelectedFilePreview(state.selectedFile);
  }
  if (state.lastResult) bindResultActions(state.lastResult);
}

async function runQuickCheck(query) {
  const out = $("quickCheckResult");
  const cleaned = query.trim();
  out.innerHTML = `<div class="notice-box">Checking VERIFY records...</div>`;
  try {
    if (/^VRT-/i.test(cleaned)) {
      const res = await fetchJson(`/verify-certificate/${encodeURIComponent(cleaned)}?format=json`);
      const cert = res.certificate || {};
      const verdict = cert.verdict || res.on_chain_record?.result_category || "N/A";
      out.innerHTML = `
        <div class="notice-box good">
          <strong>Record Found (${escapeHtml(cleaned)})</strong><br>
          Verdict: ${verdictPill(verdict)} &bull; On-Chain: <strong>${res.on_chain_record_found ? "Yes" : "Local Only"}</strong>
          <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
            <a class="btn primary" href="/certificate/${encodeURIComponent(cleaned)}" target="_blank" rel="noopener">Download PDF</a>
          </div>
        </div>
      `;
      return;
    }

    const res = await fetchJson(`/check/${encodeURIComponent(cleaned)}`);
    const bc = res.blockchain || {};
    out.innerHTML = `
      <div class="notice-box good">
        <strong>Exact Match Found!</strong> This file was already verified.<br>
        ID: <code class="mono">${escapeHtml(res.verification_id || "On-Chain")}</code> &bull; Verdict: ${verdictPill(res.verdict || res.on_chain_records?.[0]?.result_category)}<br>
        Blockchain: ${anchorPill(bc.anchoring_status || (res.on_chain_records?.length ? "anchored" : "local_only"))}
        ${res.verification_id ? `<div style="margin-top:8px"><a class="btn primary" href="/certificate/${encodeURIComponent(res.verification_id)}" target="_blank" rel="noopener">Download PDF Certificate</a></div>` : ""}
      </div>
    `;
  } catch (err) {
    if (String(err.message).includes("No verification record") || String(err.message).includes("not found")) {
      out.innerHTML = `<div class="notice-box warn"><strong>Not Found:</strong> No existing verification record matches this file/hash yet. Use <strong>Verify Media Now</strong> on the left to analyze and record it.</div>`;
    } else {
      out.innerHTML = `<div class="notice-box risk">${escapeHtml(err.message)}</div>`;
    }
  }
}

function showSelectedFilePreview(file) {
  $("dropTitle").textContent = `${file.name} (${state.mediaKind.toUpperCase()})`;
  $("dropHint").textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MB ready to verify`;
  const url = URL.createObjectURL(file);
  if (state.mediaKind === "image") {
    $("imgPreview").src = url;
    $("imgPreview").style.display = "block";
    $("vidPreview").style.display = "none";
  } else {
    $("vidPreview").src = url;
    $("vidPreview").style.display = "block";
    $("imgPreview").style.display = "none";
  }
  $("runVerifyBtn").disabled = false;
}

function handleSelectedFile(file) {
  const isImg = file.type.startsWith("image/") || /\.(jpg|jpeg|png|webp|bmp|tif|tiff)$/i.test(file.name);
  const isVid = file.type.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm)$/i.test(file.name);
  if (!isImg && !isVid) {
    showToast("Unsupported file type. Please choose an image (JPG, PNG, WEBP) or video (MP4, MOV, AVI, MKV, WEBM).");
    return;
  }
  state.mediaKind = isVid ? "video" : "image";
  $("modeImageBtn").classList.toggle("primary", state.mediaKind === "image");
  $("modeVideoBtn").classList.toggle("primary", state.mediaKind === "video");
  state.selectedFile = file;
  showSelectedFilePreview(file);
}

async function executeVerification() {
  if (!state.selectedFile) return;
  const btn = $("runVerifyBtn");
  const progWrap = $("verifyProgressWrap");
  const stage = $("verifyStageText");
  btn.disabled = true;
  progWrap.style.display = "block";
  stage.textContent = "Step 1/3: Hashing raw bytes, inspecting file signature & running forensic indicators...";

  const form = new FormData();
  form.append("file", state.selectedFile);

  const params = new URLSearchParams();
  const dv = $("detectorVersionInput").value.trim();
  const cfg = $("configIdInput").value.trim();
  if (dv) params.set("detector_version", dv);
  if (cfg) params.set("config_id", cfg);
  const endpoint = state.mediaKind === "image" ? "/verify" : "/verify-video";
  const url = params.toString() ? `${endpoint}?${params.toString()}` : endpoint;

  try {
    stage.textContent = "Step 2/3: Recording canonical ledger entry & submitting EVM smart contract transaction...";
    const cert = await fetchJson(url, { method: "POST", body: form });
    state.lastResult = cert;
    $("verificationResultArea").innerHTML = renderResultHtml(cert);
    bindResultActions(cert);
    showToast("Verification completed.");
    refreshTopStatus();
  } catch (err) {
    showToast(`Verification failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    progWrap.style.display = "none";
  }
}

function explainVerdictPlain(verdict, cleanPct) {
  const v = String(verdict || "Inconclusive");
  const pctNote = cleanPct !== null ? ` (${cleanPct}% clean signal)` : "";
  if (v === "Low Evidence" || v === "Authentic") {
    return `Looks Authentic${pctNote} — No strong signs of tampering or AI generation were detected.`;
  }
  if (v === "Review Required" || v === "Potentially Manipulated") {
    return `Needs Review${pctNote} — Some unusual visual patterns were found; please inspect carefully.`;
  }
  if (v === "Elevated Evidence of Manipulation" || v === "Manipulated" || v === "AI-Generated") {
    return `High Manipulation Risk${pctNote} — Strong signs of editing or AI generation were detected.`;
  }
  return `${v} — Not enough clear visual data to score reliably.`;
}

function renderResultHtml(cert) {
  const bc = cert.blockchain || {};
  const ind = cert.analysis_indicators || {};
  const noise = ind.noise_residual || {};
  const edge = ind.regional_edge_density || {};
  const prov = ind.provenance || {};
  const sim = cert.similarity_matches || [];
  const timings = cert.timings_ms || {};
  const rawScore = typeof cert.heuristic_score === "number" ? cert.heuristic_score : null;
  const riskPct = rawScore !== null ? Math.round(rawScore * 100) : null;
  const cleanPct = riskPct !== null ? Math.max(0, 100 - riskPct) : null;
  const vStr = String(cert.verdict || "Inconclusive");
  const barCls =
    vStr === "Low Evidence" || vStr === "Authentic"
      ? "good"
      : vStr === "Review Required" || vStr === "Potentially Manipulated"
        ? "warn"
        : "risk";
  const friendlyTitle =
    vStr === "Low Evidence" || vStr === "Authentic"
      ? "Looks Authentic"
      : vStr === "Review Required" || vStr === "Potentially Manipulated"
        ? "Needs Review"
        : vStr === "Elevated Evidence of Manipulation" || vStr === "Manipulated" || vStr === "AI-Generated"
          ? "High Risk"
          : vStr;
  const historyFriendly = String(cert.hash_status || "").includes("exact match")
    ? "Already Verified Before"
    : String(cert.hash_status || "").includes("re-analysis")
      ? "Re-checked File"
      : "First Time Checked";
  const bcAnchored = bc.anchoring_status === "anchored" || bc.anchoring_status === "already_anchored";
  const bcSummary = bcAnchored
    ? `Saved locally and anchored on blockchain (Block #${bc.block_number ?? "confirmed"}).`
    : "Saved in the local tamper-evident ledger (blockchain node offline).";

  return `
    <section class="panel" aria-live="polite">
      <div class="panel-head">
        <div>
          <div class="kicker">Verification Result</div>
          <h2>${escapeHtml(cert.verification_id)} — ${verdictPill(cert.verdict)}</h2>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn primary" href="/certificate/${encodeURIComponent(cert.verification_id)}" target="_blank" rel="noopener">Download PDF Certificate</a>
          <button type="button" class="btn" id="verifyCertEndpointBtn">Verify Certificate</button>
          ${bc.tx_hash ? `<button type="button" class="btn" id="inspectResultTxBtn">Inspect Blockchain Tx</button>` : ""}
        </div>
      </div>

      <div class="plain-summary">
        <div><strong>1. Manipulation Check:</strong> ${escapeHtml(explainVerdictPlain(cert.verdict, cleanPct))}</div>
        <div><strong>2. File History Check:</strong> ${escapeHtml(historyFriendly)}${sim.length ? ` (${sim.length} visually similar file(s) found)` : ""}</div>
        <div><strong>3. Tamper-Proof Record:</strong> ${escapeHtml(bcSummary)}</div>
      </div>

      <div class="grid-3">
        <div class="kpi-card">
          <div class="kpi-label">1. Manipulation Check</div>
          <div class="kpi-value" style="font-size:22px">${cleanPct !== null ? `${cleanPct}% Clean` : escapeHtml(friendlyTitle)}</div>
          <div class="score-bar-track"><div class="score-bar-fill ${barCls}" style="width:${cleanPct ?? 50}%"></div></div>
          <div class="kpi-foot">${escapeHtml(friendlyTitle)}${riskPct !== null ? ` &bull; ${riskPct}% anomaly signal` : ""}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">2. File Identity Check</div>
          <div style="margin-top:8px"><span class="pill info">${escapeHtml(historyFriendly)}</span></div>
          <div class="kpi-foot" style="margin-top:10px">${sim.length ? `${sim.length} visually similar file(s) found` : "No visually similar duplicates found"}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">3. Tamper-Proof Record</div>
          <div style="margin-top:8px">${bcAnchored ? '<span class="pill good">Saved on Blockchain</span>' : '<span class="pill warn">Saved Locally</span>'}</div>
          <div class="kpi-foot mono" style="margin-top:10px">${bcAnchored ? `Block #${bc.block_number ?? "confirmed"} &bull; ` : ""}Record #${cert.ledger_index}</div>
        </div>
      </div>

      <details class="advanced-box">
        <summary>Technical Details (Hashes, Smart Contract Proof &amp; Forensic Metrics)</summary>
        <div class="grid-2" style="margin-top:14px">
          <div class="panel" style="margin-bottom:0">
            <h3>Cryptographic &amp; Blockchain Proof</h3>
            <div class="kv-list">
              <div class="kv-row"><span class="kv-key">SHA-256 (Raw Bytes)</span><span class="kv-val mono">${escapeHtml(cert.content_hash_sha256)}</span></div>
              <div class="kv-row"><span class="kv-key">Perceptual Fingerprint</span><span class="kv-val mono">${escapeHtml(cert.perceptual_hash)}</span></div>
              <div class="kv-row"><span class="kv-key">Detector / Config</span><span class="kv-val mono">${escapeHtml(cert.analysis_config_id)}</span></div>
              <div class="kv-row"><span class="kv-key">Local Record Hash</span><span class="kv-val mono">${escapeHtml(cert.ledger_record_hash)}</span></div>
              <div class="kv-row"><span class="kv-key">Smart Contract</span><span class="kv-val mono">${escapeHtml(bc.contract_address || "Not anchored")}</span></div>
              <div class="kv-row"><span class="kv-key">Transaction Hash</span><span class="kv-val mono">${escapeHtml(bc.tx_hash || "N/A")}</span></div>
              <div class="kv-row"><span class="kv-key">Block / Chain ID</span><span class="kv-val mono">${bc.block_number ?? "N/A"} / ${bc.chain_id ?? "N/A"} (${escapeHtml(bc.network_name || "N/A")})</span></div>
              <div class="kv-row"><span class="kv-key">Submitter Wallet</span><span class="kv-val mono">${escapeHtml(bc.submitter || "N/A")}</span></div>
              ${bc.error ? `<div class="kv-row"><span class="kv-key">Blockchain Error</span><span class="kv-val" style="color:#fb7185">${escapeHtml(bc.error)}</span></div>` : ""}
            </div>
          </div>

          <div class="panel" style="margin-bottom:0">
            <h3>Forensic Indicators &amp; Timings</h3>
            <div class="kv-list">
              ${noise.residual_std !== undefined ? `<div class="kv-row"><span class="kv-key">Noise Residual (R = I − Gσ(I))</span><span class="kv-val mono">std=${noise.residual_std}, CV=${noise.regional_noise_cv}</span></div>` : ""}
              ${edge.edge_density_std !== undefined ? `<div class="kv-row"><span class="kv-key">Regional Edge Density (4×4)</span><span class="kv-val mono">mean=${edge.edge_density_mean}, std=${edge.edge_density_std}</span></div>` : ""}
              ${prov.metadata_status ? `<div class="kv-row"><span class="kv-key">EXIF / Metadata</span><span class="kv-val">${escapeHtml(prov.metadata_status)} (gen_meta=${prov.generator_metadata_found}, filename_clue=${prov.weak_filename_marker_found})</span></div>` : ""}
              ${cert.frames_analyzed ? `<div class="kv-row"><span class="kv-key">Video Sampling</span><span class="kv-val">${cert.frames_analyzed} frames | ${cert.duration_seconds}s | mean=${cert.mean_frame_score}, max=${cert.max_frame_score}</span></div>` : ""}
              <div class="kv-row"><span class="kv-key">Visual Similarity Matches</span><span class="kv-val">${sim.length ? sim.map((m) => `${shortHex(m.content_sha256, 10)} (d=${m.hamming_distance})`).join(", ") : "None within threshold"}</span></div>
              <div class="kv-row"><span class="kv-key">Processing Timings (ms)</span><span class="kv-val mono">${Object.keys(timings).length ? Object.entries(timings).map(([k, v]) => `${k}=${v}`).join(" | ") : "Cached lookup"}</span></div>
            </div>
          </div>
        </div>

        ${Array.isArray(cert.frame_results) && cert.frame_results.length ? `
          <div style="margin-top:16px">
            <h3>Sampled Video Frame Analysis</h3>
            <div class="table-wrapper" style="max-block-size:260px">
              <table class="data-table">
                <thead><tr><th>Frame #</th><th>Timestamp (s)</th><th>Score</th><th>Verdict</th><th>Frame pHash</th></tr></thead>
                <tbody>
                  ${cert.frame_results.map((fr) => `<tr>
                    <td class="mono">${fr.frame_index}</td>
                    <td class="mono">${fr.timestamp_seconds}</td>
                    <td class="mono">${fr.heuristic_score.toFixed(4)}</td>
                    <td>${verdictPill(fr.verdict)}</td>
                    <td class="mono">${escapeHtml(fr.frame_phash)}</td>
                  </tr>`).join("")}
                </tbody>
              </table>
            </div>
          </div>
        ` : ""}
      </details>
    </section>
  `;
}

function bindResultActions(cert) {
  const verifyBtn = $("verifyCertEndpointBtn");
  if (verifyBtn) {
    verifyBtn.addEventListener("click", async () => {
      try {
        const res = await fetchJson(`/verify-certificate/${encodeURIComponent(cert.verification_id)}?format=json`);
        openDialog(`Certificate Verification: ${cert.verification_id}`, `<pre class="mono" style="white-space:pre-wrap;font-size:12px">${escapeHtml(JSON.stringify(res, null, 2))}</pre>`);
      } catch (err) {
        showToast(err.message);
      }
    });
  }
  const txBtn = $("inspectResultTxBtn");
  if (txBtn && cert.blockchain?.tx_hash) {
    txBtn.addEventListener("click", () => inspectTransactionModal(cert.blockchain.tx_hash));
  }
}

/* ==================== D. BLOCKCHAIN EXPLORER ==================== */
async function renderExplorerView() {
  const root = $("pageContent");
  root.innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Ethereum-Compatible EVM Inspector</div>
        <h1>Blockchain Explorer</h1>
        <p class="view-sub">Inspect deployed smart contract state, mined verification records, transaction receipts, and anchored ledger checkpoints.</p>
      </div>
      <div style="display:flex;gap:10px">
        <button type="button" class="btn primary" id="anchorCheckpointBtn">Anchor Ledger Checkpoint</button>
        <button type="button" class="btn" id="refreshExplorerBtn">Refresh</button>
      </div>
    </div>
    <div id="explorerBody"><div class="panel">Querying EVM node and VerityVerification.sol...</div></div>
  `;
  $("refreshExplorerBtn").addEventListener("click", renderExplorerView);
  $("anchorCheckpointBtn").addEventListener("click", async () => {
    try {
      const res = await fetchJson("/blockchain/checkpoint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "Dashboard-triggered VERITY checkpoint" }),
      });
      showToast(`Checkpoint #${res.checkpoint.checkpoint_id} mined in block #${res.block_number}`);
      renderExplorerView();
      refreshTopStatus();
    } catch (err) {
      showToast(`Checkpoint failed: ${err.message}`);
    }
  });

  try {
    const [status, contractInfo, onChainRecords, checkpoints] = await Promise.all([
      fetchJson("/blockchain/status"),
      fetchJson("/blockchain/contract-info").catch((e) => ({ error: e.message })),
      fetchJson("/blockchain/records?limit=100").catch(() => ({ records: [] })),
      fetchJson("/blockchain/checkpoints").catch(() => ({ checkpoints: [] })),
    ]);

    const records = onChainRecords.records || [];
    const cps = checkpoints.checkpoints || [];

    $("explorerBody").innerHTML = `
      <div class="grid-2">
        <section class="panel">
          <h2>Smart Contract Metadata</h2>
          ${contractInfo.error ? `<div class="notice-box risk" style="margin-top:12px">${escapeHtml(contractInfo.error)}</div>` : `
            <div class="kv-list" style="margin-top:10px">
              <div class="kv-row"><span class="kv-key">Contract Name</span><span class="kv-val">${escapeHtml(contractInfo.contract_name)} (${escapeHtml(contractInfo.solc_version)})</span></div>
              <div class="kv-row"><span class="kv-key">Contract Address</span><span class="kv-val mono">${escapeHtml(contractInfo.contract_address)}</span></div>
              <div class="kv-row"><span class="kv-key">Network / Chain ID</span><span class="kv-val">${escapeHtml(contractInfo.network_name)} (Chain ${contractInfo.chain_id})</span></div>
              <div class="kv-row"><span class="kv-key">Owner Wallet</span><span class="kv-val mono">${escapeHtml(contractInfo.owner)}</span></div>
              <div class="kv-row"><span class="kv-key">Deployment Tx</span><span class="kv-val mono">${escapeHtml(shortHex(contractInfo.deployment_tx_hash, 22))} (Block #${contractInfo.deployment_block_number ?? "N/A"})</span></div>
              <div class="kv-row"><span class="kv-key">On-Chain Records / Checkpoints</span><span class="kv-val">${contractInfo.total_records} / ${contractInfo.total_checkpoints}</span></div>
            </div>
          `}
        </section>

        <section class="panel">
          <h2>On-Chain Transaction &amp; Content Lookup</h2>
          <div class="field" style="margin-top:12px">
            <label for="txLookupInput">Transaction Hash (0x...)</label>
            <div style="display:flex;gap:8px">
              <input class="input mono" id="txLookupInput" placeholder="0x...">
              <button type="button" class="btn" id="txLookupBtn">Lookup Tx</button>
            </div>
          </div>
          <div class="field" style="margin-top:12px">
            <label for="hashLookupInput">Content SHA-256 Digest (64 hex chars)</label>
            <div style="display:flex;gap:8px">
              <input class="input mono" id="hashLookupInput" placeholder="64-character SHA-256 hex">
              <button type="button" class="btn" id="hashLookupBtn">Lookup Hash</button>
            </div>
          </div>
          <div style="margin-top:12px;font-size:12px;color:var(--text-dim)">
            RPC: <code>${escapeHtml(status.rpc_url)}</code> | Latest Block: <code>#${status.latest_block ?? "N/A"}</code>
          </div>
        </section>
      </div>

      <section class="panel">
        <div class="panel-head"><h2>On-Chain Verification Records (${records.length})</h2></div>
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>Verification ID</th><th>Content SHA-256</th><th>Media</th><th>Result Category</th><th>Config ID</th><th>Submitter</th><th>On-Chain Time</th></tr></thead>
            <tbody>
              ${records.length ? records.slice().reverse().map((rec) => `<tr>
                <td class="mono">${escapeHtml(rec.verification_id)}</td>
                <td class="mono">${escapeHtml(shortHex(rec.content_sha256, 16))}</td>
                <td>${escapeHtml(rec.media_type)}</td>
                <td>${verdictPill(rec.result_category)}</td>
                <td class="mono">${escapeHtml(rec.analysis_config_id)}</td>
                <td class="mono">${escapeHtml(shortHex(rec.submitter, 12))}</td>
                <td class="mono">${escapeHtml(rec.on_chain_timestamp)}</td>
              </tr>`).join("") : '<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">No on-chain records registered yet.</td></tr>'}
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Anchored Ledger Checkpoints (${cps.length})</h2></div>
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>Checkpoint #</th><th>Record Count</th><th>Last Seq</th><th>Anchored Ledger Root Hash</th><th>Submitter</th><th>Timestamp</th></tr></thead>
            <tbody>
              ${cps.length ? cps.slice().reverse().map((cp) => `<tr>
                <td class="mono">#${cp.checkpoint_id}</td>
                <td>${cp.record_count}</td>
                <td class="mono">${cp.last_sequence_number}</td>
                <td class="mono">${escapeHtml(cp.ledger_root_hash)}</td>
                <td class="mono">${escapeHtml(shortHex(cp.submitter, 12))}</td>
                <td class="mono">${escapeHtml(cp.timestamp)}</td>
              </tr>`).join("") : '<tr><td colspan="6" style="text-align:center;color:var(--text-dim)">No checkpoints anchored yet.</td></tr>'}
            </tbody>
          </table>
        </div>
      </section>
    `;

    $("txLookupBtn").addEventListener("click", () => {
      const tx = $("txLookupInput").value.trim();
      if (tx) inspectTransactionModal(tx);
    });
    $("hashLookupBtn").addEventListener("click", async () => {
      const h = $("hashLookupInput").value.trim();
      if (!h) return;
      try {
        const res = await fetchJson(`/blockchain/record/${encodeURIComponent(h)}`);
        openDialog(`On-Chain Events for ${shortHex(h, 14)}`, `<pre class="mono" style="white-space:pre-wrap;font-size:12px">${escapeHtml(JSON.stringify(res, null, 2))}</pre>`);
      } catch (err) {
        showToast(err.message);
      }
    });
  } catch (err) {
    $("explorerBody").innerHTML = `<div class="notice-box risk">${escapeHtml(err.message)}</div>`;
  }
}

async function inspectTransactionModal(txHash) {
  try {
    const data = await fetchJson(`/blockchain/transaction/${encodeURIComponent(txHash)}`);
    openDialog(`Transaction ${shortHex(txHash, 14)}`, `<pre class="mono" style="white-space:pre-wrap;font-size:12px">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`);
  } catch (err) {
    showToast(`Tx lookup failed: ${err.message}`);
  }
}

/* ==================== E. VERIFICATION HISTORY ==================== */
async function renderHistoryView() {
  const root = $("pageContent");
  root.innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Searchable Audit Log</div>
        <h1>Verification History</h1>
        <p class="view-sub">Filter by SHA-256, verification ID, media type, verdict, or date, and inspect re-analysis trails or EVM receipts.</p>
      </div>
    </div>

    <section class="panel">
      <search>
        <form id="historyFilterForm" class="form-grid" style="margin-top:0">
          <div class="field"><label for="histSearch">SHA-256 or Verification ID</label><input class="input mono" id="histSearch" type="search" placeholder="Search hash or VRT-..."></div>
          <div class="field"><label for="histMedia">Media Type</label><select class="select" id="histMedia"><option value="">All</option><option value="image">Image</option><option value="video">Video</option></select></div>
          <div class="field"><label for="histVerdict">Verdict</label><select class="select" id="histVerdict"><option value="">All</option><option value="Low Evidence">Low Evidence</option><option value="Review Required">Review Required</option><option value="Elevated Evidence of Manipulation">Elevated Evidence</option><option value="Authentic">Authentic (v1)</option><option value="Potentially Manipulated">Potentially Manipulated (v1)</option><option value="AI-Generated">AI-Generated (v1)</option></select></div>
          <div class="field"><label for="histFrom">From Date</label><input class="input" id="histFrom" type="date"></div>
          <div class="field"><label for="histTo">To Date</label><input class="input" id="histTo" type="date"></div>
        </form>
        <div style="margin-top:12px;display:flex;gap:10px">
          <button type="button" class="btn primary" id="applyHistFilterBtn">Apply Filters</button>
          <button type="button" class="btn" id="resetHistFilterBtn">Reset</button>
        </div>
      </search>
    </section>

    <section class="panel">
      <div id="historyTableContainer">Loading history...</div>
    </section>
  `;

  async function loadFiltered() {
    const params = new URLSearchParams();
    const q = $("histSearch").value.trim();
    const m = $("histMedia").value;
    const v = $("histVerdict").value;
    const df = $("histFrom").value;
    const dt = $("histTo").value;
    if (q) params.set("q", q);
    if (m) params.set("media_type", m);
    if (v) params.set("verdict", v);
    if (df) params.set("date_from", df);
    if (dt) params.set("date_to", dt);
    const qs = params.toString() ? `?${params.toString()}` : "";

    try {
      const items = await fetchJson(`/history${qs}`);
      if (!items.length) {
        $("historyTableContainer").innerHTML = `<div style="color:var(--text-dim);padding:20px;text-align:center">No matching verification records found.</div>`;
        return;
      }
      $("historyTableContainer").innerHTML = `
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>Seq</th><th>Verification ID</th><th>Media</th><th>Verdict &amp; Score</th><th>Content SHA-256</th><th>Blockchain</th><th>Actions</th></tr></thead>
            <tbody>
              ${items.slice().reverse().map((r) => {
                const seq = r.sequence_number ?? r.index;
                const verId = r.verification_id || (r.block_hash || "").slice(0, 16);
                const sha = r.content_sha256 || r.content_hash;
                const media = r.media_type || r.ai_result?.media_type || "image";
                const verdict = r.verdict || r.ai_result?.authenticity_flag;
                const score = r.heuristic_score ?? r.ai_result?.deepfake_probability;
                const txHash = r.blockchain?.tx_hash;
                return `<tr>
                  <td class="mono">#${seq}</td>
                  <td><div class="mono">${escapeHtml(verId)}</div><small style="color:var(--text-dim)">${escapeHtml(r.recorded_at || r.server_timestamp)}</small></td>
                  <td>${escapeHtml(media)}</td>
                  <td>${verdictPill(verdict)} <span class="mono" style="margin-left:6px">${typeof score === "number" ? score.toFixed(4) : "N/A"}</span></td>
                  <td class="mono">${escapeHtml(shortHex(sha, 14))}</td>
                  <td>${anchorPill(r.blockchain?.anchoring_status || "legacy_local")}${r.blockchain?.block_number != null ? `<div class="mono" style="font-size:11px;color:var(--text-dim)">Block #${r.blockchain.block_number}</div>` : ""}</td>
                  <td style="display:flex;gap:6px;flex-wrap:wrap">
                    <button type="button" class="btn" data-reanalysis="${escapeHtml(sha)}">Re-analyses</button>
                    <a class="btn" href="/certificate/${encodeURIComponent(verId)}" target="_blank" rel="noopener">PDF</a>
                    ${txHash ? `<button type="button" class="btn" data-tx="${escapeHtml(txHash)}">Tx</button>` : ""}
                  </td>
                </tr>`;
              }).join("")}
            </tbody>
          </table>
        </div>
      `;

      document.querySelectorAll("[data-reanalysis]").forEach((b) => {
        b.addEventListener("click", async () => {
          try {
            const res = await fetchJson(`/check/${encodeURIComponent(b.dataset.reanalysis)}`);
            openDialog(`Content History: ${shortHex(b.dataset.reanalysis, 14)}`, `<pre class="mono" style="white-space:pre-wrap;font-size:12px">${escapeHtml(JSON.stringify(res, null, 2))}</pre>`);
          } catch (err) {
            showToast(err.message);
          }
        });
      });
      document.querySelectorAll("[data-tx]").forEach((b) => {
        b.addEventListener("click", () => inspectTransactionModal(b.dataset.tx));
      });
    } catch (err) {
      $("historyTableContainer").innerHTML = `<div class="notice-box risk">${escapeHtml(err.message)}</div>`;
    }
  }

  $("applyHistFilterBtn").addEventListener("click", loadFiltered);
  $("resetHistFilterBtn").addEventListener("click", () => {
    $("historyFilterForm").reset();
    loadFiltered();
  });
  loadFiltered();
}

/* ==================== F. INTEGRITY VERIFICATION ==================== */
async function renderIntegrityView() {
  const root = $("pageContent");
  root.innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Three-Layer Audit Verification</div>
        <h1>Ledger &amp; Blockchain Integrity</h1>
        <p class="view-sub">Independently validates local hash-chain consistency, individual on-chain record hashes, and external smart-contract checkpoints.</p>
      </div>
      <button type="button" class="btn primary" id="recheckIntegrityBtn">Run Full Integrity Audit</button>
    </div>
    <div id="integrityBody"><div class="panel">Running local and on-chain integrity checks...</div></div>
  `;
  $("recheckIntegrityBtn").addEventListener("click", renderIntegrityView);

  try {
    const res = await fetchJson("/ledger/integrity");
    const local = res.local_ledger || {};
    const bcRec = res.blockchain_records || {};
    const cp = res.checkpoint || {};

    $("integrityBody").innerHTML = `
      <div class="grid-3">
        <section class="panel">
          <div class="panel-head"><h3>1. Local Hash-Chain</h3>${local.valid ? '<span class="pill good">VALID</span>' : '<span class="pill risk">INVALID</span>'}</div>
          <div class="kv-list">
            <div class="kv-row"><span class="kv-key">Record Count</span><span class="kv-val">${local.length ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Broken At Index</span><span class="kv-val mono">${local.broken_at_index ?? "None"}</span></div>
            <div class="kv-row"><span class="kv-key">Error Type</span><span class="kv-val mono">${escapeHtml(local.error_type || "None")}</span></div>
            <div class="kv-row"><span class="kv-key">Root Hash</span><span class="kv-val mono">${escapeHtml(shortHex(local.ledger_root_hash, 18))}</span></div>
          </div>
          ${local.reason ? `<div class="notice-box risk" style="margin-top:10px">${escapeHtml(local.reason)}</div>` : ""}
        </section>

        <section class="panel">
          <div class="panel-head"><h3>2. On-Chain Record Hashes</h3>${anchorPill(bcRec.status)}</div>
          <div class="kv-list">
            <div class="kv-row"><span class="kv-key">Checked Records</span><span class="kv-val">${bcRec.checked_records ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Verified On-Chain</span><span class="kv-val" style="color:#4ade80">${bcRec.verified_on_chain ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Mismatched On-Chain</span><span class="kv-val" style="color:#fb7185">${bcRec.mismatched_on_chain ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Local-Only / Legacy</span><span class="kv-val">${bcRec.unanchored_local_only ?? 0}</span></div>
          </div>
          ${bcRec.error ? `<div class="notice-box warn" style="margin-top:10px">${escapeHtml(bcRec.error)}</div>` : ""}
        </section>

        <section class="panel">
          <div class="panel-head"><h3>3. External Checkpoint</h3>${anchorPill(cp.status)}</div>
          <div class="kv-list">
            <div class="kv-row"><span class="kv-key">Checkpoint ID</span><span class="kv-val mono">${cp.checkpoint_id ?? "None"}</span></div>
            <div class="kv-row"><span class="kv-key">Anchored Count</span><span class="kv-val">${cp.anchored_record_count ?? "N/A"}</span></div>
            <div class="kv-row"><span class="kv-key">Local Count</span><span class="kv-val">${cp.local_record_count ?? local.length ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Unanchored Suffix</span><span class="kv-val">${cp.unanchored_suffix_count ?? "N/A"}</span></div>
          </div>
          <div class="notice-box ${cp.valid === false ? "risk" : (cp.valid ? "" : "warn")}" style="margin-top:10px">${escapeHtml(cp.reason || "No checkpoint status")}</div>
        </section>
      </div>

      <section class="panel">
        <h3>Threat Model Note: Local Hash Chain vs. Blockchain Checkpointing</h3>
        <p style="color:var(--text-muted);margin:0">
          Local hash-chaining detects single-field edits, broken <code>previous_hash</code> links, and record reordering when stored hashes are preserved. However, an adversary with write access to the local JSON file can truncate the suffix or rewrite the entire chain and recompute every local hash. External checkpoints anchored on <code>VerityVerification.sol</code> detect both truncation and full-chain rewrites across the anchored prefix.
        </p>
      </section>
    `;
  } catch (err) {
    $("integrityBody").innerHTML = `<div class="notice-box risk">${escapeHtml(err.message)}</div>`;
  }
}

/* ==================== G. RESEARCH & METHODOLOGY ==================== */
function renderResearchView() {
  $("pageContent").innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Methodology &amp; Experimental Protocol</div>
        <h1>Research Design &amp; Technical Architecture</h1>
        <p class="view-sub">Aligned with the submitted synopsis and first research draft for VERIFY.</p>
      </div>
    </div>

    <div class="grid-3">
      <section class="panel"><h3>RQ1: Heuristic Detection</h3><p style="color:var(--text-muted)">How reliably can the heuristic baseline (noise residual R = I − Gσ(I), 4×4 regional edge density, consistency metrics, metadata inspection) distinguish manipulated samples from genuine media and benign transformations?</p></section>
      <section class="panel"><h3>RQ2: Fingerprint Behavior</h3><p style="color:var(--text-muted)">How do exact SHA-256 digests and 64-bit DCT perceptual hashes behave under resizing, JPEG recompression, metadata edits, and one-byte modifications?</p></section>
      <section class="panel"><h3>RQ3: Ledger Tamper Evidence</h3><p style="color:var(--text-muted)">Which verification history modifications are detected by local canonical hash-chaining alone, and which (truncation, full rewrite) require an external Ethereum smart-contract checkpoint?</p></section>
    </div>

    <section class="panel">
      <h2>System Pipeline &amp; Explicit Limitations</h2>
      <div class="kv-list" style="margin-top:10px">
        <div class="kv-row"><span class="kv-key">Processing Pipeline</span><span class="kv-val">Upload &rarr; Signature/Size Validation &rarr; Raw-Byte SHA-256 &rarr; Forensic Analysis &amp; DCT-64 pHash &rarr; Canonical Local Ledger &rarr; Solidity Smart Contract Tx &rarr; PDF/QR Certificate</span></div>
        <div class="kv-row"><span class="kv-key">Baseline Limitation</span><span class="kv-val">Heuristic indicators are uncalibrated suspicion scores, not trained CNN probabilities; modular interface supports future XceptionNet / EfficientNet integration.</span></div>
        <div class="kv-row"><span class="kv-key">Video Sampling Limitation</span><span class="kv-val">Samples up to 12 equidistant frames via OpenCV; short manipulated segments and audio tracks are not inspected.</span></div>
        <div class="kv-row"><span class="kv-key">Blockchain Boundary</span><span class="kv-val">Anchoring verifies record existence and detects history rewrites; it does not certify that the original captured scene was authentic.</span></div>
      </div>
    </section>
  `;
}

/* ==================== H. ABOUT VERIFY ==================== */
function renderAboutView() {
  $("pageContent").innerHTML = `
    <div class="view-header">
      <div>
        <div class="kicker">Academic Identity — Group No. 27</div>
        <h1>About VERIFY</h1>
        <p class="view-sub">Blockchain-Based Verification System for Digital Content Authenticity and Deepfake Detection</p>
      </div>
    </div>

    <div class="grid-2">
      <section class="panel">
        <h2>Institution &amp; Project Details</h2>
        <div class="kv-list" style="margin-top:12px">
          <div class="kv-row"><span class="kv-key">Institution</span><span class="kv-val">Dr. Vishwanath Karad MIT World Peace University, Pune</span></div>
          <div class="kv-row"><span class="kv-key">Department</span><span class="kv-val">School of Computer Science &amp; Engineering — Dept. of Computer Science and Applications</span></div>
          <div class="kv-row"><span class="kv-key">Program &amp; Year</span><span class="kv-val">M.Sc. Computer Science | Academic Year 2026–27</span></div>
          <div class="kv-row"><span class="kv-key">Faculty Guide</span><span class="kv-val">Dr. Anuradha Kanade</span></div>
        </div>
      </section>

      <section class="panel">
        <h2>Research Objectives</h2>
        <ul style="margin:10px 0 0;padding-left:18px;color:var(--text-muted);display:grid;gap:6px">
          <li>Analyze images and sampled video frames using transparent forensic indicators.</li>
          <li>Provide dual-layer SHA-256 exact identity and DCT perceptual similarity checks.</li>
          <li>Maintain canonical local hash-chained records anchored on an EVM smart contract.</li>
          <li>Generate verifiable PDF/QR certificates and evaluate RQ1, RQ2, and RQ3 empirically.</li>
        </ul>
      </section>
    </div>

    <section class="panel">
      <h2>Group Members &amp; Contributions (Group 27)</h2>
      <div class="grid-3" style="margin-top:14px;margin-bottom:0">
        <div class="kpi-card">
          <h3>Ansari Faizalam Mahmoodbhai</h3>
          <div class="mono" style="font-size:12px;color:var(--blue-bright)">PRN 1272260053</div>
          <p style="font-size:12.5px;color:var(--text-muted)">Led research formulation, literature review, methodology, dual-layer hashing and ledger architecture, evaluation design, and paper authoring.</p>
        </div>
        <div class="kpi-card">
          <h3>Gandhi Viraj Prashant Kumar</h3>
          <div class="mono" style="font-size:12px;color:var(--blue-bright)">PRN 1272260230</div>
          <p style="font-size:12.5px;color:var(--text-muted)">Built the complete working project demo, including dual-layer hashing integration, dashboard and user interaction design, upload-to-result workflow, frontend-API integration, and certificate presentation.</p>
        </div>
        <div class="kpi-card">
          <h3>Vraj Modi Jitendra Kumar</h3>
          <div class="mono" style="font-size:12px;color:var(--blue-bright)">PRN 1272260212</div>
          <p style="font-size:12.5px;color:var(--text-muted)">Supported test-case preparation, sample-media organization, lookup and integrity evaluation checklists, and submission consistency review.</p>
        </div>
      </div>
    </section>
  `;
}

function openDialog(title, bodyHtml) {
  $("dialogTitle").textContent = title;
  $("dialogBody").innerHTML = bodyHtml;
  $("detailDialog").showModal();
}

document.querySelectorAll(".nav-btn[data-page]").forEach((btn) => {
  btn.addEventListener("click", () => navigate(btn.dataset.page));
});

window.addEventListener("hashchange", () => {
  state.page = location.hash.slice(1) || "verify";
  renderCurrentPage();
});

refreshTopStatus();
setInterval(refreshTopStatus, 15000);
renderCurrentPage();
