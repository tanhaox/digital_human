// ⑤ K2 页 (V6 薄壳: v3_k2 相位 + 人检)
import { rail } from './rail.js';
import { createJobRunner } from './job.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const ST_ZH = { planned: '待图', img_done: '图出', approved: '✓ 已过审', failed: '✗ 失败' };
let SHOTS = [], FROZEN = false;

const runner = createJobRunner({
  onFinished: job => {
    if (job.status === 'completed')
      toast(`✓ K2 完成: ${job.result?.ok ?? '?'} 成 / ${job.result?.fail ?? 0} 败` +
            (job.result?.suspect ? ` · ${job.result.suspect} 镜文字嫌疑` : ''));
    else if (job.status !== 'unknown') toast('K2 结束: ' + (job.status || ''));
    boot();
  },
  onBusy: busy => { $('btn-run').disabled = busy; $('btn-all').disabled = busy; },
});

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/k2`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; FROZEN = !!d.frozen;
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'k2' });
  if (st.running_job) runner.attach(st.running_job);
  renderCounts(); renderGrid();
}

function renderCounts() {
  const n = SHOTS.length;
  const by = k => SHOTS.filter(s => (s.status || 'planned') === k).length;
  const sus = SHOTS.filter(s => s.suspect).length;
  $('counts').innerHTML =
    `<span class="badge">${n} 镜</span>` +
    `<span class="badge">${by('img_done')} 图出</span>` +
    `<span class="badge ${by('approved') === n && n ? 'ok' : ''}">${by('approved')} 已过审</span>` +
    (sus ? `<span class="badge bad">${sus} 文字嫌疑</span>` : '') +
    (by('failed') ? `<span class="badge bad">${by('failed')} 失败</span>` : '');
  const img = n - by('planned');
  $('stat').textContent = FROZEN
    ? `图 ${img}/${n} · 过审 ${by('approved')}/${n} — 全过审后可进 ⑦ 盖章`
    : '🔒 ④ 未放行 — 先在导演页确认放行 (英文化批) 才能生图';
}

function renderGrid() {
  if (!SHOTS.length) { $('grid').innerHTML = '<div class="note">未切镜 — 先跑 ③</div>'; return; }
  $('grid').innerHTML = SHOTS.map(s => {
    const st = s.status || 'planned';
    const img = s.image
      ? `<img src="/api/anim/ep/${encodeURIComponent(B)}/${E}/file/${esc(s.image)}" loading="lazy"
              alt="${esc(s.sid)}" title="${esc(s.sid)} · seed ${s.seed ?? '?'}">`
      : (s.brand ? '🏔 品牌卡直通 (待生成)' : '— 待生成 —');
    return `<div class="kcard ${st === 'approved' ? 'approved' : (s.suspect ? 'suspect' : '')}">
      <div class="kimg">${img}</div>
      <div class="kmeta">
        <span class="ksid">${esc(s.sid)}</span>
        <span class="ksize">${esc(s.size || '')}</span>
        <span class="kcam">${esc(s.cam || '')}${s.cam && s.cam !== '固定' ? ' ↦' : ''}</span>
        <span class="flex1"></span>
        ${s.suspect ? '<span class="kbadge failed" title="OCR 检字命中 — 看图确认是否乱字">⚠文字</span>' : ''}
        <span class="kbadge ${st}">${ST_ZH[st] || st}</span>
      </div>
      ${s.error ? `<div class="kerr">${esc(s.error)}</div>` : ''}
      <div class="kbtns">
        <button class="iconbtn" data-act="approve" data-sid="${esc(s.sid)}"
                ${st !== 'img_done' ? ' disabled' : ''}>✅ 过审</button>
        <span class="flex1"></span>
        <button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}"
                title="单镜重掷 (换 seed)">${st === 'planned' ? '▶ 生成' : '🔁 重掷'}</button>
      </div>
    </div>`;
  }).join('');
  $('grid').querySelectorAll('button[data-act]').forEach(b => {
    b.onclick = async () => {
      const sid = b.dataset.sid;
      b.disabled = true;
      try {
        if (b.dataset.act === 'approve') {
          const r = await (await fetch(`${BASE}/k2/approve`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sids: [sid] }) })).json();
          toast(`✓ ${sid} 已过审 (${r.approved})`);
          boot();
        } else {
          const r = await fetch(`${BASE}/v3_k2`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ only: [sid] }) });
          if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); b.disabled = false; return; }
          runner.attach(await r.json());
        }
      } catch (e) { toast('操作失败: ' + e.message); b.disabled = false; }
    };
  });
}

$('btn-run').onclick = async () => {
  const r = await fetch(`${BASE}/v3_k2`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
};
$('btn-all').onclick = async () => {
  const r = await (await fetch(`${BASE}/k2/approve`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{"all":true}' })).json();
  toast(`✓ 过审 ${r.approved} 镜`);
  boot();
};

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}
boot();
