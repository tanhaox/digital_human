/* director.js — extracted from director.html */
const API = '/api/director';
let currentJobId = null;
let pollTimer = null;
let allSlots = [];
let _selectedSlotIdx = null;  // 当前展开的 slot 索引，轮询时保持展开
let _jobExecuting = false;    // 当前任务是否在执行中
let _composingJobIds = new Set();  // 正在合成的 job_id 集合 (本地跟踪, SSE compose_done/compose_error/force_stopped 时移除)
function _isComposing(jobId) { return _composingJobIds.has(jobId); }
let _retrying = false;       // 防重入：重试中避免重复点击
let _audioGenES = null;       // 音频生成 SSE（/api/jobs/{id}/events，注意与导演 SSE 前缀不同）
let _audioGenJob = null;      // 当前音频生成任务 id
let _audioGenRetry = 0;       // 音频生成 SSE 连续失败重连计数 (P2-3)
let _sseCollapseTimer = null; // 执行日志抽屉自动收起定时器（ID-012）

// ── Workflow 中文映射 ──
const WF_LABELS = {
  host: '数字人', mixed_host_broll: '数字人+素材',
  broll_pexels: '下载素材', broll_local: '本地素材',
  evidence_image: '证据图',
  hf_chart: 'HF图表', hf_title: 'HF标题',
  black_placeholder: '黑屏兜底',
};
function wfLabel(wf) { return WF_LABELS[wf] || wf || '?'; }

// ── API Helper ──
async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
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

// ── Toast ──
function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 4000);
}

// ── 启用的管线 (2026-08-07 director.html 含 C/P/H 开关, 与流水线页同源持久化) ──
// 开关写 localStorage key 'dh_pipeline_flags' ({c,p,h:bool}); 此处读取并转成数组。
// 若 app.js 已定义全局 getEnabledPipelines, 直接复用; 否则用本地实现。
function _dhGetEnabledPipelines() {
  try {
    const raw = localStorage.getItem('dh_pipeline_flags');
    if (raw) {
      const f = JSON.parse(raw);
      const enabled = [];
      if (f.c) enabled.push('c');
      if (f.p) enabled.push('p');
      if (f.h) enabled.push('h');
      return enabled;
    }
  } catch (e) { /* 损坏则回退默认 */ }
  return ['p', 'h'];
}
// 统一暴露为 window.getEnabledPipelines: director.html 只加载 director.js,
// 这里必须提供; 若 index.html 的 app.js 先行定义则保持其实现 (index.html 不加载本文件)。
// 不用 function 声明: 函数声明提升会让下方 if 判断永远为真 (typeof 总是 function),
// 无法与 app.js 的同名函数区分。window 赋值后才挂到全局作用域, 使 if 判断真实生效。
if (typeof window.getEnabledPipelines !== 'function') {
  window.getEnabledPipelines = function getEnabledPipelines() { return _dhGetEnabledPipelines(); };
}

// ── Step Indicator ──
function updateSteps(status) {
  const steps = ['planning', 'executing', 'reviewing', 'completed'];
  const map = { planning: 0, executing: 1, reviewing: 2, completed: 3, failed: -1 };
  const active = map[status] ?? -1;
  document.querySelectorAll('.step-dot').forEach((dot, i) => {
    dot.classList.remove('active', 'done');
    if (i < active) dot.classList.add('done');
    else if (i === active) dot.classList.add('active');
  });
  document.querySelectorAll('.step-line').forEach((line, i) => {
    line.classList.toggle('done', i < active);
  });
}

// ── Load Scripts ──
const _scriptMeta = {};  // script_id -> {video_format} 画幅提示用 (2026-08-25)
const _FMT_LABELS = { portrait: '竖屏 9:16', landscape: '横屏 16:9', square: '方形 1:1' };

async function loadScripts() {
  const sel = document.getElementById('script-select');
  try {
    const scripts = await fetch('/api/scripts?limit=20').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    sel.innerHTML = '<option value="">— 选择视频 —</option>';
    scripts.forEach(s => {
      _scriptMeta[s.id] = { video_format: s.video_format || 'portrait' };
      const opt = document.createElement('option');
      opt.value = s.id;
      const title = (s.title || '').trim();
      const preview = (s.script_text || '').slice(0, 40).replace(/\n/g, ' ');
      // 标题重复时靠保存时间区分 (2026-08-12)
      const time = s.updated_at ? ' ' + formatTime(s.updated_at) : '';
      opt.textContent = title ? `${title}（${preview}）${time}` : (preview || s.id.slice(0, 8)) + time;
      sel.appendChild(opt);
    });
  } catch (e) { console.error(e); }
  document.getElementById('btn-delete-script').disabled = !sel.value;
  syncScriptTitleInput();
}

function syncScriptTitleInput() {
  const scriptId = document.getElementById('input-script-id').value.trim();
  const titleInput = document.getElementById('input-script-title');
  if (!scriptId || !titleInput) return;
  const sel = document.getElementById('script-select');
  const opt = sel?.querySelector(`option[value="${CSS.escape(scriptId)}"]`);
  if (opt) {
    const text = opt.textContent;
    titleInput.value = text.includes('（') ? text.split('（')[0] : text;
  } else {
    // 下拉列表未命中时回查接口（如流水线 URL 带入的 script_id）
    fetch(`/api/scripts/${encodeURIComponent(scriptId)}`)
      .then(r => r.ok ? r.json() : null)
      .then(s => {
        if (!s) return;
        if (s.title) titleInput.value = s.title;
        _scriptMeta[s.id] = { video_format: s.video_format || 'portrait' };
        // 2026-08-25: 只更新提示, 不碰 format-select — 异步落地会静默覆盖用户手选
        const fmtHint = document.getElementById('script-fmt-hint');
        if (fmtHint) fmtHint.textContent = `脚本现值: ${_FMT_LABELS[s.video_format || 'portrait']}`;
      })
      .catch(() => {});
  }
}

function onScriptSelect(val) {
  document.getElementById('btn-delete-script').disabled = !val;
  document.getElementById('input-script-id').value = val;
  // 画幅 (2026-08-25 修复): 不再回写下拉 — 2026-08-24 的自动回显会把竖屏脚本
  // 的 portrait 静默压掉用户手选, 造成"选横屏输出竖屏"。改为只读提示;
  // 下拉默认横屏且只由用户操作, 回退/重选后选择依然生效.
  const fmtHint = document.getElementById('script-fmt-hint');
  if (fmtHint) {
    const m = _scriptMeta[val];
    fmtHint.textContent = m ? `脚本现值: ${_FMT_LABELS[m.video_format] || m.video_format}` : '';
  }
  syncScriptTitleInput();
  if (val) {
    loadAudioFiles(val);
    loadViewGroups();
    // ID-020 补充: 选中脚本时恢复活跃音频任务订阅 (刷新/跳页后重新进入)
    restoreActiveAudioJob(val);
  }
}

// ID-020 补充: 选中脚本时若有 pending/running 音频任务, 自动重连 SSE 恢复进度
async function restoreActiveAudioJob(scriptId) {
  try {
    const jobs = await fetch(`/api/audio/jobs?script_id=${encodeURIComponent(scriptId)}&limit=5`).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    const active = (jobs || []).find(j => j.status === 'pending' || j.status === 'running' || j.status === 'cancelling');
    if (!active) return;
    if (_audioGenES) { _audioGenES.close(); _audioGenES = null; }
    _audioGenJob = active.id;
    const btn = document.getElementById('btn-regenerate-audio');
    if (btn && !btn.classList.contains('hidden')) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> 生成中...';
    }
    connectAudioGenSSE(active.id);
  } catch (_) { /* 查询失败静默, 不影响选择脚本 */ }
}

function onAudioSelect(val) {
  if (val) document.getElementById('input-audio-id').value = val;
}

async function loadViewGroups() {
  const sel = document.getElementById('view-group-select');
  sel.innerHTML = '<option value="0">加载中…</option>';
  try {
    const roles = await fetch('/api/roles').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    const opts = [];
    roles.forEach(role => {
      const groups = role.view_groups || [];
      if (groups.length === 0) {
        opts.push(`<option value="0">${role.name} (无形象组)</option>`);
      } else {
        groups.forEach((g, i) => {
          opts.push(`<option value="${i}">${role.name} · ${g.name || '组'+i}</option>`);
        });
      }
    });
    sel.innerHTML = opts.length ? opts.join('') : '<option value="0">默认</option>';
  } catch (e) {
    sel.innerHTML = '<option value="0">默认</option>';
  }
}

