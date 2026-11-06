// ⑦ 盖章页 (V5): 实测轴 + 对账 + 盖章 (查表灌秒 + v2 回填, 确定性零 LLM)
import { rail } from './rail.js';
import { timeline } from './timeline.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const TYPE_ZH = { narr: '叙事弧', quote: '书证镜', persona: '人设镜',
  hook: '钩子', cta: '行动召', brand: '品牌' };
const TYPE_COLOR = { narr: '#2f6b52', quote: '#3a5f6e', persona: '#6e3a5a',
  hook: '#3d4a63', cta: '#3d4a63', brand: '#4a3a1c' };
let SHOTS = [], STAMPED = false, ALIGNED = false, HAS_AUDIO = false;

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/stamp`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; STAMPED = d.stamped; ALIGNED = d.audio_aligned; HAS_AUDIO = d.has_audio;
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'stamp' });
  $('btn-v2').href = `/web/anim.html?book_id=${encodeURIComponent(B)}&ep_index=${E}`;
  renderAxis(); renderCounts(); renderWarns(); renderBlocks(); renderAudit(); renderGate();
}

function renderCounts() {
  const live = SHOTS.filter(s => !s.muted);
  const real = live.filter(s => s.real_s != null);
  const total = live.reduce((a, s) => a + (s.real_s || 0), 0);
  $('counts').innerHTML =
    `<span class="badge">${live.length} 槽</span>` +
    `<span class="badge ${real.length === live.length ? 'ok' : 'warn'}">实测 ${real.length}/${live.length}</span>` +
    `<span class="badge">${SHOTS.filter(s => s.muted).length} 屏弃</span>` +
    `<span class="badge">${Math.round(total)}s 实测</span>`;
}

function renderWarns() {
  const w = [];
  if (!HAS_AUDIO) w.push('无音频包 — ⑤ 页按槽重生成后再来');
  else if (!ALIGNED) w.push('音频块与未屏弃槽数不一致 — ⑤ 页有未对齐改动 (屏弃/编辑中), 刷新 ⑤ 页让它自愈后再来');
  const noImg = SHOTS.filter(s => !s.muted && !s.image).length;
  if (noImg) w.push(`${noImg} 槽尚无图 (⑥ K2 未完) — 盖章不拦, 但 ⑧ H3 前要补`);
  $('warns').innerHTML = w.map(x => `<div class="wrow">⚠ ${esc(x)}</div>`).join('');
}

function renderAxis() {
  if (!SHOTS.length) return;
  const live = SHOTS.filter(s => !s.muted);
  const total = live.reduce((a, s) => a + (s.real_s || s.est_s || 1), 0) || 1;
  const segs = []; let acc = 0;
  for (const s of SHOTS) {
    if (s.muted) continue;
    const w = +(((s.real_s || s.est_s || 1)) / total * 100).toFixed(2);
    const cards = (s.cards || []).length;
    segs.push({ l: +acc.toFixed(2), w,
                c: TYPE_COLOR[s.arc_type] || '#334155',
                title: `${s.sid} · ${TYPE_ZH[s.arc_type] || ''} · 实测 ${s.real_s ?? '?'}s (估 ${s.est_s}s)${cards ? ` · ${cards} 卡` : ''}` });
    acc += w;
  }
  timeline($('axis'), { arc: false, shots: segs,
    axisLabel: `${Math.round(total)}s 实测`,
    caption: '柱宽=实测时长 · 柱色=弧型 · 悬停看详情 (屏弃槽不占轴)' });
}

function renderBlocks() {
  const byArc = new Map();
  for (const s of SHOTS) {
    if (!byArc.has(s.arc_id)) byArc.set(s.arc_id, []);
    byArc.get(s.arc_id).push(s);
  }
  const out = [];
  for (const [aid, ss] of byArc) {
    const type = ss[0].arc_type || 'narr';
    const muted = ss.filter(s => s.muted).length;
    const real = ss.reduce((a, s) => a + (s.muted ? 0 : (s.real_s || 0)), 0);
    out.push(`<section class="arc" data-type="${esc(type)}">
      <header class="ah"><span class="aid">${esc(aid)}</span>
        <span class="atype">${esc(TYPE_ZH[type] || type)}</span>
        <span class="world">${ss.length} 槽 · ${Math.round(real)}s 实测</span>
        ${muted ? `<span class="muted-flag">🔇 ${muted} 屏弃</span>` : ''}</header>
      ${ss.map(s => `<div class="srow${s.muted ? ' mut' : ''}">
        <span class="sid">${esc(s.sid)}</span>
        <span class="dur">${s.muted ? '🔇 屏弃' : `估 ${s.est_s}s → <b>实 ${s.real_s ?? '?'}s</b>`}</span>
        <span class="sz">${esc(s.size || '')} ${esc(s.cam || '')}</span>
        <span class="cards" title="${esc((s.cards || []).map(c => c.kind + '「' + c.text + '」').join(' '))}">${(s.cards || []).map(c => `⚑${esc(c.kind)}`).join(' ') || ''}</span>
        <span class="img">${s.image ? '🖼' : '—'}</span>
      </div>`).join('')}
    </section>`);
  }
  $('blocks').innerHTML = out.join('');
}

function renderAudit() {
  const live = SHOTS.filter(s => !s.muted);
  const real = live.filter(s => s.real_s != null);
  const est = live.reduce((a, s) => a + (s.est_s || 0), 0);
  const act = live.reduce((a, s) => a + (s.real_s || 0), 0);
  const drift = est > 0 ? Math.abs(act - est) / est * 100 : 0;
  const rows = [
    ['槽 ↔ 音频块 1:1', `${real.length}/${live.length}`, real.length === live.length ? 'ok' : 'bad'],
    ['实测总时长', `${Math.round(act)}s (估 ${Math.round(est)}s, 漂移 ${drift.toFixed(1)}%)`,
     drift < 12 ? 'ok' : 'bad'],
    ['屏弃槽 (跳过)', `${SHOTS.length - live.length} 槽`, 'ok'],
  ];
  $('audit').innerHTML = rows.map(([k, v, c]) =>
    `<div class="audit-row ${c}"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

