// ② 立意页逻辑 (CSP: 全外置, 动态样式走 CSSOM)
import { rail } from './rail.js';
import { timeline } from './timeline.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const TYPE_ZH = { narr: '叙事弧', quote: '书证镜', persona: '回忆镜',
  hook: '钩子', cta: 'CTA', brand: '品牌' };
const MODE_ZH = { hook: '钩子', quote: '书证', comm: '评论', persona: '人设',
  cta: 'CTA', brand: '牌' };
const MODE_COLOR = { hook: '#22304a', quote: '#3a5f6e', comm: '#2f6b52',
  persona: '#6e3a5a', cta: '#22304a', brand: '#4a3a1c' };

let ARCS = [], LINES_OF_ARC = {}, dirty = new Set();

let GATE_OPEN = false;
function refreshDirty() {
  $('n-dirty').textContent = dirty.size;
  $('dirty').innerHTML = dirty.size
    ? `改稿 <b class="gold-t">${dirty.size} 处未保存</b>` : '改稿 0 处未保存';
  // 无改动 + 闸未开: 保存即「确认放行」(人闸语义显性, 实锤: 用户不知保存=放行)
  const sb = $('btn-save');
  if (!dirty.size && !GATE_OPEN) sb.textContent = '✅ 确认放行立意';
  else sb.innerHTML = `💾 保存立意 (<span id="n-dirty">${dirty.size}</span>)`;
}