async function loadAudioFiles(scriptId) {
  const sel = document.getElementById('audio-select');
  sel.innerHTML = '<option value="">加载中…</option>';
  try {
    const files = await fetch(`/api/scripts/${scriptId}/audio-files`).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    sel.innerHTML = '<option value="">— 选择音频 —</option>';
    if (!files.length) {
      sel.innerHTML = '<option value="">该脚本无已完成音频</option>';
      // ID-020: 无音频时显示「生成音频」按钮并重置进度行
      document.getElementById('btn-regenerate-audio').classList.remove('hidden');
      const p = document.getElementById('audio-gen-progress');
      if (p) { p.textContent = ''; p.classList.add('hidden'); }
      return;
    }
    // 有音频时隐藏「生成音频」按钮
    document.getElementById('btn-regenerate-audio').classList.add('hidden');
    files.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.id;
      opt.textContent = `${f.duration}s · ${f.filename || f.id.slice(0,8)}`;
      sel.appendChild(opt);
    });
    sel.value = files[0].id;
    document.getElementById('input-audio-id').value = files[0].id;
  } catch (e) {
    sel.innerHTML = '<option value="">加载失败</option>';
  }
}

// ── Refresh Jobs ──
async function refreshJobs() {
  const status = document.getElementById('filter-status').value;
  const qs = status ? `?status=${encodeURIComponent(status)}&limit=30` : '?limit=30';
  try {
    const jobs = await api('/jobs' + qs);
    const el = document.getElementById('jobs-list');
    if (!jobs.length) {
      el.innerHTML = '<div class="empty"><div class="icon">🎬</div><p>暂无任务</p></div>';
      return;
    }
    el.innerHTML = jobs.map(j => {
      const slotCount = j.slots ? j.slots.length : 0;
      const completedCount = j.slots ? j.slots.filter(s => s.status === 'completed').length : 0;
      const canDelete = j.status !== 'executing';
      const displayTitle = j.title || `未命名任务 ${j.id.slice(0, 8)}…`;
      return `
        <div class="job-item ${j.id === currentJobId ? 'active' : ''}" onclick="selectJob('${j.id}')">
          <div class="job-info">
            <div class="job-id" style="font-weight:600;">${escapeHtml(displayTitle)}</div>
            <div class="job-meta">${slotCount} slots · ${completedCount} 完成</div>
          </div>
          <span class="badge ${j.status}">${j.status}</span>
          <div class="job-time">${formatTime(j.created_at)}</div>
          ${canDelete ? `<button class="btn btn-danger btn-sm job-del-btn" onclick="event.stopPropagation();deleteJob('${j.id}')" title="删除此任务">✕</button>` : ''}
        </div>
      `;
    }).join('');
  } catch (e) {
    toast('加载任务失败: ' + e.message, 'error');
  }
}

// ── Select Job ──
// ID-022: 选中任务持久化到 localStorage, 刷新/跳页返回后恢复
function persistCurrentJob(jobId) {
  if (jobId) localStorage.setItem('director_current_job', jobId);
  else localStorage.removeItem('director_current_job');
}
async function selectJob(jobId) {
  currentJobId = jobId;
  persistCurrentJob(jobId);
  stopPolling();
  try {
    const job = await api(`/jobs/${jobId}`);
    renderJobDetail(job);
    // 错别字替换模块是固定可见的独立模块, 无需 toggle
    // ID-022: 手动点选活跃任务(规划/执行中)时重连 SSE 日志流, 与刷新恢复行为一致。
    // 注意: 不含 reviewing——plan_done/exec_done terminal 事件后任务进入 reviewing,
    //       selectJob 会被自动调用, 此时不应重连(合成场景由 restoreLastDirectorJob 处理)。
    if (job.status === 'planning' || job.status === 'executing' || job.status === 'queued') {
      showExecLog();
      connectSSE(job.id);
    }
    refreshJobs();
  } catch (e) {
    toast('加载详情失败: ' + e.message, 'error');
  }
}

// ── Render Job Detail ──
function renderJobDetail(job) {
  document.getElementById('card-empty').classList.add('hidden');
  document.getElementById('card-detail').classList.remove('hidden');

  document.getElementById('detail-title').textContent = job.title || `任务 ${job.id.slice(0, 12)}…`;
  const badge = document.getElementById('detail-badge');
  badge.textContent = job.status;
  badge.className = `badge ${job.status}`;

  // Video format label
  const fmtLabels = { portrait: '竖屏 9:16', landscape: '横屏 16:9', square: '方形 1:1' };
  const fmtEl = document.getElementById('detail-format');
  if (fmtEl) fmtEl.textContent = fmtLabels[job.video_format] || '竖屏 9:16';

  // 剪映草稿名 (2026-08-25): J 线导出后常驻显示, 剪映草稿列表按此名直接找
  const draftEl = document.getElementById('detail-jy-draft');
  if (draftEl) {
    if (job.jy_draft_name) {
      draftEl.textContent = `🎬 剪映草稿: ${job.jy_draft_name}`;
      draftEl.title = '在剪映草稿列表顶部按此名称查找';
      draftEl.style.display = '';
    } else {
      draftEl.style.display = 'none';
    }
  }

  updateSteps(job.status);

  const rawSlots = job.slots || [];
  // Deduplicate by slot_index: keep the latest non-replaced slot per index.
  // Fallback chain creates "replaced" entries; timeline should only show active ones.
  const byIndex = {};
  for (const s of rawSlots) {
    const cur = byIndex[s.slot_index];
    if (!cur) {
      byIndex[s.slot_index] = s;
    } else if (cur.status === 'replaced' && s.status !== 'replaced') {
      byIndex[s.slot_index] = s;
    } else if (cur.status === 'replaced' && s.status === 'replaced') {
      if (s.created_at > cur.created_at) byIndex[s.slot_index] = s;
    }
    // else: cur is active, keep it
  }
  const slots = Object.values(byIndex).sort((a, b) => a.slot_index - b.slot_index);
  allSlots = slots;

  const isExecuting = job.status === 'executing' || job.status === 'planning';
  _jobExecuting = isExecuting;

  // 「清理已替换」按钮：执行中隐藏，有 replaced 条目时才启用
  const btnPurge = document.getElementById('btn-purge-replaced');
  if (btnPurge) {
    const replacedCount = rawSlots.filter(s => s.status === 'replaced').length;
    btnPurge.disabled = replacedCount === 0 || isExecuting;
    btnPurge.title = replacedCount > 0
      ? `清理 ${replacedCount} 条已替换记录`
      : '无已替换记录';
  }
  const total = slots.length;
  const completed = slots.filter(s => s.status === 'completed').length;
  const running = slots.filter(s => s.status === 'running').length;
  const failed = slots.filter(s => s.status === 'failed').length;
  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-completed').textContent = completed;
  document.getElementById('stat-running').textContent = running;
  document.getElementById('stat-failed').textContent = failed;

  renderTimeline(slots);

  const isDone = job.status === 'completed';
  const isReviewing = job.status === 'reviewing';
  const hasCompleted = slots.some(s => s.status === 'completed');
  const hasQueued = slots.some(s => s.status === 'queued' || s.status === 'replaced');
  const btnExec = document.getElementById('btn-execute');
  btnExec.disabled = isDone || isExecuting;
  if (isExecuting) {
    btnExec.innerHTML = '<span class="spinner"></span> 执行中...';
  } else if (isReviewing && hasCompleted && hasQueued) {
    btnExec.innerHTML = '▶ 继续执行';
  } else if (isReviewing && hasQueued) {
    btnExec.innerHTML = '▶ 执行 Slots';
  } else {
    btnExec.innerHTML = '执行 Slots';
  }

  // #2 停止按钮：仅执行中显示
  const btnCancel = document.getElementById('btn-cancel');
  if (btnCancel) btnCancel.classList.toggle('hidden', !isExecuting);

  // ⚠ 强制停止：执行/规划中常驻；合成进行中变为「强制停止合成」
  const composingActive = _isComposing(job.id);
  const btnForce = document.getElementById('btn-force-stop');
  if (btnForce) {
    const show = isExecuting || composingActive;
    btnForce.classList.toggle('hidden', !show);
    btnForce.textContent = composingActive ? '⚠ 强制停止合成' : '⚠ 强制停止';
    btnForce.title = composingActive
      ? '杀掉本任务合成进程(ffmpeg)并立即终止, 已完成的 slot 保留, 可稍后重试'
      : '杀掉该任务全部子进程(ffmpeg/ComfyUI/HF)并立即终止, 已完成的 slot 保留, 可稍后重试';
  }

  document.getElementById('btn-compose').disabled = !hasCompleted && !isDone;
  const btnScriptTxt = document.getElementById('btn-script-txt');
  if (btnScriptTxt) btnScriptTxt.disabled = !hasCompleted && !isDone;
  const btnJy = document.getElementById('btn-jy-export');
  if (btnJy) btnJy.disabled = !hasCompleted && !isDone;
  document.getElementById('btn-download').disabled = !isDone;

  if (!isDone) {
    document.getElementById('video-preview').classList.add('hidden');
  }
  // 保持已展开的 slot 详情面板
  if (_selectedSlotIdx !== null && _selectedSlotIdx < slots.length) {
    showSlotDetail(_selectedSlotIdx);
  } else {
    document.getElementById('slot-detail').classList.remove('visible');
  }

  if (!isDone && !isReviewing && job.status !== 'failed') {
    startPolling();
  }
}

