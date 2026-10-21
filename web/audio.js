// audio.js (2026-08-15) — 音频加工中心: 段落选择 / TTS / 一键成片 / 音色锁
// 从 app.js v6 迁入全部音频系函数; 依赖 app.js v7 共享层

// ── 段落渲染全家 (从 app.js v6 迁入) ──
function renderSegments(segments) {
  const container = document.getElementById('segments-list');
  if (!container) return;
  container.innerHTML = '';
  const selected = segments.filter(s => s.selected_for_host).sort((a, b) => a.host_order - b.host_order);
  const ordered = [...selected, ...segments.filter(s => !s.selected_for_host).sort((a, b) => a.line_index - b.line_index)];
  ordered.forEach((seg, idx) => {
    const div = document.createElement('div');
    div.className = 'segment ' + (seg.selected_for_host ? 'selected' : 'unselected');
    div.draggable = seg.selected_for_host;
    div.dataset.id = seg.id;
    if (seg.selected_for_host) {
      div.addEventListener('dragstart', handleDragStart);
      div.addEventListener('dragover', handleDragOver);
      div.addEventListener('drop', handleDrop);
      div.addEventListener('dragenter', handleDragEnter);
      div.addEventListener('dragleave', handleDragLeave);
      div.addEventListener('dragend', handleDragEnd);
    }
    const orderLabel = seg.selected_for_host ? String(idx + 1) : '-';
    const typeLabel = formatSegmentType(seg.segment_type);
    const durLabel = seg.estimated_duration ? `${seg.estimated_duration.toFixed(1)}s` : '';
    div.innerHTML = `
      <div class="drag-handle" ${seg.selected_for_host ? 'title="拖拽排序"' : ''}>${seg.selected_for_host ? '☰' : ''}</div>
      <div class="order">${orderLabel}</div>
      <input type="checkbox" ${seg.selected_for_host ? 'checked' : ''} onchange="toggleSegment('${seg.id}', this.checked)">
      <div class="text">${escapeHtml(seg.text)}</div>
      <div class="meta">
        <span class="type-badge">${typeLabel}</span>
        ${durLabel ? `<span class="duration">${durLabel}</span>` : ''}
      </div>
    `;
    container.appendChild(div);
  });
  updateDirectorCount();
}

function formatSegmentType(type) {
  const map = { opening: '开场', hook: '钩子', body: '正文', cta: '行动', ending: '结尾' };
  return map[type] || (type || '正文');
}

let dragSrcEl = null;

function handleDragStart(e) {
  dragSrcEl = this;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', this.dataset.id);
  this.classList.add('dragging');
}

function handleDragOver(e) {
  if (e.preventDefault) e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  return false;
}

function handleDragEnter(e) {
  this.classList.add('dragging');
}

function handleDragLeave(e) {
  this.classList.remove('dragging');
}

function handleDrop(e) {
  e.stopPropagation();
  e.preventDefault();
  if (!dragSrcEl || dragSrcEl === this) return false;
  const srcId = dragSrcEl.dataset.id;
  const dstId = this.dataset.id;
  reorderSegmentsByDrag(srcId, dstId);
  dragSrcEl.classList.remove('dragging');
  return false;
}

function handleDragEnd(e) {
  document.querySelectorAll('.segment').forEach(el => el.classList.remove('dragging'));
  dragSrcEl = null;
}

function reorderSegmentsByDrag(srcId, dstId) {
  const selected = currentScript.segments.filter(s => s.selected_for_host).sort((a, b) => a.host_order - b.host_order);
  const srcIdx = selected.findIndex(s => s.id === srcId);
  const dstIdx = selected.findIndex(s => s.id === dstId);
  if (srcIdx < 0 || dstIdx < 0) return;
  const [moved] = selected.splice(srcIdx, 1);
  selected.splice(dstIdx, 0, moved);
  selected.forEach((s, i) => { s.host_order = i; });
  renderSegments(currentScript.segments);
  setSaveDirectorEnabled(false);
}

function updateDirectorCount() {
  const container = document.getElementById('segments-list');
  if (!container) return;
  const checked = container.querySelectorAll('input[type="checkbox"]:checked').length;
  const totalDuration = Array.from(container.querySelectorAll('.segment.selected')).reduce((sum, el) => {
    const d = currentScript.segments.find(s => s.id === el.dataset.id);
    return sum + (d && d.estimated_duration ? d.estimated_duration : 0);
  }, 0);
  const count = document.getElementById('director-count');
  if (count) count.textContent = `已选 ${checked} 句 / 约 ${totalDuration.toFixed(1)}s`;
}

function selectAll(selected) {
  if (!currentScript) return;
  currentScript.segments.forEach((seg, i) => {
    seg.selected_for_host = selected;
    if (selected) seg.host_order = i;
    else seg.host_order = 0;
  });
  renderSegments(currentScript.segments);
  setSaveDirectorEnabled(false);
}

async function toggleSegment(segmentId, selected) {
  const seg = currentScript.segments.find(s => s.id === segmentId);
  if (!seg) return;
  seg.selected_for_host = selected;
  if (selected) {
    const maxOrder = Math.max(...currentScript.segments.filter(s => s.selected_for_host).map(s => s.host_order), -1);
    seg.host_order = maxOrder + 1;
  } else {
    seg.host_order = 0;
  }
  renderSegments(currentScript.segments);
  setSaveDirectorEnabled(false);
  // 2026-08-14: 立即同步 DB (之前只改本地, 取消勾选不存 → generate-audio 读 DB 仍生成)
  try {
    await api('PUT', `/scripts/segments/${segmentId}`, {
      selected_for_host: seg.selected_for_host,
      host_order: seg.host_order,
    });
  } catch (e) {
    setStatus('status-audio', '勾选保存失败: ' + e.message, true);
  }
}

async function moveSegment(segmentId, direction) {
  const selected = currentScript.segments.filter(s => s.selected_for_host).sort((a, b) => a.host_order - b.host_order);
  const idx = selected.findIndex(s => s.id === segmentId);
  if (idx < 0) return;
  const newIdx = idx + direction;
  if (newIdx < 0 || newIdx >= selected.length) return;
  [selected[idx], selected[newIdx]] = [selected[newIdx], selected[idx]];
  selected.forEach((s, i) => { s.host_order = i; });
  renderSegments(currentScript.segments);
  setSaveDirectorEnabled(false);
}

async function saveDirectorSelections() {
  const segs = currentScript.segments;
  const selected = segs.filter(s => s.selected_for_host).sort((a, b) => a.host_order - b.host_order);
  selected.forEach((s, i) => { s.host_order = i; });
  try {
    const segmentIds = selected.map(s => s.id);
    await api('POST', `/scripts/${currentScript.id}/segments/reorder`, segmentIds);
    for (const seg of segs) {
      await api('PUT', `/scripts/segments/${seg.id}`, {
        selected_for_host: seg.selected_for_host,
      });
    }
    setSaveDirectorEnabled(true);
    setStatus('status-audio', '导演选择已保存', false, true);
  } catch (e) {
    setStatus('status-audio', e.message, true);
  }
}

