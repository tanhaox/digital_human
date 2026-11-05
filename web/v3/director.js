// ④ 导演页 (UI 定稿批: 真数据 + 空字段; 生成/保存下一批接入)
import { rail } from './rail.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const SIZES = ['全景', '中景', '近景', '特写'];
const SIZE_LV = { 全景: 4, 中景: 3, 近景: 2, 特写: 1 };
const CAMS = ['固定', '缓推', '拉近', '拉远', '左摇', '右摇', '上摇', '下摇'];
const AMPS = ['小', '中', '大'];
const SPEEDS = ['慢', '中', '快'];
const TYPE_ZH = { narr: '叙事弧', quote: '书证镜', persona: '人设镜',
  hook: '钩子', cta: '行动召', brand: '品牌' };

let SHOTS = [], ARCS = [], STATE = {}, DIRTY = new Set(), GATES = {};

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/shots`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; ARCS = d.arcs || []; GATES = d.gates || {};
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  for (const s of SHOTS) {
    const saved = s.direct || {};
    STATE[s.sid] = {
      size: saved.size || s.size_guess || '中景',
      cam: { kind: (saved.cam && saved.cam.kind) || '固定',
             amp: (saved.cam && saved.cam.amp) || '中',
             speed: (saved.cam && saved.cam.speed) || '慢' },
      motion: saved.motion || '', light: saved.light || '',
      filled: !!s.direct,
    };
  }
  rail($('rail'), { line: st.line, gates: GATES, active: 'direct' });
  renderBlocks(); renderRhythm(); renderCounts(); renderSide();
}

/* ── 顶栏 ─────────────────────────────────────────────── */
function renderCounts() {
  const filled = SHOTS.filter(s => STATE[s.sid].filled).length;
  $('counts').innerHTML =
    `<span class="badge">${SHOTS.length} 镜</span>` +
    `<span class="badge ${filled ? 'ok' : ''}">已填 ${filled}</span>` +
    `<span class="badge">${Math.round(SHOTS.reduce((a, s) => a + (s.est_s || 0), 0))}s</span>`;
}

/* ── 景别节奏轴 (每镜一柱, 高度=景别) ─────────────────── */
function renderRhythm() {
  const box = $('rhythm');
  box.innerHTML = '';
  SHOTS.forEach(s => {
    const st = STATE[s.sid];
    const b = document.createElement('div');
    b.className = 'bar s' + SIZE_LV[st.size];
    b.style.height = (28 + SIZE_LV[st.size] * 12) + 'px';
    b.title = `${s.sid} · ${st.size}`;
    b.onclick = () => {
      const el = document.querySelector(`[data-sid="${s.sid}"]`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    box.appendChild(b);
  });
  const q = quotas();
  $('rhycap').textContent =
    `一镜一柱 (柱高=景别) · 全景 ${q.big}/${q.needBig} 需 · 特写 ${q.micro}/${q.maxMicro} 上限 · 相邻同景别 ${q.sameAdj} 处`;
}

/* ── 主区 ─────────────────────────────────────────────── */
function arcOf(aid) { return ARCS.find(a => a.arc_id === aid) || {}; }

function renderBlocks() {
  const byArc = new Map();
  for (const s of SHOTS) {
    if (!byArc.has(s.arc_id)) byArc.set(s.arc_id, []);
    byArc.get(s.arc_id).push(s);
  }
  const out = [];
  for (const [aid, shots] of byArc) {
    const a = arcOf(aid);
    const type = a.type || shots[0].arc_type || 'narr';
    const world = String(a.world || '').trim();
    const wCell = ['narr', 'persona', 'quote'].includes(type)
      ? `<span class="world${world ? '' : ' na'}" title="${esc(world || '视觉世界为空')}">${world ? esc(world) : '⚠ 缺视觉世界'}</span>`
      : '<span class="world na">— 非叙事位</span>';
    out.push(`<section class="arc" data-type="${esc(type)}">
      <header class="ah">
        <span class="aid">${esc(aid)}</span>
        <span class="atype">${esc(TYPE_ZH[type] || type)}</span>
        ${a.no_start ? `<span class="lspan">L${a.no_start}–${a.no_end}</span>` : ''}
        ${wCell}
        <span class="est">${shots.length} 镜</span>
      </header>
      ${shots.map(rowHTML).join('')}
    </section>`);
  }
  $('blocks').innerHTML = out.join('');
  bindRows();
}

function opts(list, cur) {
  return list.map(x => `<option value="${esc(x)}"${x === cur ? ' selected' : ''}>${esc(x)}</option>`).join('');
}

function rowHTML(s) {
  const st = STATE[s.sid];
  const fixed = st.cam.kind === '固定';
  return `<div class="row" data-sid="${esc(s.sid)}">
    <div class="r1">
      <span class="sid">${esc(s.sid)}</span>
      <span class="dur">~${s.est_s}s</span>
      ${st.filled ? '' : '<span class="warn-dot" title="尚未生成导演参数"></span>'}
      <select class="sel-size" data-k="size" title="景别">${opts(SIZES, st.size)}</select>
      <select class="sel-cam" data-k="cam.kind" title="运镜型">${opts(CAMS, st.cam.kind)}</select>
      <select class="sel-amp" data-k="cam.amp" title="运镜幅度"${fixed ? ' disabled' : ''}>${opts(AMPS, st.cam.amp)}</select>
      <select class="sel-speed" data-k="cam.speed" title="运镜速度"${fixed ? ' disabled' : ''}>${opts(SPEEDS, st.cam.speed)}</select>
      <span class="flex1"></span>
      <button class="kf-btn" data-act="kf" title="展开画面句">画面句 ▸</button>
    </div>
    <div class="r2">
      <label>主体运动</label><input type="text" data-k="motion" value="${esc(st.motion)}" placeholder="只动画面句已有的实体">
      <label>光影</label><input type="text" data-k="light" value="${esc(st.light)}" placeholder="光源方向/色温, 与相邻镜连续">
    </div>
    <div class="kf">${esc(s.keyframe_zh || '')}</div>
  </div>`;
}

function bindRows() {
  $('blocks').querySelectorAll('.row').forEach(row => {
    const sid = row.dataset.sid;
    row.querySelectorAll('[data-k]').forEach(el => {
      el.addEventListener('change', () => {
        const k = el.dataset.k;
        if (k === 'size') STATE[sid].size = el.value;
        else if (k.startsWith('cam.')) STATE[sid].cam[k.slice(4)] = el.value;
        else STATE[sid][k] = el.value;
        STATE[sid].filled = true;
        DIRTY.add(sid);
        if (k === 'cam.kind') refreshRow(row, sid);
        renderRhythm(); renderCounts(); renderSide(); refreshDirty();
      });
      el.addEventListener('input', () => {
        const k = el.dataset.k;
        if (k === 'motion' || k === 'light') {
          STATE[sid][k] = el.value; DIRTY.add(sid); refreshDirty();
        }
      });
    });
    const btn = row.querySelector('[data-act="kf"]');
    const kf = row.querySelector('.kf');
    btn.onclick = () => {
      kf.classList.toggle('open');
      btn.textContent = kf.classList.contains('open') ? '收起 ▴' : '画面句 ▸';
    };
  });
}

function refreshRow(row, sid) {
  const fixed = STATE[sid].cam.kind === '固定';
  row.querySelector('.sel-amp').disabled = fixed;
  row.querySelector('.sel-speed').disabled = fixed;
}

/* ── 配额 ─────────────────────────────────────────────── */
function quotas() {
  const n = SHOTS.length || 1;
  let big = 0, micro = 0, sameAdj = 0, prev = null;
  for (const s of SHOTS) {
    const sz = STATE[s.sid].size;
    if (sz === '全景') big++;
    if (sz === '特写') micro++;
    if (prev === sz) sameAdj++;
    prev = sz;
  }
  return { n, big, micro, sameAdj,
           needBig: Math.ceil(n / 4), maxMicro: Math.ceil(n / 2) };
}

function renderSide() {
  const q = quotas();
  const rows = [
    ['全景 ≥⌈n/4⌉', `${q.big} / ${q.needBig}`, q.big >= q.needBig ? 'ok' : 'bad'],
    ['特写 ≤⌈n/2⌉', `${q.micro} / ${q.maxMicro}`, q.micro <= q.maxMicro ? 'ok' : 'bad'],
    ['相邻镜同景别', `${q.sameAdj} 处`, q.sameAdj === 0 ? 'ok' : 'warn'],
  ];
  $('quotas').innerHTML = rows.map(([k, v, cls]) =>
    `<div class="qrow ${cls}"><span>${k}</span><b>${v}</b></div>`).join('');
  $('dissues').innerHTML = q.sameAdj
    ? `<div class="ditem">相邻镜景别相同 ${q.sameAdj} 处 — 每镜与上一镜至少换一档</div>` : '';

  const ok = q.big >= q.needBig && q.micro <= q.maxMicro && q.sameAdj === 0;
  const box = $('gatecard');
  box.className = 'card gatebar' + (ok ? ' on' : '');
  box.innerHTML = ok
    ? '<div class="gt gold-t">◉ 导演参数达标</div><div class="note">生成/保存接入后可放行 ⑤</div>'
    : '<div class="gt">🔒 导演未完成</div><div class="note">先调整景别节奏 (右栏三项全绿)</div>';
}

function refreshDirty() {
  $('dirty').innerHTML = DIRTY.size
    ? `改稿 <b class="gold-t">${DIRTY.size} 处未保存</b>` : '改稿 0 处未保存';
  $('n-dirty').textContent = DIRTY.size ? ` (${DIRTY.size})` : '';
}

boot();