// ── Timeline ──
function renderTimeline(slots) {
  const el = document.getElementById('timeline');
  el.innerHTML = slots.map((s, i) => {
    const typeShort = wfLabel(s.workflow);
    const camTag = (s.workflow === 'host' || s.workflow === 'mixed_host_broll') ? `C${s.camera_angle || 1}` : '';
    const label = camTag || typeShort;
    // ID-025: black_placeholder completed 用 warning 色提示“合成后会出现黑屏/兜底素材”
    const isFallback = s.workflow === 'black_placeholder' && s.status === 'completed';
    const statusClass = isFallback ? `${s.status} warning` : s.status;
    const titleSuffix = isFallback ? ' (兜底素材)' : '';
    return `
      <div class="timeline-slot ${statusClass}" onclick="showSlotDetail(${i})" title="#${s.slot_index} ${wfLabel(s.workflow)}${camTag ? ' '+camTag : ''}${titleSuffix} (${s.status})">
        <div class="idx">${s.slot_index}</div>
        <div class="type">${label}</div>
      </div>
    `;
  }).join('');
}

// ── Slot Detail ──
function showSlotDetail(idx) {
  const slot = allSlots[idx];
  if (!slot) return;
  _selectedSlotIdx = idx;
  const panel = document.getElementById('slot-detail');
  panel.classList.add('visible');
  document.getElementById('slot-detail-title').textContent = `Slot #${slot.slot_index}`;
  document.getElementById('slot-type').textContent = `${wfLabel(slot.workflow)} [${slot.status}]`;
  document.getElementById('slot-time').textContent = `${slot.start_sec?.toFixed(2) || '?'}s → ${slot.end_sec?.toFixed(2) || '?'}s (时长 ${slot.duration_sec?.toFixed(1) || '?'}s)`;
  document.getElementById('slot-text').textContent = slot.text_context || '—';
  const camLabels = {1:'正面半身', 2:'左侧45度', 3:'右侧45度', 4:'正面特写'};
  const camEl = document.getElementById('slot-camera');
  if (slot.workflow === 'host' || slot.workflow === 'mixed_host_broll') {
    camEl.textContent = `机位${slot.camera_angle || 1} · ${camLabels[slot.camera_angle || 1] || '正面半身'}`;
  } else {
    camEl.textContent = '— (非出镜)';
  }
  document.getElementById('slot-output').textContent = slot.output_path || '—';
  document.getElementById('slot-error').textContent = slot.error_message || '无';
  // ── Slot 视频预览 (2026-08-12): 有产物则内嵌播放, 无则隐藏 ──
  // cache-busting 时间戳: 替换素材后 output_path 变了但 preview URL 不变,
  // 浏览器会缓存旧视频; 加 ?t= 强制 video 重新拉取。设置后调 load() 重载。
  const slotPrev = document.getElementById('slot-video-preview');
  const slotPlayer = document.getElementById('slot-video-player');
  if (slotPrev && slotPlayer) {
    if (slot.output_path && slot.id && (slot.status === 'completed' || slot.status === 'failed' || slot.status === 'skipped')) {
      const bust = Date.now();
      slotPlayer.src = `${API}/jobs/${currentJobId}/slots/${slot.id}/preview?t=${bust}`;
      slotPrev.classList.remove('hidden');
      slotPlayer.load();
    } else {
      slotPlayer.removeAttribute('src');
      slotPrev.classList.add('hidden');
    }
  }
  // ── 素材替换行 (2026-08-12): 仅 broll 类 slot 显示 ──
  const isBroll = slot.workflow === 'broll_pexels' || slot.workflow === 'broll_local';
  const replaceRow = document.getElementById('slot-replace-row');
  const replaceBtn = document.getElementById('btn-replace-material');
  const disableBtn = document.getElementById('btn-disable-material');
  if (replaceRow && replaceBtn) {
    const canOperate = !_jobExecuting && (slot.status !== 'running' || (!_jobExecuting && slot.status === 'running'));
    const slotReady = slot.status === 'completed' || slot.status === 'failed' || slot.status === 'skipped';
    replaceRow.classList.toggle('hidden', !isBroll || _jobExecuting);
    replaceBtn.disabled = !isBroll || _jobExecuting || !slotReady;
    replaceBtn.dataset.slotId = slot.id;
    if (disableBtn) {
      disableBtn.disabled = !isBroll || _jobExecuting || !slotReady;
      disableBtn.dataset.slotId = slot.id;
    }
    document.getElementById('slot-replace-no').value = slot.params_json?.replaced_asset_no || '';
  }
  // ── 强制 P 线下载勾选 (2026-08-12): 仅 broll 类 + 可重新生成时显示 ──
  const forceRow = document.getElementById('slot-retry-force-row');
  if (forceRow) forceRow.classList.toggle('hidden', !isBroll || _jobExecuting);
  // #5 重新生成按钮：completed/failed/skipped 可重新生成，running/queued 不可点
  // 僵尸 running: job 不在执行中 (服务重启/中断遗留) 时 running 也可重试恢复
  const zombieRunning = slot.status === 'running' && !_jobExecuting;
  const canRegenerate = slot.status === 'completed' || slot.status === 'failed' || slot.status === 'skipped' || zombieRunning;
  const btnRetry = document.getElementById('btn-retry-slot');
  btnRetry.dataset.slotId = slot.id;
  btnRetry.classList.toggle('hidden', _jobExecuting);
  btnRetry.disabled = !canRegenerate;
  btnRetry.textContent = slot.status === 'completed' ? '重新生成' : '重试';
  // #6 批量重试按钮：同类型有 completed 或 failed 即可批量
  const btnBatch = document.getElementById('btn-retry-workflow');
  if (btnBatch) {
    btnBatch.classList.toggle('hidden', _jobExecuting);
    btnBatch.dataset.workflow = slot.workflow;
    const canBatch = allSlots.some(s => s.workflow === slot.workflow && (s.status === 'failed' || s.status === 'completed' || s.status === 'skipped'));
    btnBatch.disabled = !canBatch;
    btnBatch.textContent = '重试同类';
  }
}
function closeSlotDetail() {
  _selectedSlotIdx = null;
  document.getElementById('slot-detail').classList.remove('visible');
}