async function refresh() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/concept`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  ARCS = d.arcs || [];
  LINES_OF_ARC = d.lines_of_arc || {};
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  const nMet = ARCS.filter(a => a.metaphor).length;
  $('counts').innerHTML =
    `<span class="badge">${ARCS.length} 弧</span>` +
    `<span class="badge ${nMet === ARCS.length ? 'ok' : 'warn'}">立意 ${nMet}/${ARCS.length}</span>` +
    (d.issues.length ? `<span class="badge bad">红痕 ${d.issues.length}</span>` : '');
  rail($('rail'), { line: st.line, gates: d.gates, active: 'concept' });
  renderIssues(d.issues || []);
  renderHints(d.hints || []);
  renderAxis();
  renderTable();
  renderGate(d.gates?.arcs, d.ready);
}

function renderIssues(list) {
  $('issues').innerHTML = list.length
    ? '<div class="tblwrap-issues">' + list.map(x => `<div class="issue">▎${esc(x)}</div>`).join('') + '</div>'
    : '';
}
function renderHints(list) {
  $('hints').innerHTML = list.length
    ? '<div class="tblwrap-issues">' + list.map(x => `<div class="hint">💡 ${esc(x)}</div>`).join('') + '</div>'
    : '';
}

/** 稿层(语态色) + 弧层(弧型色) 双带轴 — 宽度按各行字数占比 */
function renderAxis() {
  const rows = [];
  ARCS.forEach(a => (LINES_OF_ARC[a.arc_id] || []).forEach(l => rows.push({ ...l, aid: a.arc_id })));
  if (!rows.length) { $('axis').innerHTML = '<div class="cap">尚无弧 — 先跑「重跑立意」</div>'; return; }
  const chars = rows.map(r => Math.max(1, (r.text || '').length));
  const total = chars.reduce((x, y) => x + y, 0);
  const script = rows.map((r, i) => ({
    label: '', w: +(chars[i] / total * 100).toFixed(2),
    color: MODE_COLOR[r.mode] || '#334155',
  }));
  const bands = ARCS.map(a => {
    const len = chars.reduce((s, c, i) => s + (rows[i].aid === a.arc_id ? c : 0), 0);
    const cl = { narr: '#2f6b52', quote: '#3a5f6e', persona: '#6e3a5a' }[a.type] || '#22304a';
    return { label: a.world ? a.arc_id + ' ' + a.world.slice(0, 6) : a.arc_id,
             w: +(len / total * 100).toFixed(2), color: cl };
  });
  timeline($('axis'), {
    script, bands, arcGhost: false,
    axisLabel: `~${Math.round(ARCS.reduce((s, a) => s + (a.est_s || 0), 0))}s 估`,
    caption: '上层=稿层语态 (钩/书证/评论/人设/CTA) · 下层=弧 (叙事/书证镜/回忆镜) — 两带对齐即弧界落模块边界',
  });
}

function renderTable() {
  if (!ARCS.length) {
    $('tbody').innerHTML = '<tr><td colspan="8" class="muted">尚无弧 — 点下方「🎲 重跑立意」由语态切弧</td></tr>';
    return;
  }
  $('tbody').innerHTML = ARCS.map(a => {
    const isNarr = a.type !== 'hook' && a.type !== 'cta' && a.type !== 'brand';
    const world = isNarr
      ? `<span class="editable" contenteditable="true" data-aid="${a.arc_id}" data-k="world">${esc(a.world || '')}</span>`
      : '<span class="muted">— 非叙事位</span>';
    const meta = isNarr
      ? `<span class="editable" contenteditable="true" data-aid="${a.arc_id}" data-k="metaphor">${esc(a.metaphor || '')}</span>`
      : '<span class="muted">— 不分配立意</span>';
    return `<tr>
      <td class="mono">${esc(a.arc_id)}</td>
      <td><span class="ty ty-${esc(a.type)}">${esc(TYPE_ZH[a.type] || a.type)}</span></td>
      <td>${esc(MODE_ZH[a.mode] || a.mode)}</td>
      <td class="mono">L${a.no_start}–L${a.no_end}</td>
      <td>${world}</td><td>${meta}</td>
      <td class="mono">${a.line_count}</td><td class="mono">~${a.est_s}s</td></tr>`;
  }).join('');
}

function renderGate(open, ready) {
  GATE_OPEN = !!open;
  refreshDirty();
  const nx = $('btn-next');
  nx.disabled = !open;
  nx.title = open ? '进入 ③ 分镜文字' : (ready ? '先处置红痕 / 保存后放行' : '先跑立意');
  $('gate').className = 'gatebar' + (open ? '' : ' locked');
  $('gate').innerHTML = open
    ? '<b class="gold-t">◉ 立意放行</b> <span class="note">弧结构已锁 — 下一步 ③ 分镜文字 (零 TTS/GPU)</span>'
    : `<b class="note">🔒 立意闸未开</b> <span class="note">— ${ready ? '有红痕待处置' : '尚未切弧'}</span>`;
}

$('tbody').addEventListener('input', ev => {
  const t = ev.target;
  if (!t.dataset?.aid) return;
  dirty.add(t.dataset.aid + '.' + t.dataset.k);
  refreshDirty();
});

async function saveConcept() {
  const arcs = ARCS.map(a => ({
    arc_id: a.arc_id,
    world: document.querySelector(`[data-aid="${a.arc_id}"][data-k="world"]`)?.textContent ?? a.world,
    metaphor: document.querySelector(`[data-aid="${a.arc_id}"][data-k="metaphor"]`)?.textContent ?? a.metaphor,
  }));
  const r = await (await fetch(`${BASE}/concept/save`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ arcs }) })).json();
  dirty.clear(); refreshDirty();
  await refresh();
  if (r.gate) toast('✓ 立意放行 — 可进 ③');
  else if (r.issues?.length) toast('红痕未清: ' + r.issues[0]);
  return r;
}
$('btn-save').onclick = saveConcept;

$('btn-run').onclick = async () => {
  if (dirty.size && !confirm('有未保存立意改动, 继续会覆盖 — 先保存?')) return;
  if (dirty.size) await saveConcept();
  const r = await fetch(`${BASE}/v3_arcs`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  const j = await r.json();
  toast('立意生成中 (LLM 约 1 分钟)');
  setBusy(true);
  for (let i = 0; i < 60; i++) {
    await new Promise(x => setTimeout(x, 3000));
    let d; try { d = await (await fetch(`/api/anim/job/${j.id}`)).json(); } catch { continue; }
    if (d.status !== 'running') {
      setBusy(false);
      toast(d.status === 'completed' ? `✓ 切出 ${d.result?.arcs ?? '?'} 弧` : '立意失败: ' + (d.error || d.status));
      refresh(); return;
    }
  }
  setBusy(false);
};
function setBusy(on) {
  const b = $('btn-run'); b.disabled = on;
  b.textContent = on ? '⏳ 立意生成中…' : '🎲 重跑立意';
}

$('btn-next').onclick = async () => {
  if (dirty.size) await saveConcept();
  try {
    const r = await fetch('./shots.html', { method: 'HEAD' });
    if (r.ok) { location.href = './shots.html' + location.search; return; }
  } catch { /* 未上线 */ }
  toast('③ 分镜文字页建设中 (V3 批次) — 立意已保存');
};

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

refresh();