// ── persona 音色锁 (2026-08-15 拆页: 按脚本模板锁音色, fetchScript 调用) ──
async function applyPersonaLockByTemplate(template) {
  const lockDiv = document.getElementById('persona-lock');
  const voiceSelect = document.getElementById('voice-select');
  if (!lockDiv || !voiceSelect) return;
  try {
    const persona = await api('GET', `/personas/by-template/${encodeURIComponent(template)}`);
    if (persona && persona.voice_id) {
      voiceSelect.value = persona.voice_id;
      voiceSelect.disabled = true;
      const voiceName = persona.voice ? persona.voice.name : persona.voice_id;
      const roleName = persona.role ? persona.role.name : '未绑定';
      lockDiv.innerHTML = `🔒 人物「${persona.name}」已锁定 — 音色: ${voiceName} | 形象: ${roleName}`;
      lockDiv.style.display = 'block';
    } else {
      voiceSelect.disabled = false;
      lockDiv.style.display = 'none';
    }
  } catch (e) {
    voiceSelect.disabled = false;
    lockDiv.style.display = 'none';
  }
}

// ── TTS 生成 (从 app.js v6 迁入) ──
// ── 停止按钮 (2026-09-02): 任务进行中显示; 段间软取消, 已生成段保留断点续传 ──
let _activeAudioJobId = null;

function showAudioStop(jobId) {
  _activeAudioJobId = jobId;
  const btn = document.getElementById('btn-audio-stop');
  if (btn) { btn.style.display = ''; btn.disabled = false; btn.textContent = '⏹ 停止'; }
}

function hideAudioStop() {
  _activeAudioJobId = null;
  const btn = document.getElementById('btn-audio-stop');
  if (btn) btn.style.display = 'none';
}

async function cancelAudio() {
  if (!_activeAudioJobId) return;
  const btn = document.getElementById('btn-audio-stop');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 停止中…'; }
  try {
    const job = await api('POST', `/audio/jobs/${_activeAudioJobId}/cancel`);
    // 二次点击仍 cancelling (线程已死的僵尸) → 后端已强制落 cancelled, SSE 会推 tts_cancelled
    if (job && job.status === 'cancelling') {
      setStatus('status-audio', '已请求停止 — 当前批次完成后中断');
    }
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '⏹ 停止'; }
    setStatus('status-audio', '停止失败: ' + e.message, true);
  }
}

async function generateAudio() {
  if (!currentScript) return;
  toggle('btn-audio', false);
  toggle('btn-onestop', false);
  setStatus('status-audio', '生成音频中...');
  try {
    const voiceId = document.getElementById('voice-select').value;
    const query = voiceId ? `?voice_id=${encodeURIComponent(voiceId)}&selected_only=true` : '?selected_only=true';
    const job = await api('POST', `/audio/scripts/${currentScript.id}/generate-audio${query}`);
    setStatus('status-audio', `任务已创建: ${job.id}`);
    showAudioStop(job.id);
    const container = document.getElementById('audio-list');
    container.innerHTML = '';
    connectAudioSSE(job.id);
  } catch (e) {
    setStatus('status-audio', e.message, true);
    toggle('btn-audio', true);
    toggle('btn-onestop', true);
  }
}

// ── 🚀 一键成片 (2026-08-09) ──
let _onestopAudioSSE = null;
async function oneStop() {
  if (!currentScript) return;
  const btn = document.getElementById('btn-onestop');
  btn.disabled = true;
  btn.textContent = '⏳ 一键成片中...';
  toggle('btn-audio', false);
  try {
    const voiceId = document.getElementById('voice-select').value;
    const query = voiceId ? `?voice_id=${encodeURIComponent(voiceId)}&selected_only=true` : '?selected_only=true';
    const job = await api('POST', `/audio/scripts/${currentScript.id}/generate-audio${query}`);
    setStatus('status-audio', `一键成片: 音频任务已提交 (${job.id})`);
    showAudioStop(job.id);
    const container = document.getElementById('audio-list');
    container.innerHTML = '';
    if (_onestopAudioSSE) { _onestopAudioSSE.close(); _onestopAudioSSE = null; }
    _onestopAudioSSE = new EventSource(`${API}/jobs/${job.id}/events`);
    _onestopAudioSSE.onmessage = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      if (data.type === 'tts_service') {
        setStatus('status-audio', data.message);
      } else if (data.type === 'tts_progress') {
        setStatus('status-audio', `一键成片: 音频进度 ${data.completed}/${data.total}`);
        if (data.audio_file) appendAudioItem(data.audio_file);
      } else if (data.type === 'tts_cancelling') {
        setStatus('status-audio', data.message || '已请求停止 — 当前批次完成后中断');
      } else if (data.type === 'tts_cancelled') {
        if (_onestopAudioSSE) { _onestopAudioSSE.close(); _onestopAudioSSE = null; }
        const btnOnestop = document.getElementById('btn-onestop');
        if (btnOnestop) { btnOnestop.disabled = false; btnOnestop.textContent = '🚀 一键成片'; }
        toggle('btn-audio', true);
        hideAudioStop();
        setStatus('status-audio', '已停止 — 一键成片中止，已生成音频保留', false, true);
        loadAudioFiles(job.id);
        try { localStorage.removeItem('dh_onestop'); } catch (_) {}
      } else if (data.type === 'tts_done') {
        if (_onestopAudioSSE) { _onestopAudioSSE.close(); _onestopAudioSSE = null; }
        const btnOnestop = document.getElementById('btn-onestop');
        if (btnOnestop) btnOnestop.textContent = '🚀 一键成片';
        hideAudioStop();
        if (data.stale) {
          // 2026-09-07: 合成期间文稿被改 → 停在音频页警示, 不带着旧音频自动进导演台
          setStatus('status-audio', data.notice || '⚠ 音频基于旧稿已过期, 请重新点「生成音频」', true);
          toggle('btn-audio', true);
          try { localStorage.removeItem('dh_onestop'); } catch (_) {}
          return;
        }
        setStatus('status-audio', '音频就绪 — 即将进入导演台自动跑完剩余步骤', false, true);
        if (data.combined_audio) prependCombinedAudio(data.combined_audio);
        loadAudioFiles(job.id);
        try { localStorage.setItem('dh_onestop', '1'); } catch (_) {}
        if (window._IS_BS1_BOOK) {  // 0908: 老谭线不走导演台 → 回 bs1 工坊生成视频
          setStatus('status-audio', '音频就绪 — 请试听校验, 然后回「bs1 页单工坊」点生成视频 (TTS 自动复用本页音频)', false, true);
          try { localStorage.removeItem('dh_onestop'); } catch (_) {}
          const ctx = _pptBookCtx();
          const div = document.createElement('div');
          div.style.cssText = 'margin:0.6rem 0;padding:0.55rem 0.8rem;border-radius:8px;background:rgba(201,162,94,.12);border:1px solid #C9A25E;font-size:0.82rem';
          div.innerHTML = `📐 音频就绪 (bs1 页单稿) — <a href="/web/bs1_story.html?book_id=${encodeURIComponent(ctx.book_id)}&ep_index=${ctx.ep_index || 1}" style="color:#C9A25E;font-weight:600">回页单工坊 → 生成视频</a>`;
          const anchor = document.querySelector('.container') || document.body;
          anchor.prepend(div);
          return;
        }
        window.location.href = `/web/director.html?script_id=${encodeURIComponent(currentScript.id)}`
          + (data.combined_audio ? `&audio_id=${encodeURIComponent(data.combined_audio.id)}` : '')
          + '&auto=1';
      } else if (data.type === 'tts_error') {
        if (_onestopAudioSSE) { _onestopAudioSSE.close(); _onestopAudioSSE = null; }
        const btnOnestop = document.getElementById('btn-onestop');
        if (btnOnestop) { btnOnestop.disabled = false; btnOnestop.textContent = '🚀 一键成片'; }
        toggle('btn-audio', true);
        hideAudioStop();
        setStatus('status-audio', data.error, true);
        try { localStorage.removeItem('dh_onestop'); } catch (_) {}
      }
    };
    _onestopAudioSSE.onerror = () => {
      if (_onestopAudioSSE && _onestopAudioSSE.readyState === EventSource.CLOSED) {
        _onestopAudioSSE.close(); _onestopAudioSSE = null;
        const btnOnestop = document.getElementById('btn-onestop');
        if (btnOnestop) { btnOnestop.disabled = false; btnOnestop.textContent = '🚀 一键成片'; }
        toggle('btn-audio', true);
        hideAudioStop();
        setStatus('status-audio', 'SSE 连接错误', true);
      }
    };
  } catch (e) {
    const btnOnestop = document.getElementById('btn-onestop');
    if (btnOnestop) { btnOnestop.disabled = false; btnOnestop.textContent = '🚀 一键成片'; }
    toggle('btn-audio', true);
    hideAudioStop();
    setStatus('status-audio', e.message, true);
  }
}