// ── Create Job ──
async function createJob() {
  const scriptId = document.getElementById('input-script-id').value.trim();
  if (!scriptId) { toast('请输入脚本 ID', 'error'); return; }
  const audioId = document.getElementById('input-audio-id').value.trim() || undefined;
  const viewGroupIdx = parseInt(document.getElementById('view-group-select').value) || 0;

  // 管线开关已迁至流水线页(index.html)并经 localStorage 持久化; 这里统一读取。
  // 修复 (2026-08-07): 全开 (c,p,h 三个) 之前被塌缩成 undefined → 后端 None = 无约束,
  // LLM 面对写死 host 的提示词必然回归数字人。现在:
  //   - 全开 → 仍发 "c,p,h" (后端 _encode: 3 个 = None 全启用, 允许 host, 语义正确)
  //   - 部分 → 发 "c,p" 等 (约束块 + _strip_host_mode 生效)
  //   - 全关 → 发 "" (后端 _decode: "" → set() 全关, 只跑本地/黑场兜底)
  const enabled = getEnabledPipelines();
  const pipelines = enabled.length === 0 ? '' : enabled.join(',');

  // 失败现场: 点击即记录本次请求参数 (后端无日志落盘时用于回溯)
  const videoFormat = (document.getElementById('format-select') || {}).value || undefined;
  const attempt = {
    script_id: scriptId, audio_id: audioId,
    view_group_index: viewGroupIdx, pipelines, video_format: videoFormat,
    t: new Date().toISOString(),
  };
  try { localStorage.setItem('director_create_attempt', JSON.stringify(attempt)); } catch (_) {}

  const btn = document.getElementById('btn-create');
  const spinner = document.getElementById('spinner-create');
  btn.disabled = true;
  spinner.classList.remove('hidden');
  try {
    const resp = await api('/jobs', {
      method: 'POST',
      body: JSON.stringify({ script_id: scriptId, audio_file_id: audioId, view_group_index: viewGroupIdx, pipelines, video_format: videoFormat }),
    });
    currentJobId = resp.job_id;
    try {
      const rec = JSON.parse(localStorage.getItem('director_create_attempt') || '{}');
      rec.result = 'ok';
      rec.job_id = resp.job_id;
      localStorage.setItem('director_create_attempt', JSON.stringify(rec));
    } catch (_) {}
    toast('规划已提交后台，进度将实时显示…', 'info');
    showExecLog();
    connectSSE(currentJobId);
    await refreshJobs();
    await selectJob(resp.job_id);
  } catch (e) {
    try {
      const rec = JSON.parse(localStorage.getItem('director_create_attempt') || '{}');
      rec.result = 'error';
      rec.error = String(e && e.message || e);
      localStorage.setItem('director_create_attempt', JSON.stringify(rec));
    } catch (_) {}
    toast('创建失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    spinner.classList.add('hidden');
  }
}

// ── Execute Job ──

// 管线开关: 与流水线页 index.html 同源, 持久化到 localStorage 'dh_pipeline_flags'。
// director.html 未加载 app.js, 故在此本地实现 (与 app.js 的 getPipelineFlags/savePipelineFlags 一致)。
function _getPipelineFlags() {
  try {
    const raw = localStorage.getItem('dh_pipeline_flags');
    if (raw) {
      const f = JSON.parse(raw);
      if (f && typeof f === 'object') return f;
    }
  } catch (e) { /* 损坏则回退默认 */ }
  // 默认 C 线关 (2026-08-07): 数字人出镜默认不启用, 只跑 P/H 线
  return { c: false, p: true, h: true };
}
function _savePipelineFlags(f) {
  try { localStorage.setItem('dh_pipeline_flags', JSON.stringify(f)); } catch (_) {}
}
function onPipelineToggle() {
  const f = _getPipelineFlags();
  f.c = document.getElementById('toggle-c')?.checked ?? f.c;
  f.p = document.getElementById('toggle-p')?.checked ?? f.p;
  f.h = document.getElementById('toggle-h')?.checked ?? f.h;
  _savePipelineFlags(f);
  const n = (f.c ? 1 : 0) + (f.p ? 1 : 0) + (f.h ? 1 : 0);
  if (n === 3) {
    toast('⚠ 三条管线全开: 将启用数字人出镜, LLM 可能规划 host slot。若只要 P/H 请关闭 C 线', 'warn');
  } else if (n === 0) {
    toast('⚠ 全部关闭，将只跑兜底(本地素材/黑场)', 'info');
  } else {
    toast(`管线已保存: ${(['c','p','h']).filter(k => f[k]).join(',')}`, 'info');
  }
}

let _evtSource = null;
let _sseRetry = 0;        // SSE 连续失败重连计数 (P2-3: readyState===CLOSED 永不成立, 改计数)
const SSE_MAX_RETRY = 5;  // 连续失败上限, 超过视为服务端已停止
async function executeJob() {
  if (!currentJobId) return;
  const btn = document.getElementById('btn-execute');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 执行中...';
  toast('已提交后台执行，进度将自动刷新...', 'info');
  showExecLog();
  connectSSE(currentJobId);
  try {
    // 管线开关经 localStorage 统一读取 (流水线页设置)。
    // 始终显式发送: 全开发 "c,p,h" (后端折 None = 允许 host), 全关发 "" (后端 set() 全关),
    // 部分发 "c,p" 等 (约束块 + _strip_host_mode 生效)。
    const enabled = getEnabledPipelines();
    let url = `/jobs/${currentJobId}/execute?pipelines=${enabled.join(',')}`;
    const job = await api(url, { method: 'POST' });
    renderJobDetail(job);
    refreshJobs();
  } catch (e) {
    toast('执行触发失败: ' + e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = '执行 Slots';
    disconnectSSE();
    // 🚀 自动模式: 触发失败无终态事件, 立即中断
    if (_onestopActive) _onestopFail('执行触发失败: ' + e.message);
  }
}

// ── SSE Execution Log ──
function showExecLog() {
  const el = document.getElementById('exec-log');
  el.classList.remove('hidden');
  el.classList.add('visible');
  const parent = el.parentElement;
  if (parent) { el.style.width = parent.clientWidth + 'px'; }
  const bodyEl = document.getElementById('exec-log-body');
  bodyEl.style.width = '100%';
  // ID-012: 清空 <pre> 文本而非整体重建 innerHTML, 避免销毁 <pre id="exec-log-pre">
  // 导致后续 appendExecEvent 的 getElementById 返回 null、SSE 事件全部静默丢弃
  const pre = document.getElementById('exec-log-pre');
  if (pre) pre.textContent = '';
  const btn = document.getElementById('btn-toggle-log');
  if (btn) btn.textContent = '📡 收起日志';
}
function toggleExecLog() {
  const el = document.getElementById('exec-log');
  const isOpen = el.classList.toggle('visible');
  el.classList.toggle('hidden', !isOpen);
  const btn = document.getElementById('btn-toggle-log');
  if (btn) btn.textContent = isOpen ? '📡 收起日志' : '📡 日志';
}
function clearExecLog() {
  // ID-012: 同 showExecLog, 只清 <pre> 文本, 不销毁节点
  const pre = document.getElementById('exec-log-pre');
  if (pre) pre.textContent = '';
}
function disconnectSSE() {
  if (_evtSource) { _evtSource.close(); _evtSource = null; }
  setTimeout(() => { document.body.style.paddingBottom = ''; }, 3000);
}
// ── 🚀 一键成片 自动串联 (2026-08-09) ──
// 首页 index.html 点击「一键成片」→ 音频完成后跳转本页(auto=1), 并写 localStorage 'dh_onestop'='1'。
// 本页按 SSE 终态事件自动触发下一步: plan_done→executeJob → exec_done→composeJob → compose_done(结束)。
// 防重入: 每次触发前主动 disconnectSSE + 校验标志, 消除双击/重连导致的二次触发。
let _onestopActive = false;
let _onestopTransitioning = false;  // 防重入锁: 仅在同一阶段 transition 期间置位, 不改变 _onestopActive
function _onestopOn() {
  try { return localStorage.getItem('dh_onestop') === '1'; } catch (_) { return false; }
}
function _onestopClear() {
  try { localStorage.removeItem('dh_onestop'); } catch (_) {}
  _onestopActive = false;
}
function _onestopFail(msg) {
  _onestopClear();
  toast('一键成片已中断: ' + msg, 'error');
  const btnExec = document.getElementById('btn-execute');
  if (btnExec) { btnExec.disabled = false; btnExec.innerHTML = '执行 Slots'; }
  const btnComp = document.getElementById('btn-compose');
  if (btnComp) { btnComp.disabled = false; btnComp.innerHTML = '合成视频'; }
  // 中断不跳转, 留在导演台手动续跑 (产出信息未丢失, 可重试)
}
// terminal 事件: 结束 SSE 连接; 其余(心跳/进度/warning)保持连接等待后续事件
const SSE_TERMINAL = new Set(['plan_error', 'plan_cancelled', 'plan_done', 'exec_done', 'exec_cancelled', 'compose_done', 'compose_error', 'force_stopped']);
function connectSSE(jobId) {
  disconnectSSE();
  _sseRetry = 0;
  _evtSource = new EventSource(`/api/director/jobs/${jobId}/events`);
  _evtSource.onmessage = async (e) => {
    try {
      const d = JSON.parse(e.data);
      appendExecEvent(d);
      if (SSE_TERMINAL.has(d.type)) {
        disconnectSSE();
        // compose 终止 (含强停): 本地合成集合移除, 按钮恢复
        if (d.type === 'compose_done' || d.type === 'compose_error' || d.type === 'force_stopped') {
          _composingJobIds.delete(currentJobId);
        }
        // 恢复执行/合成按钮
        const btnExec = document.getElementById('btn-execute');
        if (btnExec && (d.type === 'exec_done' || d.type === 'exec_cancelled')) {
          btnExec.disabled = false; btnExec.innerHTML = '执行 Slots';
        }
        const btnComp = document.getElementById('btn-compose');
        if (btnComp) { btnComp.disabled = false; btnComp.innerHTML = '合成视频'; }
        // 刷新任务详情: 让时间轴/下载按钮同步最新状态
        if (d.type === 'plan_done' || d.type === 'exec_done' || d.type === 'compose_done' || d.type === 'force_stopped') {
          await selectJob(currentJobId);
          refreshJobs();
        }
        if (d.type === 'compose_done') {
          const sec = d.duration_sec != null ? ` ${d.duration_sec.toFixed(1)}s` : '';
          toast('合成完成' + sec, 'success');
          // 自动打开产物所在文件夹
          if (d.output_path) {
            setTimeout(() => openOutputFolder(), 500);
          }
          // 🚀 一键成片: 全流程完成, 结束自动模式
          if (_onestopActive) {
            _onestopClear();
            toast('🚀 一键成片完成', 'success');
          }
        } else if (d.type === 'compose_error') {
          toast('合成失败: ' + (d.msg || '未知错误'), 'error');
          if (_onestopActive) _onestopFail(d.msg || '合成失败');
        } else if (d.type === 'force_stopped') {
          toast('任务已强制停止: ' + (d.msg || ''), 'warning');
          if (_onestopActive) _onestopFail('已强制停止');
        } else if (d.type === 'plan_done') {
          // 🚀 自动模式: 规划完成 → 自动执行 Slots (防重入: 双击/重连会产生重复 plan_done)
          if (_onestopActive && !_onestopTransitioning) {
            _onestopTransitioning = true;
            try { await executeJob(); } finally { _onestopTransitioning = false; }
          }
        } else if (d.type === 'exec_done') {
          // 🚀 自动模式: Slots 执行完成 → 自动合成
          if (_onestopActive && !_onestopTransitioning) {
            _onestopTransitioning = true;
            try { await composeJob(); } finally { _onestopTransitioning = false; }
          }
        } else if (d.type === 'plan_error' || d.type === 'plan_cancelled') {
          if (_onestopActive) _onestopFail(d.msg || (d.type === 'plan_error' ? '规划失败' : '规划已取消'));
        } else if (d.type === 'exec_cancelled') {
          if (_onestopActive) _onestopFail('执行已取消');
        }
        // ID-012: 仅 terminal 事件后 3s 自动收起日志抽屉 (心跳/进度不收起)
        if (_sseCollapseTimer) { clearTimeout(_sseCollapseTimer); }
        _sseCollapseTimer = setTimeout(() => {
          const el = document.getElementById('exec-log');
          if (el && el.classList.contains('visible')) {
            el.classList.remove('visible');
            el.classList.add('hidden');
            const b = document.getElementById('btn-toggle-log');
            if (b) b.textContent = '📡 日志';
          }
        }, 3000);
      }
    } catch (_) {}
  };
  _evtSource.onerror = () => {
    // EventSource 失败后自动进入 CONNECTING 并重连, readyState 不会停留 CLOSED
    // (除非显式 .close()). 原判断 readyState===CLOSED 永不成立 → 无限重连.
    // 改用重连计数: 超过 SSE_MAX_RETRY 次连续失败视为服务端已停止, 主动断开.
    _sseRetry++;
    if (_sseRetry > SSE_MAX_RETRY) {
      disconnectSSE();
      _sseRetry = 0;
      const btnExec = document.getElementById('btn-execute');
      if (btnExec) { btnExec.disabled = false; btnExec.innerHTML = '执行 Slots'; }
    }
  };
}
function appendExecEvent(d) {
  const pre = document.getElementById('exec-log-pre');
  if (!pre) return;
  let text = '', color = '#8892a8';
  switch (d.type) {
    case 'plan_start': case 'exec_start':
      color = '#3b82f6'; text = '\u25B6 ' + (d.msg || ''); break;
    case 'phase_start':
      color = '#3b82f6'; text = '\u25B6 Phase ' + d.phase_idx + ': ' + d.phase + ' (' + d.slot_count + ' slots)'; break;
    case 'phase_end':
      color = '#3b82f6'; text = '\u2713 Phase ' + d.phase_idx + ': ' + d.phase + ' done'; break;
    case 'alignment_start': case 'alignment_progress': case 'service_start': case 'llm_start': case 'slot_start':
      color = '#fbbf24'; text = '  \u251C\u2500 ' + (d.msg || ('Slot #' + d.slot_index + ' ' + d.workflow)); break;
    case 'alignment_done': case 'service_ready': case 'llm_done': case 'slot_done':
      color = '#34d399'; text = '  \u251C\u2500 ' + (d.msg || ('Slot #' + d.slot_index + ' ok')); break;
    case 'llm_error': case 'slot_fail': case 'plan_error':
      color = '#f87171'; text = '  \u251C\u2500 ' + (d.msg || ('Slot #' + d.slot_index + ' FAIL')); break;
    case 'slot_skip':
      color = '#94a3b8'; text = '  \u251C\u2500 Slot #' + d.slot_index + ' SKIP: ' + (d.reason || ''); break;
    case 'service_stop':
      color = '#fbbf24'; text = '  \u2514\u2500 ' + (d.msg || ''); break;
    case 'plan_done': case 'exec_done':
      color = '#3b82f6'; text = '\uD83C\uDFC1 ' + (d.msg || ''); break;
    case 'plan_cancelled': case 'exec_cancelled':
      color = '#f59e0b'; text = '\u26A0 ' + (d.msg || '已取消'); break;
    case 'force_stopped':
      color = '#f87171'; text = '🚨 强制停止: ' + (d.msg || '任务已强制停止'); break;
    case 'heartbeat':
      color = '#64748b'; text = '  ⏳ ' + (d.msg || '服务端仍在运行') + (d.elapsed_sec != null ? '（已等待 ' + Math.round(d.elapsed_sec) + 's）' : ''); break;
    case 'alignment_heartbeat':
      color = '#64748b'; text = '  ⏳ ' + (d.msg || '模型加载中') + (d.elapsed_sec != null ? '（已等待 ' + Math.round(d.elapsed_sec) + 's）' : ''); break;
    case 'slot_progress':
      color = '#64748b'; text = '  ⏳ ' + (d.msg || ('Slot #' + d.slot_index + ' 运行中')) + (d.elapsed_sec != null ? '（已运行 ' + Math.round(d.elapsed_sec) + 's）' : ''); break;
    case 'plan_warning':
      color = '#fbbf24'; text = '  ⚠ ' + (d.msg || '规划警告'); break;
    default:
      text = '  ' + JSON.stringify(d); break;
  }
  const span = document.createElement('span');
  span.style.color = color;
  span.textContent = text + '\n';
  pre.appendChild(span);
  pre.parentElement.scrollTop = pre.parentElement.scrollHeight;
}

// ── Compose Job (异步: POST 立即返回, 进度经导演 SSE 事件推送) ──
async function composeJob() {
  if (!currentJobId) return;
  const btn = document.getElementById('btn-compose');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 合成中...';
  toast('已提交合成，后台执行中，进度见日志...', 'info');
  showExecLog();
  connectSSE(currentJobId);
  try {
    // 端点立即返回 {status:'composing'}, compose_start/compose_step/compose_done
    // 由 connectSSE 接收渲染; compose_done 后自动刷新详情 + 下载按钮可用
    await api(`/jobs/${currentJobId}/compose`, { method: 'POST' });
    // 登记本地合成集合: 让「⚠ 强制停止合成」按钮在合成期间显示
    _composingJobIds.add(currentJobId);
    if (btn) btn.textContent = '⏳ 合成中...';
  } catch (e) {
    toast('合成触发失败: ' + e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = '合成视频';
    disconnectSSE();
    // 🚀 自动模式: 触发失败无终态事件, 立即中断
    if (_onestopActive) _onestopFail('合成触发失败: ' + e.message);
  }
}

// ── Download Job ──
function downloadJob() {
  if (!currentJobId) return;
  const a = document.createElement('a');
  a.href = `${API}/jobs/${currentJobId}/download`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast('开始下载...', 'success');
}

// ── J 线: 导出剪映草稿 (同步端点, 纯写盘 <1s; 音画不合成, 渲染交给剪映) ──
async function exportJyDraft() {
  if (!currentJobId) return;
  const btn = document.getElementById('btn-jy-export');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 生成草稿...'; }
  try {
    const res = await api(`/jobs/${currentJobId}/export-jy-draft`, { method: 'POST' });
    const skipped = res.skipped_slots?.length ? ` (⚠ ${res.skipped_slots.length} 个 slot 产物缺失已跳过)` : '';
    const r9 = res.emphasis_words != null ? ` + R9编排: ${res.emphasis_words}强调字/${res.sfx_attached}音效` : '';
    toast(`🎬 草稿「${res.draft_name}」已放入剪映 (${res.video_segments}画面 + ${res.audio_segments}音频段 + ${res.text_segments}字幕)${r9}${skipped} — 打开剪映在列表顶部查看`, 'success');
    // 2026-08-25: 导出成功 → 后端置 completed + 落草稿名; 刷新详情让状态/草稿名即时上屏
    await selectJob(currentJobId);
  } catch (e) {
    toast('导出剪映草稿失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '🎬 导出剪映草稿'; }
  }
}

// ── Open Output Folder ──
async function openOutputFolder() {
  if (!currentJobId) return;
  try {
    const res = await api(`/jobs/${currentJobId}/open-folder`, { method: 'POST' });
    if (res.status === 'ok') {
      toast('已打开产物文件夹 📂', 'success');
    }
  } catch (e) {
    toast('打开文件夹失败: ' + e.message, 'error');
  }
}

// ── 文稿.txt (2026-08-19): 合成按钮的稿件功能独立 — 不跑合成, 写洗稿.txt+开文件夹 ──
async function exportScriptTxt() {
  if (!currentJobId) return;
  const btn = document.getElementById('btn-script-txt');
  if (btn) btn.disabled = true;
  try {
    await api(`/jobs/${currentJobId}/export-script-txt`, { method: 'POST' });
    toast('文稿.txt 已生成并打开文件夹 📄', 'success');
  } catch (e) {
    toast('文稿.txt 生成失败: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── 重试按钮禁用/恢复辅助 ──
function _setRetryButtonsDisabled(disabled, loadingText) {
  const btnSlot = document.getElementById('btn-retry-slot');
  const btnBatch = document.getElementById('btn-retry-workflow');
  if (btnSlot) {
    btnSlot.disabled = disabled;
    if (loadingText) btnSlot.textContent = loadingText;
    else if (!disabled) btnSlot.textContent = '重试';
  }
  if (btnBatch) {
    btnBatch.disabled = disabled;
    if (loadingText) btnBatch.textContent = loadingText;
    else if (!disabled) btnBatch.textContent = '重试同类';
  }
}

// ── Retry Slot ──
async function retrySlot(btn) {
  // 防重入：如果已有重试进行中，忽略后续点击
  if (_retrying) return;
  // 立即禁用点击的按钮（双重保险：直接用传入的 DOM 元素）
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 执行中…'; }
  _setRetryButtonsDisabled(true, '⏳ 执行中…');
  _retrying = true;

  const slotId = document.getElementById('btn-retry-slot')?.dataset?.slotId;
  if (!currentJobId || !slotId) {
    toast('无法重试：请先选择任务和失败 Slot', 'error');
    _setRetryButtonsDisabled(false);
    _retrying = false;
    return;
  }
  const slotIdx = _selectedSlotIdx;
  // 展示执行日志抽屉并连接 SSE: 后端 retry_slot 会发布 slot_start/slot_done 事件,
  // 让用户能看到重试的真实进度 (与「重试同类」走 /execute 的事件流一致)。
  showExecLog();
  connectSSE(currentJobId);
  try {
    // 管线开关经 localStorage 统一读取，重试时也遵守禁用规则。
    // 始终显式发送 (与 executeJob 一致): 全开 "c,p,h" / 部分 "c,p" / 全关 ""
    const enabled = getEnabledPipelines();
    let url = `/jobs/${currentJobId}/slots/${slotId}/retry?pipelines=${enabled.join(',')}`;
    // force_pexels (2026-08-12): 勾选后强制 P 线下载新素材, 跳过本地库
    const forcePexels = document.getElementById('retry-force-pexels')?.checked;
    if (forcePexels) url += '&force_pexels=true';
    const resp = await api(url, { method: 'POST' });
    const statusText = resp.status === 'completed' ? '已完成' : resp.status;
    const toastLevel = resp.status === 'completed' ? 'success' : (resp.status === 'failed' ? 'error' : 'info');
    toast(`Slot #${slotIdx != null ? slotIdx + 1 : '?'} 重试: ${statusText}`, toastLevel);
    await selectJob(currentJobId);
  } catch (e) {
    toast('重试失败: ' + e.message, 'error');
    _setRetryButtonsDisabled(false);
  } finally {
    _retrying = false;
  }
}

// ── 替换 slot 素材 (2026-08-12): 填素材库编号 → 后端重渲染 → 即时刷新预览 ──
async function replaceSlotMaterial(btn) {
  if (_retrying) return;
  const slotId = btn?.dataset?.slotId;
  const noInput = document.getElementById('slot-replace-no');
  const assetNo = noInput?.value?.trim();
  if (!currentJobId || !slotId) { toast('无法替换：请先选择任务和 Slot', 'error'); return; }
  if (!assetNo) { toast('请先填写素材库编号 (如 V20260803-0177)', 'error'); noInput?.focus(); return; }

  _retrying = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 替换中…'; }
  try {
    const resp = await api(`/jobs/${currentJobId}/slots/${slotId}/replace-material`, {
      method: 'POST',
      body: JSON.stringify({ asset_no: assetNo }),
    });
    const ok = resp.status === 'completed';
    toast(`Slot #${_selectedSlotIdx != null ? _selectedSlotIdx + 1 : '?'} 替换素材: ${resp.status}${resp.message ? ' · ' + resp.message : ''}`, ok ? 'success' : 'error');
    // 刷新任务数据 → timeline + slot 详情 + 预览立即更新为新素材 (即时生效)
    await selectJob(currentJobId);
    if (_selectedSlotIdx != null) showSlotDetail(_selectedSlotIdx);
  } catch (e) {
    toast('替换素材失败: ' + e.message, 'error');
  } finally {
    _retrying = false;
    if (btn) { btn.disabled = false; btn.textContent = '替换'; }
  }
}

// ── 禁用 slot 素材 (2026-08-12): 当前素材不好 → 打禁用标, 后续不再选用 ──
async function disableSlotMaterial(btn) {
  if (_retrying) return;
  const slotId = btn?.dataset?.slotId;
  if (!currentJobId || !slotId) { toast('无法禁用：请先选择任务和 Slot', 'error'); return; }
  if (!confirm('确认禁用当前 Slot 的素材？禁用后该素材在本地/在线都不会再被选用。')) return;

  _retrying = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 禁用中…'; }
  try {
    const resp = await api(`/jobs/${currentJobId}/slots/${slotId}/disable-material`, { method: 'POST' });
    toast(`已禁用素材 ${resp.asset_no || ''} (Slot #${_selectedSlotIdx != null ? _selectedSlotIdx + 1 : '?'})`, 'success');
    await selectJob(currentJobId);
    if (_selectedSlotIdx != null) showSlotDetail(_selectedSlotIdx);
  } catch (e) {
    toast('禁用素材失败: ' + e.message, 'error');
  } finally {
    _retrying = false;
    if (btn) { btn.disabled = false; btn.textContent = '🚫 禁用素材'; }
  }
}

// ── #6 按 workflow 批量重试 ──
async function retryByWorkflow(btn) {
  // 防重入
  if (_retrying) return;
  // 立即禁用点击的按钮
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 重置中…'; }
  _setRetryButtonsDisabled(true, '⏳ 重置中…');
  _retrying = true;

  const wf = document.getElementById('btn-retry-workflow')?.dataset.workflow;
  if (!currentJobId || !wf) {
    toast('无法批量重试：请先选择任务和失败 Slot', 'error');
    _setRetryButtonsDisabled(false);
    _retrying = false;
    return;
  }
  const retryableCount = allSlots.filter(s => s.workflow === wf && (s.status === 'failed' || s.status === 'completed' || s.status === 'skipped')).length;
  if (!retryableCount) {
    toast('该类型无可重新生成的 slot', 'info');
    _setRetryButtonsDisabled(false);
    _retrying = false;
    return;
  }
  if (!confirm(`将重新生成所有 ${wfLabel(wf)} 类型的 ${retryableCount} 个 slot，确认？`)) {
    _setRetryButtonsDisabled(false);
    _retrying = false;
    return;
  }
  try {
    const resp = await api(`/jobs/${currentJobId}/retry-workflow?workflow=${encodeURIComponent(wf)}`, { method: 'POST' });
    toast(`已重置 ${resp.reset_count || retryableCount} 个 slot，自动触发执行…`, 'success');
    // 重置成功后自动触发执行，避免用户还需手动点"执行 Slots"
    await executeJob();
  } catch (e) {
    toast('批量重试失败: ' + e.message, 'error');
  } finally {
    _setRetryButtonsDisabled(false);
    _retrying = false;
  }
}

// ── 清理 replaced slots（fallback 链产物） ──
async function purgeReplaced() {
  if (!currentJobId) return;
  try {
    const resp = await api(`/jobs/${currentJobId}/purge-replaced`, { method: 'POST' });
    const n = resp.purged_count || 0;
    toast(n > 0 ? `已清理 ${n} 条已替换记录` : '无需清理', n > 0 ? 'success' : 'info');
    if (n > 0) await selectJob(currentJobId);
  } catch (e) {
    toast('清理失败: ' + e.message, 'error');
  }
}

// ── #2 取消执行 ──
async function cancelJob() {
  if (!currentJobId) return;
  if (!confirm('确认停止当前任务执行？已完成的 slot 不会丢失。')) return;
  try {
    await api(`/jobs/${currentJobId}/cancel`, { method: 'POST' });
    toast('已发送停止信号', 'info');
    await selectJob(currentJobId);
  } catch (e) {
    toast('取消失败: ' + e.message, 'error');
  }
}

// ── ⚠ 强制停止 (kill 卡死子进程) ──
async function forceStopJob() {
  if (!currentJobId) return;
  const composing = _isComposing(currentJobId);
  const hint = composing
    ? '强制停止会中断本次合成的全部 ffmpeg 进程。已完成的 slot 保留，可稍后重试。确认强制停止合成？'
    : '强制停止会杀掉该任务的全部子进程(ffmpeg/ComfyUI/HF)，立即终止当前执行/规划。\n已完成的 slot 保留，可稍后重试。确认强制停止？';
  if (!confirm(hint)) return;
  const btn = document.getElementById('btn-force-stop');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 强制停止中...'; }
  try {
    const resp = await api(`/jobs/${currentJobId}/force-stop`, { method: 'POST' });
    const killed = resp.killed != null ? ` (杀掉 ${resp.killed} 个进程树)` : '';
    toast(`已强制停止，任务状态: ${resp.status}${killed}`, resp.status === 'failed' ? 'warning' : 'success');
    await selectJob(currentJobId);
    refreshJobs();
  } catch (e) {
    toast('强制停止失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; }
  }
}

// ── Polling ──
function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (!currentJobId) return;
    try {
      const job = await api(`/jobs/${currentJobId}`);
      renderJobDetail(job);
      if (job.status === 'completed' || job.status === 'failed' || job.status === 'reviewing') {
        stopPolling();
        disconnectSSE();
        if (job.status === 'reviewing') {
          const done = (job.slots || []).filter(s => s.status === 'completed').length;
          toast(`执行完成: ${done} slots 成功，可合成视频`, 'success');
        }
        if (job.status === 'completed') {
          const preview = document.getElementById('video-preview');
          const player = document.getElementById('video-player');
          player.src = `${API}/jobs/${currentJobId}/download`;
          preview.classList.remove('hidden');
          document.getElementById('btn-download').disabled = false;
        }
        refreshJobs();
      }
    } catch (e) { /* keep polling */ }
  }, 3000);
}
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

// ── Utils ──
function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch { return ts; }
}

// ── Delete / Cleanup Jobs ──
async function deleteJob(jobId) {
  if (!confirm('确认删除此任务？关联的 slots 也会一并删除。')) return;
  try {
    await api(`/jobs/${jobId}`, { method: 'DELETE' });
    toast('任务已删除', 'success');
    if (currentJobId === jobId) {
      currentJobId = null;
      document.getElementById('card-detail').classList.add('hidden');
      document.getElementById('card-empty').classList.remove('hidden');
    }
    await refreshJobs();
  } catch (e) {
    toast('删除失败: ' + e.message, 'error');
  }
}

async function cleanupJobs() {
  if (!confirm('将删除所有从未产出视频的失败/规划任务，确认？')) return;
  try {
    const resp = await api('/jobs/cleanup?only_empty=true', { method: 'POST' });
    const n = resp.deleted_count || 0;
    toast(n > 0 ? `已清理 ${n} 个空任务` : '无需清理', n > 0 ? 'success' : 'info');
    if (n > 0) {
      // 如果当前选中的被删了，重置
      if (resp.deleted_ids && resp.deleted_ids.includes(currentJobId)) {
        currentJobId = null;
        document.getElementById('card-detail').classList.add('hidden');
        document.getElementById('card-empty').classList.remove('hidden');
      }
      await refreshJobs();
    }
  } catch (e) {
    toast('清理失败: ' + e.message, 'error');
  }
}

// ── Delete Script (级联 + 回收站, ID-021) ──
async function deleteScript() {
  const scriptId = document.getElementById('input-script-id').value.trim();
  if (!scriptId) { toast('请先选择脚本', 'error'); return; }
  const name = (document.getElementById('script-select').selectedOptions[0]?.textContent || scriptId).slice(0, 40);
  if (!confirm(`确认删除脚本「${name}」？\n将同时删除其所有音频文件（可回收站恢复），源文章保留。`)) return;
  try {
    const r = await fetch(`/api/scripts/${encodeURIComponent(scriptId)}`, { method: 'DELETE' });
    if (!r.ok) {
      let detail = r.statusText;
      try { const j = await r.json(); detail = j.detail || detail; } catch {}
      throw new Error(`${r.status} ${detail}`);
    }
    toast('脚本已删除', 'success');
    if (_audioGenES) { _audioGenES.close(); _audioGenES = null; }
    _audioGenJob = null;
    document.getElementById('input-script-id').value = '';
    document.getElementById('input-audio-id').value = '';
    document.getElementById('audio-select').innerHTML = '<option value="">— 先选脚本 —</option>';
    document.getElementById('btn-regenerate-audio').classList.add('hidden');
    document.getElementById('btn-delete-script').disabled = true;
    await loadScripts();
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

// ── Regenerate Audio (无已完成音频时一键生成, ID-020) ──
// 复用流水线 index.html 同一端点 /api/audio/scripts/{script_id}/generate-audio
// SSE 事件走 /api/jobs/{job_id}/events (注意与导演 SSE /api/director/jobs/... 前缀不同)
async function regenerateAudio() {
  const scriptId = document.getElementById('input-script-id').value.trim();
  if (!scriptId) { toast('请先选择脚本', 'error'); return; }

  // 已有活跃任务(僵尸恢复场景)则直接订阅, 不重复新建
  // 注意: GET /api/audio/jobs?status= 是精确匹配, 无 "active" 复合值, 需取全量列表自行判断
  try {
    const jobs = await fetch(`/api/audio/jobs?script_id=${encodeURIComponent(scriptId)}&limit=5`).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    const active = (jobs || []).find(j => j.status === 'pending' || j.status === 'running' || j.status === 'cancelling');
    if (active) { _audioGenJob = active.id; }
  } catch (_) {}

  if (!_audioGenJob) {
    const btn = document.getElementById('btn-regenerate-audio');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 生成中...';
    try {
      const resp = await fetch(`/api/audio/scripts/${encodeURIComponent(scriptId)}/generate-audio?selected_only=true`, { method: 'POST' });
      if (!resp.ok) {
        let detail = resp.statusText;
        try { const j = await resp.json(); detail = j.detail || detail; } catch {}
        throw new Error(`${resp.status} ${detail}`);
      }
      const data = await resp.json();
      _audioGenJob = data.id;
    } catch (e) {
      toast('生成音频失败: ' + e.message, 'error');
      _resetSpinnerBtns();
      return;
    }
  }

  connectAudioGenSSE(_audioGenJob);
}

function connectAudioGenSSE(jobId) {
  if (_audioGenES) { _audioGenES.close(); _audioGenES = null; }
  _audioGenRetry = 0;
  const prog = document.getElementById('audio-gen-progress');
  if (prog) { prog.textContent = '连接进度流…'; prog.classList.remove('hidden'); }
  _audioGenES = new EventSource(`/api/jobs/${jobId}/events`);
  _audioGenES.onmessage = (e) => {
    let d; try { d = JSON.parse(e.data); } catch (_) { return; }
    if (!prog) return;
    switch (d.type) {
      case 'tts_service':
        prog.textContent = `⏳ ${d.message || '准备合成…'}`;
        break;
      case 'tts_progress':
        prog.textContent = `🎙️ ${d.completed ?? 0}/${d.total ?? '?'} 句 · ${(d.text || '').slice(0, 18)}${(d.text || '').length > 18 ? '…' : ''}`;
        if (d.audio_file?.id) {
          const opt = document.createElement('option');
          opt.value = d.audio_file.id;
          opt.textContent = `${d.audio_file.duration ?? '?'}s · ${d.audio_file.filename || d.audio_file.id.slice(0, 8)}（生成中）`;
          const sel = document.getElementById('audio-select');
          if (sel) sel.appendChild(opt);
        }
        break;
      case 'tts_done':
        if (prog) { prog.textContent = '✅ 音频生成完成'; prog.classList.add('hidden'); }
        toast('音频生成完成', 'success');
        _audioGenES.close(); _audioGenES = null;
        _audioGenJob = null;
        _resetSpinnerBtns();
        loadAudioFiles(document.getElementById('input-script-id').value.trim());
        break;
      case 'tts_error':
        if (prog) { prog.textContent = ''; prog.classList.add('hidden'); }
        toast('音频生成失败: ' + (d.error || '未知错误'), 'error');
        _audioGenES.close(); _audioGenES = null;
        _audioGenJob = null;
        _resetSpinnerBtns();
        break;
    }
  };
  _audioGenES.onerror = () => {
    // 同 connectSSE 的 P2-3 修复: readyState===CLOSED 永不成立 (EventSource 自动重连)
    // 改为连续失败计数, 超过上限后主动关闭, 恢复按钮.
    _audioGenRetry++;
    if (_audioGenRetry > SSE_MAX_RETRY) {
      _audioGenES.close(); _audioGenES = null;
      _audioGenRetry = 0;
      _audioGenJob = null;
      _resetSpinnerBtns();
    }
  };
}

function _resetSpinnerBtns() {
  const btn = document.getElementById('btn-regenerate-audio');
  if (btn) { btn.disabled = false; btn.innerHTML = '🎙️ 生成音频'; }
}

// ── Init ──
loadScripts();
refreshJobs();

// ── ID-022: 刷新/跳页后恢复上次选中的导演任务 ──
// 方案参考 ID-019 (restoreAudioJobs): 选中任务持久化到 localStorage,
// 管线开关: director.html 无 app.js, 独立实现读写 + DOMContentLoaded 回填
// 与流水线页 index.html 共享 localStorage key, 双向同步同一份状态。
(async function initPipelineToggles() {
  try {
    const f = _getPipelineFlags();
    const tc = document.getElementById('toggle-c');
    const tp = document.getElementById('toggle-p');
    const th = document.getElementById('toggle-h');
    if (tc) tc.checked = f.c;
    if (tp) tp.checked = f.p;
    if (th) th.checked = f.h;
  } catch (e) { /* 无开关 DOM 时忽略 */ }
})();

// init 时重新拉取详情 + 按状态决定是否重连 SSE/轮询, 让执行中状态在
// 刷新/跳转返回后依然可见。
(async function restoreLastDirectorJob() {
  // 流水线跳转场景(URL 带 script_id)由 handlePipelineHandoff 负责, 跳过恢复避免竞态
  if (new URLSearchParams(window.location.search).has('script_id')) return;
  const savedId = localStorage.getItem('director_current_job');
  if (!savedId) return;
  try {
    const job = await api(`/jobs/${savedId}`);
    if (!job) throw new Error('job not found');
    currentJobId = job.id;
    persistCurrentJob(job.id);
    renderJobDetail(job);     // 内部按状态自动 startPolling/不轮询
    await refreshJobs();
    // 活跃任务(规划/执行/合成中)刷新后重连 SSE, 让日志抽屉继续接收事件
    // 注意: 后端合成期间 job.status 仍是 reviewing (compose 端点不写 composing),
    //       因此 reviewing 也需重连, 否则刷新后合成进度日志丢失。
    const active = job.status === 'planning' || job.status === 'executing' ||
                   job.status === 'queued' || job.status === 'reviewing';
    if (active) {
      showExecLog();
      connectSSE(job.id);
      if (job.status !== 'reviewing') {
        toast('已恢复执行中的导演任务，日志流已重连', 'info');
      }
    } else if (job.status === 'completed' && job.output_path) {
      const preview = document.getElementById('video-preview');
      const player = document.getElementById('video-player');
      if (preview && player) {
        player.src = `${API}/jobs/${job.id}/download`;
        preview.classList.remove('hidden');
        document.getElementById('btn-download').disabled = false;
      }
    }
  } catch (e) {
    // 任务已被删除或不存在: 清理持久化, 保持空态
    localStorage.removeItem('director_current_job');
  }
})();

// ── Pipeline handoff ──
(function handlePipelineHandoff() {
  const params = new URLSearchParams(window.location.search);
  const scriptId = params.get('script_id');
  if (!scriptId) return;
  const audioId = params.get('audio_id') || '';
  document.getElementById('input-script-id').value = scriptId;
  document.getElementById('input-audio-id').value = audioId;
  syncScriptTitleInput();
  history.replaceState(null, '', window.location.pathname);
  if (params.get('auto') === '1') {
    // 🚀 一键成片标记存在时启用自动模式: plan_done→executeJob→composeJob 全程无人值守
    if (_onestopOn()) _onestopActive = true;
    toast(_onestopActive ? '🚀 一键成片: 已接收流水线产出，自动创建导演任务…' : '已接收流水线产出，自动创建导演任务…', 'info');
    createJob();
  } else {
    toast('已带入流水线的脚本与音频，点击"创建并规划"继续', 'info');
  }
})();

// ── 错别字音频替换 (2026-08-11) ──────────────────────────────────────────────
// 生僻字(indextts 读错) → 替换字 → 重做含该字段 → 替换 wav → 重合成
function toggleReplaceCharPanel(show) {
  const panel = document.getElementById('replace-char-panel');
  if (panel) panel.style.display = show ? 'block' : 'none';
}

async function replaceCharAudio(btn) {
  if (!currentJobId) { toast('请先选择导演任务', 'error'); return; }
  const fromChar = document.getElementById('replace-from').value.trim();
  const toChar = document.getElementById('replace-to').value.trim();
  if (!fromChar || !toChar) { toast('请输入原字和替换字', 'error'); return; }

  // 取当前 job 的 script_id (从 job 详情或 URL)
  const scriptId = document.getElementById('input-script-id')?.value || '';
  if (!scriptId) { toast('未找到脚本', 'error'); return; }

  btn.disabled = true;
  const statusEl = document.getElementById('replace-status');
  if (statusEl) statusEl.textContent = '执行中…';
  try {
    const resp = await fetch(`/api/audio/scripts/${scriptId}/replace-char`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_char: fromChar, to_char: toChar }),
    });
    const data = await resp.json();
    if (data.ok) {
      if (statusEl) statusEl.textContent = `✅ ${data.msg}`;
      toast(data.msg || '替换完成', 'success');
    } else {
      if (statusEl) statusEl.textContent = `❌ ${data.msg || data.detail || '失败'}`;
      toast(data.msg || data.detail || '替换失败', 'error');
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = `❌ ${e.message}`;
    toast('替换失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}