function renderGate() {
  const box = $('gatecard');
  const ok = ALIGNED && HAS_AUDIO;
  box.className = 'card gatebar' + (STAMPED ? ' on' : '');
  box.innerHTML = STAMPED
    ? `<div class="gt gold-t">◉ 已盖章</div><div class="note">实测秒数已灌入 + v2 字段已回填 — ⑧ H3 就绪 (待建); v2 工坊可接管</div>`
    : ok
      ? `<div class="gt">可盖章</div><div class="note">点底部「🔍 盖章」— 查表灌秒 + v2 回填, 秒级完成</div>`
      : `<div class="gt">🔒 前置未齐</div><div class="note">${!HAS_AUDIO ? '等 ⑤ TTS 音频包' : '等音频对齐 (刷 ⑤ 页自愈)'}</div>`;
  $('btn-stamp').disabled = !ok;
  $('stat').textContent = STAMPED ? '已盖章 — 重盖章随时可做 (改字/屏弃后刷新秒数)' : '待盖章';
}

$('btn-stamp').onclick = async () => {
  const b = $('btn-stamp'); b.disabled = true; b.textContent = '⏳ 盖章中…';
  try {
    const r = await fetch(`${BASE}/v3_stamp`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const j = await r.json();
    if (!r.ok) { toast('盖章失败: ' + (j.detail || '')); return; }
    // 同步相位走 job 池 — 短轮询
    for (let i = 0; i < 20; i++) {
      await new Promise(x => setTimeout(x, 1500));
      const d = await (await fetch(`/api/anim/job/${j.id}`)).json();
      if (d.status !== 'running') {
        if (d.status === 'completed') {
          toast(`✓ 盖章完成: ${d.result?.slots} 槽 ${d.result?.total_s}s · v2 已回填`);
        } else toast('盖章失败: ' + (d.error || d.status));
        break;
      }
    }
    await boot();
  } catch (e) { toast('盖章失败: ' + e.message); }
  b.disabled = false; b.textContent = '🔍 盖章 (灌秒 + v2 回填)';
};

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}
boot();