// ── ID-019: 公共音频 SSE 处理 ──
let _audioSSE = null;
let currentAudioJobId = null;
function connectAudioSSE(jobId) {
  if (_audioSSE) { _audioSSE.close(); _audioSSE = null; }
  _audioSSE = new EventSource(`${API}/jobs/${jobId}/events`);
  _audioSSE.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch (_) { return; }  // P2-4: 坏数据不崩
    if (data.type === 'tts_service') {
      setStatus('status-audio', data.message);
    } else if (data.type === 'tts_cancelling') {
      setStatus('status-audio', data.message || '已请求停止 — 当前批次完成后中断');
    } else if (data.type === 'tts_progress') {
      setStatus('status-audio', `进度 ${data.completed}/${data.total}`);
      if (data.audio_file) {
        appendAudioItem(data.audio_file);
      }
    } else if (data.type === 'tts_cancelled') {
      _audioSSE.close(); _audioSSE = null;
      setStatus('status-audio', '已停止 — 已生成音频保留，重新生成将从断点继续', false, true);
      hideAudioStop();
      toggle('btn-audio', true);
      toggle('btn-onestop', true);
      loadAudioFiles(jobId);
    } else if (data.type === 'tts_done') {
      _audioSSE.close(); _audioSSE = null;
      hideAudioStop();
      toggle('btn-audio', true);
      if (data.stale) {
        // 2026-09-07: 合成期间文稿被改, 产物是旧稿 → 红色警示, 不引导进入导演台
        setStatus('status-audio', data.notice || '⚠ 音频基于旧稿已过期, 请重新点「生成音频」', true);
      } else {
        setStatus('status-audio', '音频生成完成', false, true);
        // 衔接下一步: 带脚本+合并音频跳到视觉导演页自动建任务
        showDirectorHandoff(currentScript.id, data.combined_audio ? data.combined_audio.id : '');
      }
      if (data.combined_audio) {
        prependCombinedAudio(data.combined_audio);
      }
      loadAudioFiles(jobId);
    } else if (data.type === 'tts_error') {
      _audioSSE.close(); _audioSSE = null;
      setStatus('status-audio', data.error, true);
      hideAudioStop();
      toggle('btn-audio', true);
      toggle('btn-onestop', true);
    }
  };
  _audioSSE.onerror = () => {
    if (_audioSSE && _audioSSE.readyState === EventSource.CLOSED) {
      _audioSSE.close(); _audioSSE = null;
      setStatus('status-audio', 'SSE 连接错误', true);
      hideAudioStop();
      toggle('btn-audio', true);
      toggle('btn-onestop', true);
    }
  };
}

// ── ID-019: 刷新/跳页返回后恢复音频任务状态 ──
async function restoreAudioJobs() {
  if (!currentScript) return;
  const container = document.getElementById('audio-list');
  if (!container) return;
  const jobs = await api('GET', `/audio/jobs?script_id=${encodeURIComponent(currentScript.id)}&limit=20`)
    .catch(() => []);
  if (!jobs || !jobs.length) return;

  const active = jobs.find(j => j.status === 'pending' || j.status === 'running' || j.status === 'cancelling');
  if (active) {
    // 任务进行中: 禁生成 (防并发二任务) + 显示停止按钮 (2026-09-02)
    toggle('btn-audio', false);
    toggle('btn-onestop', false);
    setStatus('status-audio', active.status === 'cancelling'
      ? `任务停止中 (${active.id.slice(0, 8)}…) — 当前批次完成后中断，再点一次停止可强制复位`
      : `任务进行中 (${active.id.slice(0, 8)}…) 已恢复订阅`);
    showAudioStop(active.id);
    connectAudioSSE(active.id);
    return;
  }

  // P0-1 方案 1: 最新 failed/cancelled 任务提示可续传 — 已生成段保留在盘, 重试自动跳过
  const latestInterrupted = jobs.find(j => j.status === 'failed' || j.status === 'cancelled');
  if (latestInterrupted) {
    const done = latestInterrupted.completed_segments || 0;
    const total = latestInterrupted.total_segments || 0;
    if (done > 0 && done < total) {
      setStatus('status-audio', `上次生成中断于 ${done}/${total} 段 — 重新生成将从断点继续`, true);
    } else if (latestInterrupted.status === 'failed') {
      setStatus('status-audio', '上次生成失败 — 重新生成将从头开始', true);
    }
  }

  const completed = jobs.filter(j => j.status === 'completed').slice(0, 3);
  container.innerHTML = '';
  // 0912 修复: 只渲染最新一个 completed — 旧版循环渲染多个, 但 loadAudioFiles 每次
  // 清空容器, 实际只留最旧那个; 换音色/情绪参考重跑后页面放的是旧音频 (ep1 实锤)
  if (completed[0]) {
    await loadAudioFiles(completed[0].id);
  }
}

// 0912 单行重roll (页面闭环: 人耳发现问题→重生成→自动回听→报告入库)
const _fileVer = {};  // fileId → 版本戳 (reroll 后换 URL 强制浏览器拉新, 免手动刷新)

