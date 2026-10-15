const API = "/api/visual-render";
let currentJobId = null;
let pollTimer = null;

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { const j = await r.json(); detail = j.detail || JSON.stringify(j); } catch {}
    throw new Error(`${r.status} ${detail}`);
  }
  if (r.status === 204) return null;
  return r.json();
}

function setStatus(elId, msg, kind = "") {
  const el = document.getElementById(elId);
  el.textContent = msg;
  el.className = "status " + kind;
}

function badge(status) {
  return `<span class="badge ${status}">${status}</span>`;
}

// ① 加载模板
async function loadTemplates() {
  try {
    setStatus("status-templates", "加载模板中...");
    const list = await api("/templates");
    const sel = document.getElementById("template-select");
    sel.innerHTML = "";
    list.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.template_id;
      opt.textContent = `${t.template_id} v${t.version} (${t.composition_id}, ${t.duration_sec_range[0]}-${t.duration_sec_range[1]}s)`;
      sel.appendChild(opt);
    });
    sel.onchange = () => showTemplateSchema(sel.value);
    if (list.length) showTemplateSchema(list[0].template_id);
    setStatus("status-templates", `共 ${list.length} 个模板`, "ok");
    document.getElementById("step-2").classList.remove("disabled");
    addMetric();
  } catch (e) {
    setStatus("status-templates", "✗ " + e.message, "err");
  }
}

async function showTemplateSchema(tid) {
  const list = await api("/templates");
  const t = list.find(x => x.template_id === tid);
  const pre = document.getElementById("template-schema");
  if (t && t.json_schema) {
    pre.textContent = JSON.stringify(t.json_schema, null, 2);
    pre.classList.remove("hidden");
  } else {
    pre.classList.add("hidden");
  }
}

