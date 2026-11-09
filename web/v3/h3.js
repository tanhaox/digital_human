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
let SHOTS = [], BLOCKED = false, RUNNING = false;   // 1109: 首卡点禁钮 + 任务态双旗

/* 1108 修 (与 ⑥ K2 同根): job 完成 boot() 重渲染清 checkbox, cart 条停旧值
   → 重做按钮静默失效。SEL 单源跨重渲染存活; sessionStorage 兜刷新。 */
const SEL = new Set(JSON.parse(sessionStorage.getItem('h3sel') || '[]'));
function selSync() {
  const live = new Set(SHOTS.map(s => s.sid));
  [...SEL].forEach(x => { if (!live.has(x)) SEL.delete(x); });
  try { sessionStorage.setItem('h3sel', JSON.stringify([...SEL])); } catch (e) {}
}

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
  onBusy: busy => { RUNNING = busy;
    $('btn-run').disabled = busy || BLOCKED; $('btn-retry').disabled = busy;
    if (busy) $('btn-approve').disabled = true; },
});

let LASTG = {}, LASTSTAMPED = false;   // 1109: 过审后免整页刷新就地重算闸

function refreshGate() {
  const nApp = SHOTS.filter(s => s.status === 'approved').length;
  const nImg = SHOTS.filter(s => s.status === 'img_done').length;
  const nDone = SHOTS.filter(s => s.status === 'anim_done').length;
  // 1109 修 (不动作实锤): 常驻状态行只知 ⑦ 盖章, 不知上游卡点 — 首卡点指路。
  const blocker = !LASTG.direct
    ? '🔒 ④ 未放行 — 回 ④ 页: 右栏清拦截项 (⚡改固定) → ✅ 确认放行 (英文化批)'
    : (nApp + nImg) === 0
      ? '🔒 ⑥ K2 未生图 — 回 ⑥ 页 🎨 生图后过审 (✅), H3 只吃 approved'
      : !nApp
        ? `🔒 ${nImg} 镜图出未过审 — 点图过目 (可放大翻页) → ✅ 全部过审 → 🎬 批生`
        : !LASTSTAMPED
          ? '🔒 ⑦ 未盖章 — H3 吃实测时长, 先盖章'
          : '';
  BLOCKED = !!blocker;
  if (blocker) {
    $('stat').textContent = blocker;
    $('btn-run').title = blocker;
  } else {
    const nImgNB = SHOTS.filter(s => s.status === 'img_done' && !s.brand).length;
    $('stat').textContent = `视频 ${nDone}/${SHOTS.length} · 待生 ${nApp}`
      + (nImgNB ? ` · ⚠ ${nImgNB} 镜图出未过审 (去 ⑥ 页 ✅)` : '')
      + ' — 全出后 v2 工坊装配剪映草稿';
    $('btn-run').title = 'approved 镜批生视频 (h3/r2v 工位自动路由)';
  }
  $('btn-run').disabled = BLOCKED || RUNNING;   // 1109: 双旗并集, 任务期不误启
  $('btn-approve').disabled = !nImg || RUNNING;   // 1109: 全审钮 — 有图出才可点, 任务期锁
}

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/h3`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || [];
  LASTG = d.gates || {}; LASTSTAMPED = !!d.stamped;
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'h3' });
  if (st.running_job) runner.attach(st.running_job);
  $('btn-v2').href = `/web/anim.html?book_id=${encodeURIComponent(B)}&ep_index=${E}`;
  renderCounts(); renderGrid();
  refreshGate();
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
    // 品牌镜: video 字段是 ep 目录外资产路径, /file/ 拦穿越 → 后端专用端点
    // (h3/brand-video) 下发 video_url; 未直通 (无 video) 退品牌首帧图。
    const media = s.brand
      ? (s.video_url
          ? `<video controls preload="metadata" src="${esc(s.video_url)}" title="品牌定稿视频"></video>`
          : (s.image_url ? `<img src="${esc(s.image_url)}" loading="lazy" alt="${esc(s.sid)}" title="品牌卡首帧">` : '— 待生 —'))
      : (s.video
      ? `<video controls preload="metadata" src="/api/anim/ep/${encodeURIComponent(B)}/${E}/file/anim/${s.sid}.mp4"></video>`
      : (s.image_url ? `<img src="${esc(s.image_url)}" loading="lazy" alt="${esc(s.sid)}" title="首帧 (K2 图)">`
                 : '— 待生 —'));
    return `<div class="hcard ${stt === 'anim_done' ? 'done' : (stt === 'anim_fail' ? 'fail' : '')}" data-sid="${esc(s.sid)}">
      <div class="hmedia">${media}</div>
      <div class="hmeta">
        ${s.brand ? '' : `<input type="checkbox" class="hsel" data-sid="${esc(s.sid)}"${SEL.has(s.sid) ? ' checked' : ''} title="加入购物篮 (列队重做)">`}
        <span class="hsid">${esc(s.sid)}</span>
        <span class="hsize">${esc(s.size || '')}</span>
        <span class="hcam">${esc(s.cam || '')}</span>
        <span class="flex1"></span>
        ${s.redo_review ? '<span class="hbadge bd-r" title="重做待复查: 此镜刚被列队重做 — 播放看过后消除">R</span>' : ''}
        <span class="hbadge ${stt}">${ST_ZH[stt] || stt}</span>
      </div>
      ${s.error ? `<div class="herr">${esc(s.error)}</div>` : ''}
      <div class="hbtns">
        ${s.brand ? `<button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}"
                title="品牌卡重置: 失效后批内零 GPU 直通重插定稿资产 (秒回)">↺ 重置</button>`
      : `<button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}"
                ${stt !== 'anim_done' && stt !== 'anim_fail' ? ' disabled' : ''}
                title="先 K2 同句换seed重生 (当前代码锚 look/防人) → 再生视频 (~4-5min)">${stt === 'planned' || stt === 'approved' ? '▶ 生成' : '🎲 重掷'}</button>`}
      </div>
    </div>`;
  }).join('');
  selSync(); refreshCart();
  $('grid').querySelectorAll('button[data-act]').forEach(b => {
    b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await fetch(`${BASE}/v3_h3`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ only: [b.dataset.sid], redo_k2: true }) });
        if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); b.disabled = false; return; }
        runner.attach(await r.json());
      } catch (e) { toast('失败: ' + e.message); b.disabled = false; }
    };
  });
  // 1109: 点图放大细看 (过审=人眼唯一图检, 令51 — 卡片缩图看不清肤色/双入境)
  const _imgs = [...$('grid').querySelectorAll('.hmedia img')];
  _imgs.forEach(im => {
    im.onclick = () => lbOpen(im.src, `${im.alt} 首帧 (K2 图)`,
      _imgs.map(x => ({ src: x.src, cap: x.alt })));
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
  if (!nApp) { toast('无已过审镜 — 先点图过目再 ✅ 全部过审, H3 只吃 approved'); return; }
  const r = await fetch(`${BASE}/v3_h3`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
};

// 1109 用户令: 全部过审 (单镜 ✅ 已按令撤, 过目靠点图放大)
$('btn-approve').onclick = async () => {
  const sids = SHOTS.filter(s => s.status === 'img_done' && !s.brand).map(s => s.sid);
  if (!sids.length) { toast('无图出待审镜'); return; }
  const b = $('btn-approve'); b.disabled = true; b.textContent = '⏳ 过审中…';
  try {
    const r = await (await fetch(`${BASE}/k2/approve`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sids }) })).json();
    if (r && r.detail) { toast('过审被拒: ' + r.detail); boot(); return; }
    toast(r.approved ? `✓ 已过审 ${r.approved} 镜 — 可 🎬 批生`
                     : '✓ 全部已是过审态 — 可 🎬 批生');
    // 1109 (全审后按钮仍黑实锤): 旧服务器 /h3 读盖章快照看不到过审 —
    // 本地立现 (批任务开跑时 h3.run 会现场同步真状态); 新服务器投影后同真。
    SHOTS.forEach(s => { if (s.status === 'img_done' && !s.brand) s.status = 'approved'; });
    renderCounts(); renderGrid(); refreshGate();
  } catch (e) { toast('过审失败: ' + e.message); boot(); return; }
  b.disabled = false; b.textContent = '✅ 全部过审';
};
$('btn-retry').onclick = async () => {
  const n = SHOTS.filter(s => s.status === 'anim_fail').length;
  if (!n) { toast('无败镜'); return; }
  const r = await fetch(`${BASE}/v3_h3`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{"retry_failed":true}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
};

// 1108 R 标 (v2 机制继承): 播放看过 = 摘徽章 + POST reviewed (v2 actions.js 语义)
document.addEventListener('play', ev => {
  const card = ev.target.closest?.('.hcard');
  const badge = card?.querySelector('.hbadge.bd-r');
  if (!badge) return;
  badge.remove();
  fetch(`${BASE}/reviewed/${encodeURIComponent(card.dataset.sid)}`, { method: 'POST' }).catch(() => {});
}, true);

// ── 购物篮 (1106 用户令: 对齐 v2 — 清空 + ♻️列队重做全链) ──
function cartSids() {
  return SHOTS.filter(s => SEL.has(s.sid)).map(s => s.sid);
}
function refreshCart() {
  const n = cartSids().length;
  $('cart').hidden = !n;
  $('cart-count').textContent = n;
}
$('grid').addEventListener('change', e => {
  if (e.target.classList.contains('hsel')) {
    e.target.checked ? SEL.add(e.target.dataset.sid) : SEL.delete(e.target.dataset.sid);
    selSync(); refreshCart();
  }
});
$('btn-cart-clear').onclick = () => {
  SEL.clear(); selSync();
  document.querySelectorAll('.hsel').forEach(c => { c.checked = false; });
  refreshCart();
};
$('btn-redo').onclick = async () => {
  const sids = cartSids();
  if (!sids.length) { toast('未选镜 — 勾选镜卡片上的选择框'); return; }
  if (!confirm(`♻️ 列队重做 ${sids.length} 镜 (全链到视频)?
LLM重设计画面句 → 重译英文 → 重生图(换seed) → 生视频 · ~5min/镜 · 镜间可⛔停止`)) return;
  const note = prompt('重做原因 (可空, 会带进重设计提示; 留空用系统默认)', '') ?? '';
  const r = await fetch(`${BASE}/v3_redo`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ only: sids, to_video: true, note }) });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  SEL.clear(); selSync();
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
