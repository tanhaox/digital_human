// ── v3 任务面板 (1105 移植自 v2 web/anim/job.js 成熟模式, ①-④ 共用) ──
// SSE 推流 + 轮询兜底双通道; 进度条/日志/取消/熔断/结束驻留。
// v3 增: 面板 DOM 自带 (无需各页写 HTML); busy 回调 (挂任务时禁页内操作钮)。
const POLL_MS = 3000, LOG_MAX = 200, HIDE_MS = 4000, POLL_ERR_MAX = 10, SSE_CLOSED = 2;

const PHASE_LABEL = {
  v3_triage: '① 文稿分诊', v3_arcs: '② 立意', v3_shots: '③ 分镜文字',
  v3_direct: '④ 导演', v3_stamp: '⑦ 盖章', v3_en: '🔤 英文化批',
};

/** 面板骨架 (CSP: 零内联, 样式全在 v3.css) */
function buildPanel() {
  const d = document.createElement('div');
  d.id = 'v3job';
  d.hidden = true;
  d.innerHTML = `
    <div class="jrow"><b id="v3job-phase"></b><span class="flex1"></span>
      <button class="btn-fix" id="v3job-cancel">⛔ 停止</button></div>
    <div class="jbar"><div class="jbar-fill" id="v3job-bar"></div></div>
    <div class="jprog mono" id="v3job-prog"></div>
    <pre class="jlog mono" id="v3job-log"></pre>`;
  document.body.appendChild(d);
  return {
    panel: d,
    phase: d.querySelector('#v3job-phase'),
    bar: d.querySelector('#v3job-bar'),
    prog: d.querySelector('#v3job-prog'),
    log: d.querySelector('#v3job-log'),
    cancelBtn: d.querySelector('#v3job-cancel'),
  };
}

/**
 * @param {{onFinished:(job:object)=>void, onBusy?:(busy:boolean)=>void,
 *          notify?:(msg:string)=>void}} deps
 */
export function createJobRunner({ onFinished, onBusy, notify = msg => {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
} }) {
  let jobId = null, es = null, pollId = null, hideId = null;
  let errStreak = 0, finished = false, lastJob = null;
  let dom = null;
  const el = () => dom || (dom = buildPanel());

  function appendLog(msg) {
    const { log } = el();
    log.textContent = (log.textContent + '\n' + msg).split('\n').slice(-LOG_MAX).join('\n');
    log.scrollTop = log.scrollHeight;
  }

  function setProg(p) {
    if (!p) return;
    const { bar, prog } = el();
    bar.style.width = p.total ? Math.round((p.done / p.total) * 100) + '%' : '0%';
    prog.textContent = `${p.done}/${p.total}${p.current ? ' · ' + p.current : ''}`;
  }

  function showPanel(job) {
    clearTimeout(hideId); hideId = null;
    const { panel, phase, prog, cancelBtn } = el();
    panel.hidden = false;
    phase.textContent = `${PHASE_LABEL[job.phase] || job.phase} · ep${job.ep ?? ''}`;
    prog.textContent = '';
    cancelBtn.onclick = () => runner.cancel();
  }

  function openSSE() {
    es = new EventSource('/api/jobs/' + jobId + '/events');
    es.onmessage = ev => {
      let d; try { d = JSON.parse(ev.data); } catch { return; }
      if (d.type === 'anim_progress') {
        setProg({ done: d.done, total: d.total, current: d.shot_id });
      } else if (d.type === 'anim_log' || d.type === 'anim_service') {
        appendLog(d.message);
      } else if (['anim_done', 'anim_error', 'anim_cancelled'].includes(d.type)) {
        appendLog(d.type === 'anim_done'
          ? '== 完成 ' + ((d.result && JSON.stringify(d.result)) || '')
          : `== ${d.type}: ${d.error || ''}`);
        el().phase.textContent = d.type === 'anim_done' ? '✅ 完成' : '❌ ' + d.type.replace('anim_', '');
        finish();
      }
    };
    es.onerror = () => {
      if (es.readyState === SSE_CLOSED) { appendLog('⚠ SSE 已关闭 — 轮询兜底'); es = null; }
      else appendLog('⚠ SSE 重连中 — 轮询兜底');
    };
  }

  async function poll() {
    if (!jobId || (typeof document !== 'undefined' && document.hidden)) return;
    try {
      const r = await fetch('/api/anim/job/' + jobId);
      if (!r.ok) throw new Error('job ' + r.status);
      const job = await r.json();
      lastJob = job;
      errStreak = 0;
      setProg(job.progress);
      if (job.error) appendLog('ERROR ' + job.error);
      if (job.status !== 'running') finish();
    } catch (e) {
      if (String(e.message || '').includes('404')) {
        appendLog('⚠ 任务已从服务端归档 (404) — 停止轮询');
        finish(); return;
      }
      if (++errStreak >= POLL_ERR_MAX) { notify('任务状态轮询失败: ' + e.message); finish(); }
    }
  }

  function finish() {
    if (finished) return;
    finished = true;
    detach();
    hideId = setTimeout(() => { if (dom) dom.panel.hidden = true; }, HIDE_MS);
    if (onBusy) onBusy(false);
    onFinished(lastJob || { status: 'unknown' });
  }

  function detach() {
    clearInterval(pollId); pollId = null;
    clearTimeout(hideId); hideId = null;
    if (es) { es.close(); es = null; }
    jobId = null;
  }

  const runner = {
    attach(job) {
      if (jobId === job.id) { showPanel(job); return; }
      detach();
      finished = false; errStreak = 0;
      jobId = job.id; lastJob = job;
      showPanel(job);
      openSSE();
      pollId = setInterval(poll, POLL_MS);
      if (onBusy) onBusy(true);
    },
    activeId: () => jobId,
    cancel: async () => {
      if (!jobId) return;
      try {
        await fetch('/api/anim/job/' + jobId + '/cancel', { method: 'POST' });
        notify('已请求停止 — 当前批完成后中断');
      } catch (e) { notify('取消失败: ' + e.message); }
    },
    appendLog, detach,
  };
  return runner;
}