// ② 指标列表
function addMetric() {
  const list = document.getElementById("metrics-list");
  const idx = list.children.length + 1;
  if (idx > 4) return;
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <input type="text" placeholder="指标 ${idx} 标签 (如 上半年收入)" data-metric-label>
    <input type="text" placeholder="指标 ${idx} 值 (如 +2.32%)" data-metric-value>
    <button class="secondary" onclick="this.parentNode.remove()" style="font-size:0.8rem;flex:0 0 auto">删除</button>
  `;
  list.appendChild(row);
}

function loadExample() {
  document.getElementById("in-title").value = "经济新数据";
  document.getElementById("in-subtitle").value = "国家统计局公布最新统计";
  const list = document.getElementById("metrics-list");
  list.innerHTML = "";
  addMetric();
  list.children[0].querySelector("[data-metric-label]").value = "上半年\n全民收入";
  list.children[0].querySelector("[data-metric-value]").value = "+2.32%";
  addMetric();
  list.children[1].querySelector("[data-metric-label]").value = "目标";
  list.children[1].querySelector("[data-metric-value]").value = "全民增收";
  document.getElementById("in-chart-label-1").value = "2024H1";
  document.getElementById("in-chart-value-1").value = "200";
  document.getElementById("in-chart-label-2").value = "2025H1";
  document.getElementById("in-chart-value-2").value = "280";
  document.getElementById("in-caption").value = "国家统计局公布，今年上半年，全民收入提升 2.32 个百分点，实现全民增收。";
  document.getElementById("in-source").value = "数据来源：国家统计局 · 2026-07-25";
  document.getElementById("in-duration").value = "12";
}

function buildPayload() {
  const metrics = [];
  document.querySelectorAll("#metrics-list .row").forEach(row => {
    const label = row.querySelector("[data-metric-label]").value.trim();
    const value = row.querySelector("[data-metric-value]").value.trim();
    if (label && value) metrics.push({ label, value });
  });
  const chartItems = [];
  for (let i = 1; i <= 2; i++) {
    const lbl = document.getElementById(`in-chart-label-${i}`).value.trim();
    const val = document.getElementById(`in-chart-value-${i}`).value;
    if (lbl && val !== "") chartItems.push({ label: lbl, value: parseFloat(val) });
  }
  return {
    template_id: document.getElementById("template-select").value,
    input_data: {
      title: document.getElementById("in-title").value.trim(),
      subtitle: document.getElementById("in-subtitle").value.trim(),
      metrics,
      chart: chartItems.length ? { type: "bar", unit: "", items: chartItems } : undefined,
      caption: document.getElementById("in-caption").value.trim(),
      source: document.getElementById("in-source").value.trim(),
      duration_sec: parseInt(document.getElementById("in-duration").value || "12", 10),
    },
  };
}

// ③ 提交 + 轮询
async function submitJob() {
  setStatus("status-submit", "校验入参...");
  const payload = buildPayload();
  if (!payload.input_data.title) {
    setStatus("status-submit", "✗ 标题必填", "err"); return;
  }
  if (payload.input_data.metrics.length === 0) {
    setStatus("status-submit", "✗ 至少 1 条指标", "err"); return;
  }
  try {
    setStatus("status-submit", "提交任务...");
    const job = await api("/jobs", { method: "POST", body: JSON.stringify(payload) });
    currentJobId = job.id;
    setStatus("status-submit", `✓ 任务创建: ${job.id}（${job.template_id}）`, "ok");
    document.getElementById("step-3").classList.remove("disabled");
    await runGenerate();
    startPolling();
  } catch (e) {
    setStatus("status-submit", "✗ " + e.message, "err");
  }
}

async function runGenerate() {
  if (!currentJobId) return;
  try {
    setStatus("status-submit", "同步渲染中（最多 60s）...", "warn");
    const t0 = performance.now();
    const result = await api(`/jobs/${currentJobId}/generate`, { method: "POST" });
    const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
    setStatus("status-submit", `✓ 渲染返回 status=${result.status}, 端到端 ${elapsed}s`, result.status === "completed" ? "ok" : "err");
  } catch (e) {
    setStatus("status-submit", "✗ " + e.message, "err");
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (!currentJobId) return;
    try {
      const job = await api(`/jobs/${currentJobId}`);
      renderCurrentJob(job);
      if (job.is_terminal) { stopPolling(); refreshList(); }
    } catch (e) { /* keep polling */ }
  }, 2000);
}

function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

function renderCurrentJob(job) {
  const el = document.getElementById("current-job");
  const warningsHtml = (job.warnings || []).length
    ? `<div class="warn-list">⚠ ${job.warnings.join(" | ")}</div>` : "";
  const media = job.media || {};
  let framesHtml = "";
  if (job.status === "completed") {
    const pf = job.preview_frames || {};
    framesHtml = `
      <div class="grid" style="margin-top:0.75rem">
        ${["first","middle","final"].map(k => pf[k]
          ? `<figure><img src="/api/visual-render/jobs/${job.id}/frames/${k}" loading="lazy"><figcaption>${k}</figcaption></figure>`
          : `<figure><figcaption style="color:#64748b">${k} (missing)</figcaption></figure>`).join("")}
      </div>
      <video controls style="width:100%;margin-top:0.75rem;background:#000" src="/api/visual-render/jobs/${job.id}/download"></video>
      <div class="actions" style="margin-top:0.5rem">
        <a href="/api/visual-render/jobs/${job.id}/download" download><button>下载 MP4</button></a>
        <a href="${job.manifest_path || '#'}" target="_blank" class="secondary" style="text-decoration:none"><button class="secondary">查看 manifest</button></a>
      </div>
    `;
  }
  el.innerHTML = `
    <div class="head">
      ${badge(job.status)}
      <span class="id">${job.id}</span>
    </div>
    <div class="meta">
      模板: <b>${job.template_id}</b> v${job.template_version} · composition: ${job.composition_id}
      ${media.codec ? ` · ${media.codec} ${media.width}×${media.height} @ ${media.fps}fps, ${media.duration_sec}s` : ""}
      ${job.render_seconds ? ` · 渲染 ${job.render_seconds}s` : ""}
    </div>
    ${job.error_message ? `<div class="status err" style="margin-top:0.5rem">${job.error_message}</div>` : ""}
    ${warningsHtml}
    ${framesHtml}
  `;
}

// ④ 历史列表
async function refreshList() {
  const status = document.getElementById("filter-status").value;
  const qs = status ? `?status=${encodeURIComponent(status)}&limit=20` : "?limit=20";
  try {
    const jobs = await api("/jobs" + qs);
    const el = document.getElementById("jobs-list");
    if (!jobs.length) { el.innerHTML = '<div class="status">暂无任务。</div>'; return; }
    el.innerHTML = jobs.map(j => {
      const m = j.media || {};
      const media = m.codec ? `${m.codec} ${m.width}×${m.height} @ ${m.fps}fps` : "—";
      return `
        <div class="job-card">
          <div class="head">${badge(j.status)}<span class="id">${j.id}</span></div>
          <div class="meta">
            ${j.template_id} v${j.template_version} · ${j.composition_id} · ${media} · ${j.render_seconds || "?"}s · ${j.created_at}
          </div>
          ${j.error_message ? `<div class="status err">${j.error_message}</div>` : ""}
          <div class="actions">
            <button class="secondary" onclick='viewJob("${j.id}")'>查看</button>
            ${j.status === "completed" ? `<a href="/api/visual-render/jobs/${j.id}/download" download><button>下载 MP4</button></a>` : ""}
            <button class="danger" onclick='delJob("${j.id}")'>删除</button>
          </div>
        </div>
      `;
    }).join("");
  } catch (e) {
    document.getElementById("jobs-list").innerHTML = `<div class="status err">${e.message}</div>`;
  }
}

async function viewJob(id) {
  currentJobId = id;
  const job = await api(`/jobs/${id}`);
  document.getElementById("step-3").classList.remove("disabled");
  renderCurrentJob(job);
  if (!job.is_terminal) startPolling();
}

async function delJob(id) {
  if (!confirm(`确认删除任务 ${id} 及其产物？`)) return;
  try {
    await api(`/jobs/${id}`, { method: "DELETE" });
    if (currentJobId === id) {
      currentJobId = null;
      document.getElementById("current-job").innerHTML = "任务已删除。";
      stopPolling();
    }
    refreshList();
  } catch (e) { alert("删除失败: " + e.message); }
}

// init
loadTemplates();
refreshList();