// 1001 修: Win 锁坑统一前置 (0921 tempo 教义泛化到全部重合成入口) — 暂停中的
// <audio> 让服务端 FileResponse 持读句柄, 服务端换名重试也赢不了 (055 实锤);
// pause+摘src+load 触发 abort, 连接即断
function _releasePlayers() {
  document.querySelectorAll('#audio-list audio').forEach(a => {
    try { a.pause(); a.removeAttribute('src'); a.load(); } catch (_e) {}
  });
}
function appendAudioItem(f) {
  const container = document.getElementById('audio-list');
  const div = document.createElement('div');
  div.className = 'audio-item';
  // 0912 降噪 (用户令): 行标签只留行号 (去 .wav/时长), 去下载按钮 — 无效信息清除
  const label = f.filename.replace(/\.wav$/, '');
  // 0912 逐字稿接线: 音频行带文本参照; 回听未解决行标红 (人耳复核优先听这些)
  const flag = f.verify_flag === 'unresolved'
    ? '<span style="color:#f87171;font-weight:600"> ⚠回听未过</span>'
    : (f.verify_flag === 'slow'
      ? '<span style="color:#fb923c;font-weight:600"> ⚠语速&lt;5.3</span>'
      : (f.verify_flag === 'anchor_off'
        ? '<span style="color:#fb923c;font-weight:600"> ⚠偏离锚</span>'
        : (f.verify_flag === 'suspect' ? '<span style="color:#fbbf24"> ⚠嫌疑</span>' : '')));
  const txt = f.text ? `<div style="flex:1;min-width:220px;font-size:.85rem;color:inherit;opacity:.9">${escapeHtml(f.text)}</div>` : '';
  // 0912 单行重roll (页面闭环: 人耳发现问题→重生成→自动回听→报告入库)
  const rerollBtn = f.segment_id
    ? `<button class="sm" style="padding:2px 10px;font-size:.78rem" onclick="rerollLine('${f.id}', this)">↻重roll</button>` +
      `<button class="sm secondary" style="padding:2px 8px;font-size:.78rem" onclick="tempoLine('${f.id}', this, 'faster')" title="技术变速 atempo (变速不变调): 每点快~5%, 从原始一次成型零损耗 — TTS2 引擎无语速参数, 这是唯一真生效的加速">⏩加速</button>` +
      `<button class="sm secondary" style="padding:2px 8px;font-size:.78rem" onclick="tempoLine('${f.id}', this, 'slower')" title="减速一档 (x0.95), 回到1.0x自动还原原始">↩减速</button>` +
      `<button class="sm secondary" style="padding:2px 8px;font-size:.78rem" onclick="tempoLine('${f.id}', this, 'reset')" title="还原原始 take, 清除累计倍率">⟲还原</button>` +
      `<button class="sm secondary" style="padding:2px 10px;font-size:.78rem;background:#5b21b6" onclick="openSegmentEditor('${f.id}', this)" title="统一编辑: 改字 + 停顿(气口) + 注音 — 一个文本框全搞定">✎编辑</button>` +
      `<button class="sm secondary" style="padding:2px 8px;font-size:.78rem" onclick="anchorLine('${f.id}', this)" title="登记语速基准: 实测本行含气口字/s (含停顿的整体节奏), 按区位(钩子/过渡/巡航)写入音色 — 之后重roll/改稿重生成的同区行自动校正回锚 (atempo 变速不变调零损耗)">📌基准</button>` +
      `<button class="sm warn" style="padding:2px 10px;font-size:.78rem" onclick="muteLine('${f.id}', this)" title="本单移出播放+大段, 并反勾选文稿段 (重生成不再出镜); 恢复=文稿区重新勾选">🔇屏蔽</button>`
    : '';
  const v = _fileVer[f.id] ? `?v=${_fileVer[f.id]}` : '';
  div.innerHTML = `
    <div style="min-width:36px">${label}${flag}</div>
    ${txt}
    <audio controls src="${API}/audio/files/${f.id}/download${v}"></audio>
    ${rerollBtn}
  `;
  container.appendChild(div);
}

// 0912 完整段落音频重拼: 段级 reroll/修复后从当前段重建 full_paragraph
async function rebuildCombined(jobId, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/jobs/${jobId}/rebuild-combined`);
    toast(`重拼 ✓ ${r.segments} 段 / ${r.duration}s`, 'success');
    // 大段文件已换 → 版本戳换 URL, 重渲染立即可听
    const combinedFile = (await api('GET', `/audio/jobs/${jobId}/files`)).find(x => !x.segment_id);
    if (combinedFile) _fileVer[combinedFile.id] = Date.now();
    await loadAudioFiles(jobId);
  } catch (e) {
    toast('重拼失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄重拼'; }
  }
}


// ── 统一段编辑器 (0914 用户令: 改字+注音+停顿合一个文本框) ──────────
let _editorCtx = null;  // {fileId, btn, textarea, pinyinInput}
function openSegmentEditor(fileId, btn) {
  const row = btn.closest('.audio-item');
  const curText = row ? (row.querySelector('div:nth-child(2)')?.textContent || '') : '';
  _editorCtx = { fileId, btn };
  // 剥已有停顿标记后显示 (改完统一加回去, 看见 -0.5s- 干扰编辑)
  const showText = curText.replace(/-\d+(?:\.\d+)?s-\s*$/gm, '').trimEnd();

  // 复用或创建 modal
  let ov = document.getElementById('seg-editor');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'seg-editor';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9999;display:flex;align-items:center;justify-content:center';
    document.body.appendChild(ov);
  }
  ov.innerHTML = `
    <div style="background:#1e293b;border:1px solid #475569;border-radius:12px;padding:1.2rem 1.4rem;width:min(560px,92vw);max-height:80vh;overflow-y:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.6rem">
        <b style="font-size:.95rem;color:#e2e8f0">✎ 段编辑 <span style="color:#64748b;font-size:.78rem">改字 · 注音 · 停顿</span></b>
        <button class="sm" style="background:#475569" onclick="closeSegEditor()">✕</button>
      </div>
      <textarea id="seg-edit-text" rows="5"
        style="width:100%;box-sizing:border-box;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:8px;padding:.6rem;font-size:.88rem;line-height:1.6;resize:vertical"
        placeholder="直接改字; 任意位置插 -0.5s- 或 -1s- 垫停顿 (光标处, 停顿垫在标记位置)">${showText}</textarea>
      <div style="display:flex;gap:.4rem;margin-top:.5rem;flex-wrap:wrap">
        <span style="color:#64748b;font-size:.78rem;align-self:center">气口快插:</span>
        <button class="sm secondary" style="font-size:.75rem;padding:2px 8px" onclick="insertAtCursor('-0.5s-')">↩ -0.5s-</button>
        <button class="sm secondary" style="font-size:.75rem;padding:2px 8px" onclick="insertAtCursor('-1s-')">↩ -1s-</button>
        <span style="color:#475569;font-size:.72rem;align-self:center">问句自动 1.2s · 大段落末自动 1s · -Xs- 光标处生效 (不用手动)</span>
      </div>
      <input id="seg-edit-pinyin" type="text"
        style="width:100%;box-sizing:border-box;margin-top:.5rem;background:#0f172a;color:#fbbf24;border:1px solid #334155;border-radius:8px;padding:.45rem .6rem;font-size:.82rem"
        placeholder="注音 (可选): 字=拼音 空格分隔, 如 恰=QIA4 份=FEN4 — 系统性读错时用" />
      <div style="display:flex;justify-content:space-between;margin-top:.8rem">
        <span style="color:#64748b;font-size:.75rem">改字→三处同步(段/稿/书) · 停顿→合成后垫静音 · 注音→字级拼音标注</span>
        <button class="sm" style="background:#5b21b6;padding:4px 18px" id="seg-edit-go" onclick="submitSegEditor(this)">重合成</button>
      </div>
    </div>`;
  ov.style.display = 'flex';
  const ta = document.getElementById('seg-edit-text');
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

function closeSegEditor() {
  const ov = document.getElementById('seg-editor');
  if (ov) ov.style.display = 'none';
  _editorCtx = null;
}

function insertAtCursor(marker) {
  const ta = document.getElementById('seg-edit-text');
  if (!ta) return;
  const pos = ta.selectionStart;
  ta.value = ta.value.slice(0, pos) + marker + ta.value.slice(ta.selectionEnd);
  ta.focus();
  ta.setSelectionRange(pos + marker.length, pos + marker.length);
}

async function submitSegEditor(goBtn) {
  if (!_editorCtx) return;
  const { fileId, btn } = _editorCtx;
  const ta = document.getElementById('seg-edit-text');
  const py = document.getElementById('seg-edit-pinyin');
  const newText = (ta?.value || '').trim();
  const pinyinRaw = (py?.value || '').trim();

  // 注音解析: "恰=QIA4 份=FEN4" → {恰:"QIA4", 份:"FEN4"}
  // 1022 修: 大小写宽容 (xing2 也收, 统一转大写); 解析失败改 confirm 模态阻断 —
  // 旧版 4s toast 一闪即逝, 错过后用户以为注音已生效 (036 我多行=XING2 实锤)
  const annotate = {};
  if (pinyinRaw) {
    for (const m of pinyinRaw.matchAll(/([一-鿿])=([A-Za-z]+\d)/g)) {
      annotate[m[1]] = m[2].toUpperCase();
    }
    if (!Object.keys(annotate).length) {
      const go = confirm(`注音「${pinyinRaw}」没认出来。\n格式: 字=拼音, 空格分隔 (如 恰=QIA4, 拼音+声调数字, 大小写均可)\n\n确定 = 忽略注音, 只按改字重合成\n取消 = 回去改格式`);
      if (!go) return;
    }
  }
  // 1022 防呆: 正文里混入「字=拼音」写法 — 正文中的 = 会被 TTS 念成"等于",
  // 注音语义彻底失效 (pinyin_fix._eq_to_cn 行为), 提示挪到注音框
  if (/([一-鿿])\s*=\s*[A-Za-z]+\d/.test(newText)) {
    const go = confirm('正文里检测到「字=拼音」写法。\n正文的 = 会被念成"等于", 拼音注音请填下方黄色注音框。\n\n确定 = 照正文原样合成\n取消 = 回去改');
    if (!go) return;
  }
  if (!newText) { toast('文本不能为空', 'error'); return; }
  _releasePlayers();  // Win 锁前置 (055 实锤, 见 helper 注释)
  goBtn.disabled = true; goBtn.textContent = '⏳';
  try {
    const payload = { text: newText };
    if (Object.keys(annotate).length) payload.annotate = annotate;
    const r = await api('POST', `/audio/files/${fileId}/reroll`, payload);
    const v = r.verdict || {};
    const notes = [];
    if (r.duration) notes.push(`${r.duration}s`);
    const diff = [...(v.diff || [])];
    if (diff.length) notes.push(`疑点:${diff.join('/')}`);
    if (newText.includes('-')) {
      const pauses = (newText.match(/-(\d+(?:\.\d+)?)s-/g) || []).length;
      if (pauses) notes.push(`气口×${pauses}`);
    }
    if (Object.keys(annotate).length) notes.push(`注音×${Object.keys(annotate).length}`);
    toast(`${r.file || '段'} 编辑重合成 ✓ ${notes.join(' | ') || '完成'}`, 'success');
    closeSegEditor();
    await bustVersions(fileId);
    await loadAudioFiles(currentAudioJobId || '');
  } catch (e) {
    toast('编辑重合成失败: ' + e.message, 'error');
    goBtn.disabled = false; goBtn.textContent = '重合成';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✎编辑'; }
  }
}

// 0912 注音重roll: 系统性读错 (重roll无效) → 字+拼音标注重合成; 词级恒定读法自动沉淀词表
// 格式宽容: "恰 QIA4 饭 FAN4" / "恰QIA4 饭FAN4" / "恰QIA4饭FAN4" / 逗号顿号均可
async function rerollAnnotated(fileId, btn) {
  const input = prompt('注音重roll — 输入 字+拼音 (拼音大写+声调数字, 可连写可空格)\n例: 恰QIA4 饭FAN4', '');
  if (!input) return;
  const annotate = {};
  const re = /([^A-Z\s,，、;；])[\s,，、;；]*([A-Z]+[1-5])/g;
  let m;
  while ((m = re.exec(input)) !== null) {
    annotate[m[1]] = m[2];
  }
  if (!Object.keys(annotate).length) { toast('格式: 字+拼音 (如 恰QIA4 饭FAN4)', 'error'); return; }
  _releasePlayers();
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/files/${fileId}/reroll`, { annotate });
    const v = r.verdict || {};
    const candNote = (r.candidates_added?.length) ? ` | 候选沉淀「${r.candidates_added.join('/')}」待LLM审核` : '';
    toast(`${r.file} 注音重roll ✓ ${r.duration}s${candNote} | 剩余疑点: ${[...(v.diff || [])].join('/') || '无'}`, 'success');
    await bustVersions(fileId);
    await loadAudioFiles(currentAudioJobId || '');
  } catch (e) {
    toast('注音重roll失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✎注音'; }
  }
}

