// ⑧ H3 页 (V6b): v3_h3 相位 + 视频卡人检 + 灯箱
import { rail } from './rail.js';
import { createJobRunner } from './job.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const ST_ZH = { approved: '图过审·待生', anim_done: '✓ 视频出', anim_fail: '✗ 失败',
                planned: '待图', img_done: '图出·待审' };
let SHOTS = [];

const runner = createJobRunner({
  onPoll: job => {                     // 逐镜增量 (与 ⑥ K2 同机制)
    if (job?.status !== 'running' || !job.progress?.current) return;
    const m = String(job.progress.current).match(/^(s\d+)/);
    if (!m) return;
    const sid = m[1];
    fetch(`${BASE}/h3`).then(r => r.json()).then(d => {
      const s = (d.shots || []).find(x => x.sid === sid);
      const card = document.querySelector(`.hcard[data-sid="${sid}"]`);
      if (s && card && card.dataset.loaded !== '1' && s.video) {
        card.dataset.loaded = '1';
        const box = card.querySelector('.hmedia');
        if (box) box.innerHTML = `<video controls preload="metadata" src="/api/anim/ep/${encodeURIComponent(B)}/${E}/file/anim/${sid}.mp4?v=${Date.now()}"></video>`;
        const b = card.querySelector('.hbadge');
        if (b) { b.className = 'hbadge anim_done'; b.textContent = '✓ 视频出'; }
      }
    }).catch(() => {});
  },
  onFinished: job => {
    if (job.status === 'completed')
      toast(`✓ H3 完成: ${job.result?.ok ?? '?'} 成 / ${job.result?.fail ?? 0} 败`);
    else if (job.status !== 'unknown') toast('H3 结束: ' + (job.status || ''));
    boot();
  },
  onBusy: busy => { $('btn-run').disabled = busy; $('btn-retry').disabled = busy; },
});

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/h3`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || [];
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'h3' });
  if (st.running_job) runner.attach(st.running_job);
  $('btn-v2').href = `/web/anim.html?book_id=${encodeURIComponent(B)}&ep_index=${E}`;
  renderCounts(); renderGrid();
  const nApp = SHOTS.filter(s => s.status === 'approved').length;
  const nImg = SHOTS.filter(s => s.status === 'img_done').length;
  const nDone = SHOTS.filter(s => s.status === 'anim_done').length;
  $('stat').textContent = d.stamped
    ? `视频 ${nDone}/${SHOTS.length} · 待生 ${nApp}`
      + (nImg ? ` · ⚠ ${nImg} 镜图出未过审 (去 ⑥ 页 ✅)` : '')
      + ' — 全出后 v2 工坊装配剪映草稿'
    : '🔒 ⑦ 未盖章 — H3 吃实测时长, 先盖章';
}

function renderCounts() {
  const by = k => SHOTS.filter(s => s.status === k).length;
  $('counts').innerHTML =
    `<span class="badge">${SHOTS.length} 镜</span>` +
    `<span class="badge">${by('approved')} 待生</span>` +
    `<span class="badge ${by('anim_done') ? 'ok' : ''}">${by('anim_done')} 视频出</span>` +
    (by('anim_fail') ? `<span class="badge bad">${by('anim_fail')} 败</span>` : '') +
    `<span class="badge">${Math.round(SHOTS.reduce((a, s) => a + (s.dur_s || 0), 0))}s</span>`;
}

function renderGrid() {
  if (!SHOTS.length) { $('grid').innerHTML = '<div class="note">未盖章 — 先跑 ⑦</div>'; return; }
  $('grid').innerHTML = SHOTS.map(s => {
    const stt = s.status || 'planned';
    const media = s.video
      ? `<video controls preload="metadata" src="/api/anim/ep/${encodeURIComponent(B)}/${E}/file/anim/${s.sid}.mp4"></video>`
      : (s.image_url ? `<img src="${esc(s.image_url)}" loading="lazy" alt="${esc(s.sid)}" title="首帧 (K2 图)">`
                 : '— 待生 —');
    return `<div class="hcard ${stt === 'anim_done' ? 'done' : (stt === 'anim_fail' ? 'fail' : '')}" data-sid="${esc(s.sid)}">
      <div class="hmedia">${media}</div>
      <div class="hmeta">
        ${s.brand ? '' : `<input type="checkbox" class="hsel" data-sid="${esc(s.sid)}" title="加入购物篮 (列队重做)">`}
        <span class="hsid">${esc(s.sid)}</span>
        <span class="hsize">${esc(s.size || '')}</span>
        <span class="hcam">${esc(s.cam || '')}</span>
        <span class="flex1"></span>
        <span class="hbadge ${stt}">${ST_ZH[stt] || stt}</span>
      </div>
      ${s.error ? `<div class="herr">${esc(s.error)}</div>` : ''}
      <div class="hbtns">
        <button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}"
                ${stt !== 'anim_done' && stt !== 'anim_fail' ? ' disabled' : ''}
                title="换 h3_seed 重生本镜">${stt === 'planned' || stt === 'approved' ? '▶ 生成' : '🎲 重掷'}</button>
      </div>
    </div>`;
  }).join('');
  $('grid').querySelectorAll('button[data-act]').forEach(b => {
    b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await fetch(`${BASE}/v3_h3`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ only: [b.dataset.sid] }) });
        if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); b.disabled = false; return; }
        runner.attach(await r.json());
      } catch (e) { toast('失败: ' + e.message); b.disabled = false; }
    };
  });
}

$('btn-draft').onclick = async () => {
  const nDone = SHOTS.filter(s => s.status === 'anim_done').length;
  if (SHOTS.some(s => s.status !== 'anim_done')) {
    if (!confirm(`还有 ${SHOTS.length - nDone} 镜未出视频 — 继续装配? (缺镜段只有口播+字幕)`)) return;
  }
  const b = $('btn-draft'); b.disabled = true; b.textContent = '⏳ 装配中…';
  try {
    const r = await fetch(`/api/anim/ep/${encodeURIComponent(B)}/${E}/draft`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const d = await r.json();
    if (!r.ok) { toast('装配失败: ' + (d.detail || '')); return; }
    toast(`✓ 剪映草稿已装配: ${JSON.stringify(d).slice(0, 120)}`);
  } catch (e) { toast('装配失败: ' + e.message); }
  b.disabled = false; b.textContent = '🎞 装配剪映草稿';
};

$('btn-run').onclick = async () => {
  const nApp = SHOTS.filter(s => s.status === 'approved').length;
  if (!nApp) { toast('无已过审镜 — 先去 ⑥ K2 页过审 (✅ / 全部过审), H3 只吃 approved'); return; }
  const r = await fetch(`${BASE}/v3_h3`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
};
$('btn-retry').onclick = async () => {
  const n = SHOTS.filter(s => s.status === 'anim_fail').length;
  if (!n) { toast('无败镜'); return; }
  const r = await fetch(`${BASE}/v3_h3`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{"retry_failed":true}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
};

// ── 购物篮 (1106 用户令: 对齐 v2 — 清空 + ♻️列队重做全链) ──
function cartSids() {
  return [...document.querySelectorAll('.hsel:checked')].map(c => c.dataset.sid);
}
function refreshCart() {
  const n = cartSids().length;
  $('cart').hidden = !n;
  $('cart-count').textContent = n;
}
$('grid').addEventListener('change', e => {
  if (e.target.classList.contains('hsel')) refreshCart();
});
$('btn-cart-clear').onclick = () => {
  document.querySelectorAll('.hsel:checked').forEach(c => { c.checked = false; });
  refreshCart();
};
$('btn-redo').onclick = async () => {
  const sids = cartSids();
  if (!sids.length) return;
  if (!confirm(`♻️ 列队重做 ${sids.length} 镜 (全链到视频)?
LLM重设计画面句 → 重译英文 → 重生图(换seed) → 生视频 · ~5min/镜 · 镜间可⛔停止`)) return;
  const note = prompt('重做原因 (可空, 会带进重设计提示; 留空用系统默认)', '') ?? '';
  const r = await fetch(`${BASE}/v3_redo`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ only: sids, to_video: true, note }) });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  document.querySelectorAll('.hsel:checked').forEach(c => { c.checked = false; });
  refreshCart();
  runner.attach(await r.json());
};

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}
boot();