// 0912 行操作后: 单行 + 大段一起换版本戳 — 后端 reroll/mute 已重拼 full_paragraph,
// 前端不 bust 大段就会吃缓存 (需手动刷新的 bug 根源)
async function bustVersions(fileId) {
  _fileVer[fileId] = Date.now();
  try {
    const files = await api('GET', `/audio/jobs/${currentAudioJobId}/files`);
    const c = files.find(x => !x.segment_id);
    if (c) _fileVer[c.id] = Date.now();
  } catch (_) {}
}

// 0912 行屏蔽: 听到就想删的行 — 本单移出 + 文稿段反勾选 (重生成不再出镜)
async function muteLine(fileId, btn) {
  const row = btn.closest('.audio-item');
  const txt = row ? (row.querySelector('div:nth-child(2)')?.textContent || '') : '';
  if (!confirm(`屏蔽本行? (不再出镜)\n「${txt.slice(0, 40)}」\n\n本单移出播放+大段; 文稿段同步反勾选, 未来重生成也跳过。恢复=文稿区重新勾选该段。`)) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/files/${fileId}/mute`);
    toast(`🔇 ${r.file} 已屏蔽`, 'success');
    await bustVersions(fileId);  // 大段已重拼 — 一起换版本戳
    await loadAudioFiles(currentAudioJobId || '');
  } catch (e) {
    toast('屏蔽失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔇屏蔽'; }
  }
}

// 0912 改字重roll: 断句/语义歧义类病 (模型把词组解析错逐字拖读) — 改字消歧
// 改完 Segment/Script/书稿三处同步, 字幕文稿一致
async function rerollEditText(fileId, btn) {
  const row = btn.closest('.audio-item');
  const curText = row ? (row.querySelector('div:nth-child(2)')?.textContent || '') : '';
  const input = prompt('改字消歧重roll — 修改本行文字 (微调语序/加消歧字, 别大改内容)\n例: 核辐射死人 → 核辐射害死人', curText);
  if (!input || input === curText) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/files/${fileId}/reroll`, { text: input.trim() });
    const v = r.verdict || {};
    toast(`${r.file} 改字重roll ✓ ${r.duration}s | 疑点: ${[...(v.diff || [])].join('/') || '无'}`, 'success');
    await bustVersions(fileId);
    await loadAudioFiles(currentAudioJobId || '');
  } catch (e) {
    toast('改字重roll失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✏改字'; }
  }
}

// 0929 语速基准登记: 实测本行含气口字/s → 按区位写入音色锚 (voice 级, 换书继承)
async function anchorLine(fileId, btn) {
  if (!confirm('📌 把本行登记为语速基准? (实测含气口字/s, 按区位写入音色 — 重roll/改稿重生成的同区行将自动校正回锚)')) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/files/${fileId}/anchor`);
    const zoneName = { hook: '钩子区', transition: '过渡区', cruise: '巡航区' }[r.zone] || r.zone;
    toast(`锚已存 ✓ ${zoneName} ${r.rate}字/s ← 行${String(r.line).padStart(3, '0')} (音色「${r.voice}」全局继承)`, 'success');
  } catch (e) {
    toast('锚登记失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '📌基准'; }
  }
}

async function rerollLine(fileId, btn, speedFactor) {  if (!confirm(speedFactor === 'accel'
    ? '⏩加速重合成本行? (累加档: 在当前语速上再快~10%, 逐次叠加, 下限0.5)'
    : speedFactor
    ? `按语速系数 ${speedFactor} 重合成本行?`
    : '重roll 本行? (采样重生成 → 自动回听复检; 语气词/插字/吞字类大概率消失)')) return;
  _releasePlayers();
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/files/${fileId}/reroll`,
                        speedFactor === 'accel' ? { accel: true }
                        : speedFactor ? { duration_factor: speedFactor } : undefined);
    const v = r.verdict || {};
    const issues = [...(v.filler || []), ...(v.insert || []), ...(v.diff || [])];
    const dfNote = r.df !== undefined ? ` df=${r.df}` : '';
    const slowNote = v.slow !== undefined && v.slow !== null ? ` ⚠语速${v.slow}<5.3可再⏩` : '';
    // 0929 锚定协调回读: ok=带内 / done=已atempo拉回 / far=超帽建议重roll
    const a = v.anchor || {};
    const anchorNote = a.flag === 'done' ? ` 🔗已校正${a.dev || ''}→${a.anchor}字/s`
      : a.flag === 'ok' ? ` 🔗${a.rate}≈锚${a.anchor}字/s`
      : a.flag === 'far' ? ` ⚠偏离锚${a.dev || ''} (>15%), 建议重roll` : '';
    toast(r.ok && !v.diff?.length
      ? `${r.file} 重roll ✓ ${r.duration}s${dfNote}${slowNote || ' 回听干净'}${anchorNote}`
      : `${r.file} 重roll完成${dfNote}${slowNote}${anchorNote}, 回听仍有疑点: ${issues.join('/') || '见报告'} — 可再roll或人工听`, r.ok ? 'success' : 'info');
    _fileVer[fileId] = Date.now();  // 换 URL → 重渲染后新音频立即可听 (免手动刷新)
    await loadAudioFiles(currentAudioJobId || '');
  } catch (e) {
    toast('重roll失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻重roll'; }
  }
}


async function tempoLine(fileId, btn, op) {
  // 0921 技术变速 (atempo): 即时+可回退的旋钮 — 无 confirm, 点-听-点收敛
  // Win 锁坑: 播放器流式占用 wav 时服务端换文件被拒 — 先释放全部 <audio>
  // 连接 (pause+摘src+load 触发 abort), 重渲染后自然换缓存干净的 src
  document.querySelectorAll('#audio-list audio').forEach(a => {
    try { a.pause(); a.removeAttribute('src'); a.load(); } catch (_e) {}
  });
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await api('POST', `/audio/files/${fileId}/tempo`, { op });
    const cps = r.cps ? ` ${r.cps}字/s` : '';
    const cap = r.capped ? ' — 已到1.5x上限, 建议改稿/换anchor' : '';
    toast(op === 'reset' || r.factor <= 1.0
      ? `${r.file} ⟲已还原原始 ${r.duration}s${cps}`
      : `${r.file} ${r.factor.toFixed(2)}x → ${r.duration}s${cps}${cap}`, 'success');
    _fileVer[fileId] = Date.now();  // 换 URL → 新音频立即可听
    await loadAudioFiles(currentAudioJobId || '');
  } catch (e) {
    toast('变速失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false;
      btn.textContent = op === 'faster' ? '⏩加速' : op === 'slower' ? '↩减速' : '⟲还原'; }
  }
}

function prependCombinedAudio(f) {
  const container = document.getElementById('audio-list');
  const prev = container.querySelector('.audio-item.combined');
  if (prev) prev.remove();
  const div = document.createElement('div');
  div.className = 'audio-item combined';
  div.style.borderLeft = '3px solid var(--accent)';
  div.style.background = 'rgba(59,130,246,0.06)';
  const label = '🎙️ 完整段落音频' + (f.duration ? ` (${f.duration.toFixed(2)}s)` : '');
  const jobBtn = (typeof currentAudioJobId === 'string' && currentAudioJobId)
    ? `<button class="sm" style="padding:2px 10px;font-size:.78rem" onclick="rebuildCombined('${currentAudioJobId}', this)">🔄重拼</button>` : '';
  const v = _fileVer[f.id] ? `?v=${_fileVer[f.id]}` : '';
  div.innerHTML = `
    <div style="min-width:160px;font-weight:600;color:var(--accent);">${label}</div>
    <audio controls src="${API}/audio/files/${f.id}/download${v}"></audio>
    ${jobBtn}
    <a href="${API}/audio/files/${f.id}/download${v}" download>下载</a>
  `;
  container.insertBefore(div, container.firstChild);
}

async function loadAudioFiles(jobId) {
  currentAudioJobId = jobId;  // 0912: 单行重roll 后刷新用
  const files = await api('GET', `/audio/jobs/${jobId}/files`);
  const container = document.getElementById('audio-list');
  container.innerHTML = '';
  const combined = files.find(f => !f.segment_id);
  if (combined) {
    prependCombinedAudio(combined);
  }
  files.filter(f => f.segment_id).forEach(f => appendAudioItem(f));
}

// ── 音频加工 → 视觉导演衔接: 倒计时跳转, 携带 script/audio ID ──
let directorHandoffTimer = null;

function showDirectorHandoff(scriptId, audioId) {
  const container = document.getElementById('audio-list');
  if (!container) return;
  if (window._IS_BS1_BOOK) return;  // 0908: 老谭线不走导演台 (bs1 工坊出片)
  const prev = document.getElementById('director-handoff');
  if (prev) prev.remove();

  const url = `/web/director.html?script_id=${encodeURIComponent(scriptId)}`
    + (audioId ? `&audio_id=${encodeURIComponent(audioId)}` : '')
    + '&auto=1';

  const div = document.createElement('div');
  div.id = 'director-handoff';
  div.className = 'audio-item';
  div.style.borderLeft = '3px solid #8b5cf6';
  div.style.background = 'rgba(139,92,246,0.08)';
  div.style.marginTop = '0.75rem';
  div.innerHTML = `
    <div style="flex:1;font-weight:600;color:#a78bfa;">
      🎬 音频就绪 — <span id="handoff-count">8</span>s 后进入视觉导演自动创建任务
    </div>
    <button class="btn btn-primary btn-sm" type="button" id="handoff-go">立即进入</button>
    <button class="btn btn-secondary btn-sm" type="button" id="handoff-stay">留在本页</button>
  `;
  container.parentNode.insertBefore(div, container);

  const go = () => {
    if (directorHandoffTimer) { clearInterval(directorHandoffTimer); directorHandoffTimer = null; }
    window.location.href = url;
  };
  document.getElementById('handoff-go').onclick = go;
  document.getElementById('handoff-stay').onclick = () => {
    if (directorHandoffTimer) { clearInterval(directorHandoffTimer); directorHandoffTimer = null; }
    div.querySelector('div').innerHTML = '🎬 音频就绪 — 可随时进入视觉导演继续下一步';
    document.getElementById('handoff-stay').remove();
  };

  let remain = 8;
  directorHandoffTimer = setInterval(() => {
    remain -= 1;
    const el = document.getElementById('handoff-count');
    if (!el) { clearInterval(directorHandoffTimer); directorHandoffTimer = null; return; }
    el.textContent = remain;
    if (remain <= 0) go();
  }, 1000);
}

// ── 本页脚本下拉回调 (与文字加工页 selectExistingScript 区分命名) ──
async function selectScriptForAudio(select) {
  const scriptId = select.value;
  if (!scriptId) return;
  try {
    await fetchScript(scriptId);
    toggle('btn-audio', true);
    toggle('btn-onestop', true);
    setStatus('status-audio', '已调取脚本 — 选段落和音色后生成', false, true);
  } catch (e) {
    setStatus('status-audio', '调取脚本失败: ' + e.message, true);
  }
}

// ── 初始化: URL 参数 script_id (从文字加工中心/拆书讲书页跳入) ──
document.addEventListener('DOMContentLoaded', async () => {
  const scriptId = new URLSearchParams(location.search).get('script_id');
  if (scriptId) {
    try {
      await fetchScript(scriptId);
      toggle('btn-audio', true);
      toggle('btn-onestop', true);
      setStatus('status-audio', '脚本已加载 — 选段落和音色后生成', false, true);
      // 2026-08-20: 同步下拉选中, 与拆书讲书页进产线跳转联动
      try {
        await loadScriptList();
        const sel = document.getElementById('script-select');
        if (sel && [...sel.options].some(o => o.value === scriptId)) sel.value = scriptId;
      } catch (_) { /* 下拉不同步不阻塞 */ }
      // 0919 陈稿警示 (实锤: 页面回听旧稿音频, 产线已换新稿重合成 — 3.98s 死静音
      // 漏进动画线两整天才现形): URL script_id ≠ 该集当前稿 → 顶部红条 + 直达当前稿
      try {
        const ctx0 = _pptBookCtx();
        if (ctx0.book_id && ctx0.ep_index) {
          const bk = await (await fetch('/books/' + encodeURIComponent(ctx0.book_id))).json();
          const cur = (bk.episodes || []).find(e => String(e.ep) === String(ctx0.ep_index));
          if (cur && cur.script_id && cur.script_id !== scriptId) {
            const bar = document.createElement('div');
            bar.setAttribute('role', 'alert');
            bar.style.cssText = 'background:#7f1d1d;color:#fecaca;padding:8px 14px;'
              + 'font-size:13px;font-weight:600;letter-spacing:.3px';
            bar.innerHTML = '⚠ 此稿已被新稿取代 — 正在听的是旧稿音频 ('
              + scriptId.slice(0, 8) + '), 产线用的是当前稿 (' + cur.script_id.slice(0, 8) + ')。'
              + '<a style="color:#fff;text-decoration:underline;font-weight:700" href="/web/audio.html?script_id='
              + encodeURIComponent(cur.script_id) + '&book_id=' + encodeURIComponent(ctx0.book_id)
              + '&ep_index=' + encodeURIComponent(ctx0.ep_index) + '">→ 去听当前稿</a>';
            document.body.prepend(bar);
          }
        }
      } catch (_) { /* 陈稿检查失败不阻塞 */ }
    } catch (e) {
      setStatus('status-audio', '加载脚本失败: ' + e.message, true);
    }
  }
  // PPT 书集上下文 (2026-08-21)
  _refreshPptBookCtx();
  _syncGotoAnim();  // 动画工坊直达链接
  // 恢复未完成 PPT 任务 (刷新后继续看进度, 不丢 job)
  // 优先 sessionStorage, 其次 URL 参数 ?ppt_job_id= (跨刷新/跨会话手工续看)
  try {
    const params = new URLSearchParams(location.search);
    const saved = sessionStorage.getItem('pptJobId') || params.get('ppt_job_id');
    if (saved) {
      _pptJobId = saved;
      const st = await (await fetch(API + `/ppt/${saved}/status`)).json();
      if (st && st.status === 'running') {
        _connectPPTSSE(saved);
        _pollPPT(saved);
      }
    }
  } catch (_) { /* 恢复失败不阻塞 */ }
});

// ── PPT 出片 (2026-08-20): 上传 → 解析 → 渲染 (有 PPT 走 PPT 产线, 无则现有) ──
// 2026-08-21: book_id/ep_index 绑定拆书系列 + 母本皮肤包
let _pptJobId = null;
let _pptES = null;

// 从 URL 取 book_id/ep_index (从拆书讲书页进产线带参)
function _pptBookCtx() {
  const p = new URLSearchParams(location.search);
  return { book_id: p.get('book_id') || '', ep_index: p.get('ep_index') || '' };
}

// 0916 用户令: 直达动画工坊 (带本书本集; 规划自动吃最新完成音频 — 音频页重生成是原地更新)
function _syncGotoAnim() {
  const ctx = _pptBookCtx();
  const a = document.getElementById('btn-goto-anim');
  if (!a) return;
  if (ctx.book_id && ctx.ep_index) {
    a.href = `/web/anim.html?book_id=${encodeURIComponent(ctx.book_id)}&ep_index=${ctx.ep_index}`;
  } else {
    a.style.display = 'none';
  }
}

async function _refreshPptBookCtx() {
  const ctx = _pptBookCtx();
  const el = document.getElementById('ppt-bookctx');
  if (!el) return;
  if (!ctx.book_id) { el.textContent = ''; return; }
  let txt = `📖 书 ${ctx.book_id.slice(0, 8)}…` + (ctx.ep_index ? ` · 第${ctx.ep_index}集` : '');
  // 0908 双产线闸门: 老谭拆书线走 bs1 页单工坊 (探测=bs1/state 放行), 本页 pptx 上传/导演台隐藏
  try {
    const probe = await fetch(API + `/ppt/bs1/state?book_id=${encodeURIComponent(ctx.book_id)}&ep_index=${ctx.ep_index || 1}`);
    if (probe.ok) {
      window._IS_BS1_BOOK = true;
      // from_bs1 入口提示 (工坊桥接而来, 听审完回工坊)
      if (new URLSearchParams(location.search).get('from_bs1') === '1') {
        const tip = document.createElement('div');
        tip.style.cssText = 'margin:0.5rem 0;padding:0.5rem 0.8rem;border-radius:8px;background:rgba(201,162,94,.12);border:1px solid #C9A25E;font-size:0.82rem';
        tip.innerHTML = `📐 <b>bs1 页单稿</b> (页=段) — 生成音频并试听校验后, <a href="/web/bs1_story.html?book_id=${encodeURIComponent(ctx.book_id)}&ep_index=${ctx.ep_index || 1}" style="color:#C9A25E;font-weight:600">回页单工坊 → 生成视频</a> (音频自动复用)`;
        const c = document.querySelector('.container') || document.body;
        c.prepend(tip);
      }
      const sec = document.querySelector('.ppt-section');
      if (sec) sec.innerHTML = `<div style="font-size:0.8rem;color:var(--text-muted);padding:0.2rem 0">
        📐 本书走 <b>bs1 页单工坊</b> 产线 (页单→确认→音视频→剪映草稿, 不经过本页)
        <a href="/web/bs1_story.html?book_id=${encodeURIComponent(ctx.book_id)}&ep_index=${ctx.ep_index || 1}"
           style="color:#4ade80">打开工坊 ↗</a></div>`;
      return;
    }
  } catch (_) {}
  try {
    const r = await (await fetch(API + `/ppt/series-skin/${encodeURIComponent(ctx.book_id)}`)).json();
    if (r.has_master) txt += ` ｜ ✅ 已有母本(第${r.master_ep}集), 渲染自动对齐皮肤`;
  } catch (_) {}
  el.textContent = txt;
}

async function uploadPPT() {
  const file = document.getElementById('ppt-file').files[0];
  const status = document.getElementById('ppt-status');
  if (!file) { status.textContent = '请先选择 .pptx 文件'; return; }
  status.textContent = '上传解析中…';
  const ctx = _pptBookCtx();
  try {
    const fd = new FormData();
    fd.append('file', file);
    if (ctx.book_id) fd.append('book_id', ctx.book_id);
    if (ctx.ep_index) fd.append('ep_index', ctx.ep_index);
    // 2026-08-22: 前端选的音色传入 PPT 产线 (此前固定用书账号音色静姐)
    const voiceId = document.getElementById('voice-select').value;
    if (voiceId) fd.append('voice_id', voiceId);
    const resp = await fetch(API + '/ppt/upload', { method: 'POST', body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const r = await resp.json();
    _pptJobId = r.job_id;
    try { sessionStorage.setItem('pptJobId', _pptJobId); } catch (_) {}
    const preview = (r.preview || []).map(p =>
      `P${p.index} [${p.notes_len}字] ${p.text_preview}`).join('\n');
    document.getElementById('ppt-preview').textContent = preview;
    document.getElementById('btn-ppt-render').disabled = false;
    // 母本按钮: 绑定了 book_id 且尚未有母本时显示
    const masterBtn = document.getElementById('btn-ppt-master');
    if (masterBtn) {
      if (ctx.book_id && ctx.ep_index && !r.has_master) {
        masterBtn.style.display = '';
        masterBtn.disabled = false;
      } else {
        masterBtn.style.display = 'none';
      }
    }
    let msg = `解析成功: ${r.slides} 页`;
    if (r.has_master) msg += ` · 已套用现成母本(第${r.master_ep}集)皮肤`;
    if (ctx.book_id && ctx.ep_index && !r.has_master) msg += ' · 可点「定为母本」';
    status.textContent = msg;
    document.getElementById('ppt-download').style.display = 'none';
  } catch (e) {
    status.textContent = '上传失败: ' + e.message;
  }
}

async function markMasterPPT() {
  if (!_pptJobId) return;
  const btn = document.getElementById('btn-ppt-master');
  const status = document.getElementById('ppt-status');
  btn.disabled = true; btn.textContent = '⏳ 抽取中…';
  try {
    const resp = await fetch(API + `/ppt/${_pptJobId}/mark-master`, { method: 'POST' });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${resp.status}`); }
    const r = await resp.json();
    status.textContent = `✅ 已定为母本: 主色 ${r.color_tokens.join('/')} 字号档 ${r.fontsize_tokens.join('/')}`;
    btn.textContent = '✓ 已为母本';
    toast('母本皮肤包已生成', 'success');
    _refreshPptBookCtx();
  } catch (e) {
    status.textContent = '定为母本失败: ' + e.message;
    btn.disabled = false; btn.textContent = '📌 定为母本';
  }
}

async function renderPPT() {
  const status = document.getElementById('ppt-status');
  if (!_pptJobId) { status.textContent = '请先上传 PPT'; return; }
  const btn = document.getElementById('btn-ppt-render');
  btn.disabled = true; btn.textContent = '⏳ 成片中…';
  status.textContent = '开始 PPT 成片 (TTS → 渲染 → 拼片, 约 15-25 分钟)…';
  try {
    const resp = await fetch(API + `/ppt/${_pptJobId}/render`, { method: 'POST' });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${resp.status}`); }
    _connectPPTSSE(_pptJobId);
    _pollPPT(_pptJobId);
  } catch (e) {
    status.textContent = '成片启动失败: ' + e.message;
    btn.disabled = false; btn.textContent = '🎬 PPT 一键成片';
  }
}

function _connectPPTSSE(jobId) {
  if (_pptES) _pptES.close();
  _pptES = new EventSource(API + `/ppt/${jobId}/events`);
  _pptES.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === 'ppt_done') { setStatus('ppt-status-ss', '成片完成 ✓', false, true); }
    if (d.type === 'ppt_error') { setStatus('ppt-status-ss', '成片失败: ' + (d.msg || ''), true); }
  };
  _pptES.onerror = () => _pptES.close();
}

async function _pollPPT(jobId) {
  const status = document.getElementById('ppt-status');
  const btn = document.getElementById('btn-ppt-render');
  try {
    const resp = await fetch(API + `/ppt/${jobId}/status`);
    const st = await resp.json();
    const ev = st.events || [];
    const bar = document.getElementById('ppt-progress');
    if (ev.length) status.textContent = ev[ev.length - 1].msg;
    // 进度条: 取最近带 progress 的事件 (TTS x/21 / 渲染 x/21)
    if (bar) {
      let prog = null;
      for (let i = ev.length - 1; i >= 0 && !prog; i--) if (ev[i].progress) prog = ev[i];
      if (prog && prog.progress && String(prog.progress).includes('/')) {
        const parts = String(prog.progress).split('/').map(Number);
        if (parts[1] > 0) { bar.value = parts[0] / parts[1]; bar.style.display = ''; }
      } else {
        bar.style.display = 'none';
      }
    }
    if (st.status === 'running') {
      setTimeout(() => _pollPPT(jobId), 3000);
    } else {
      if (bar) bar.style.display = 'none';
      if (st.status === 'failed' || st.error) {
        btn.disabled = false; btn.textContent = '🎬 PPT 一键成片';
        status.textContent = '失败: ' + (st.error || '');
      } else if (st.status === 'done') {
        btn.disabled = false; btn.textContent = '🎬 PPT 一键成片';
        const dl = document.getElementById('ppt-download');
        const jyBtn = document.getElementById('btn-ppt-jy');
        if (st.draft) {
          // jy2 元素级: 草稿已直接进剪映草稿箱, 隐藏下载/手动导出按钮
          status.textContent = '✅ 剪映草稿已生成: ' + st.draft.draft_name + ' — 打开剪映即可查看/导出';
          if (dl) dl.style.display = 'none';
          if (jyBtn) jyBtn.style.display = 'none';
          const draftEl = document.getElementById('ppt-draft-name');
          if (draftEl) draftEl.textContent = st.draft.draft_name;
        } else {
          dl.href = API + `/ppt/${jobId}/download`;
          dl.style.display = '';
          if (jyBtn) { jyBtn.style.display = ''; jyBtn.disabled = false; jyBtn.dataset.jobId = jobId; }
          status.textContent = '成片完成 ✓ 可下载或导出剪映草稿';
        }
      }
    }
  } catch (e) {
    status.textContent = '轮询失败: ' + e.message;
  }
}

// PPT 出片 → 剪映草稿 (J 线)
async function exportPPTJy() {
  const btn = document.getElementById('btn-ppt-jy');
  const jobId = btn && btn.dataset.jobId;
  const status = document.getElementById('ppt-status');
  if (!jobId) { status.textContent = '请先完成成片'; return; }
  btn.disabled = true; btn.textContent = '⏳ 导出中…';
  try {
    const resp = await fetch(API + `/ppt/${jobId}/export-jy-draft`, { method: 'POST' });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${resp.status}`); }
    const r = await resp.json();
    status.textContent = `剪映草稿已导出 ✓ ${r.video_segments} 页画面 + ${r.text_segments} 条字幕 → ${r.draft_name}`;
    btn.disabled = false; btn.textContent = '🎬 导出剪映草稿';
    toast('剪映草稿已生成', 'success');
  } catch (e) {
    status.textContent = '导出失败: ' + e.message;
    btn.disabled = false; btn.textContent = '🎬 导出剪映草稿';
  }
}
