// ② 立意页逻辑 (CSP: 全外置, 动态样式走 CSSOM)
import { rail } from './rail.js';
import { timeline } from './timeline.js';
import { createJobRunner } from './job.js';
const runner = createJobRunner({
  onFinished: job => {
    if (job.status === 'completed') toast(`✓ 切出 ${job.result?.arcs ?? '?'} 弧`);
    else if (job.status !== 'unknown') toast('立意结束: ' + (job.status || ''));
    refresh();
  },
  onBusy: busy => { $('btn-run').disabled = busy; },
});

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const TYPE_ZH = { narr: '叙事弧', quote: '书证镜', persona: '人设镜',
  hook: '钩子', cta: '行动召', brand: '品牌' };
const MODE_ZH = { hook: '钩子', quote: '书证', comm: '评论', persona: '人设',
  cta: '行动召', brand: '牌' };
const MODE_COLOR = { hook: '#22304a', quote: '#3a5f6e', comm: '#2f6b52',
  persona: '#6e3a5a', cta: '#22304a', brand: '#4a3a1c' };

let ARCS = [], LINES_OF_ARC = {}, dirty = new Set();

let GATE_OPEN = false;
function refreshDirty() {
  const confirmMode = !dirty.size && !GATE_OPEN;
  $('save-label').textContent = confirmMode ? '✅ 确认放行立意' : '💾 保存立意';
  $('n-dirty').textContent = confirmMode ? '' : ` (${dirty.size})`;   // 常驻节点, 不重建
  $('dirty').innerHTML = dirty.size
    ? `改稿 <b class="gold-t">${dirty.size} 处未保存</b>` : '改稿 0 处未保存';
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
  if (st.running_job) runner.attach(st.running_job);
  renderIssues(d.issues || []);
  renderHints(d.hints || []);
  renderAxis();
  renderTable();
  renderGate(d.gates?.arcs, d.ready);
}

function renderIssues(list) {
  if (!list.length) { $('issues').innerHTML = ''; return; }
  const fix = list.some(x => x.includes('跨语态'))
    ? '<button class="go" id="btn-resplit" title="按语态变化点把跨语态的弧拆回多个弧 (保留立意文字)">🔧 按语态重切</button>'
    : '';
  $('issues').innerHTML = '<div class="tblwrap-issues">'
    + list.map(x => `<div class="issue">▎${esc(x)}</div>`).join('')
    + (fix ? `<div class="fixrow">${fix}</div>` : '') + '</div>';
  const rb = document.getElementById('btn-resplit');
  if (rb) rb.onclick = async () => {
    const r = await (await fetch(`${BASE}/concept/resplit`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    toast(`重切完成: ${r.arcs_before} → ${r.arcs_after} 弧 (剩余红痕 ${r.issues.length})`);
    await refresh();
  };
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
    caption: '上层=稿层语态 (钩子/书证/评论/人设/行动召) · 下层=弧 (叙事/书证镜/人设镜) — 两带对齐即弧界落模块边界',
  });
}

/** 批3 vocab: 词表单元格式 "zh=en、zh=en" ↔ [{zh,en}] (与 ② 产出同尺) */
const vocabTxt = vs => (vs || []).map(v => `${v.zh}=${v.en}`).join('、');
const parseVocab = txt => String(txt || '').split(/[、;；\n]/)
  .map(p => p.trim()).filter(Boolean).map(p => {
    const i = p.indexOf('=');
    return i > 0 ? { zh: p.slice(0, i).trim(), en: p.slice(i + 1).trim() } : null;
  }).filter(v => v && v.zh && v.en);

function renderTable() {
  if (!ARCS.length) {
    $('tbody').innerHTML = '<tr><td colspan="9" class="muted">尚无弧 — 点下方「🎲 重跑立意」由语态切弧</td></tr>';
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
    // 批3: 实体词表列 (非叙事位不可编, 空表=零执法)
    const vocab = isNarr
      ? `<span class="editable" contenteditable="true" data-aid="${a.arc_id}" data-k="vocab"
            title="本弧实体统一词 (zh=en 对, 顿号分隔, ≤10 条): ③画面句/④英译用原词, ⑥ 跨字段闸执法">${esc(vocabTxt(a.vocab))}</span>`
      : '<span class="muted">—</span>';
    const i = ARCS.indexOf(a);
    return `<tr>
      <td class="mono">${esc(a.arc_id)}</td>
      <td><button class="ty ty-${esc(a.type)} tybtn" data-act="type" data-aid="${a.arc_id}"
            title="点改弧型 (叙事/书证/人设/钩子/行动召/品牌)">${esc(TYPE_ZH[a.type] || a.type)}</button></td>
      <td>${esc(MODE_ZH[a.mode] || a.mode)}</td>
      <td class="mono">L${a.no_start}–L${a.no_end}
        ${i > 0 ? `<button class="iconbtn" data-act="merge" data-aid="${a.arc_id}"
          title="并入上一弧 (两弧行区间合并成一弧)">⬆合并</button>` : ''}</td>
      <td>${world}</td><td>${meta}</td><td>${vocab}</td>
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

const TYPE_ORDER = ['narr', 'quote', 'persona', 'hook', 'cta', 'brand'];
$('tbody').addEventListener('click', ev => {
  const b = ev.target.closest('button'); if (!b) return;
  const aid = b.dataset.aid;
  const i = ARCS.findIndex(a => a.arc_id === aid);
  if (i < 0) return;
  if (b.dataset.act === 'type') {
    const cur = ARCS[i].type;
    ARCS[i].type = TYPE_ORDER[(TYPE_ORDER.indexOf(cur) + 1) % TYPE_ORDER.length];
    dirty.add(aid + '.type');
    renderTable(); renderAxis(); refreshDirty();
  } else if (b.dataset.act === 'merge' && i > 0) {
    const prev = ARCS[i - 1], cur = ARCS[i];
    if (prev.mode !== cur.mode) {          // 跨语态合并必破执法 (实锤: A8 两段书证夹评论)
      toast(`不能合并: ${prev.arc_id}(${MODE_ZH[prev.mode] || prev.mode}) 与 `
            + `${cur.arc_id}(${MODE_ZH[cur.mode] || cur.mode}) 语态不同 — 弧内必须单一语态`);
      return;
    }
    prev.no_end = cur.no_end;
    prev.line_count = (prev.line_count || 0) + (cur.line_count || 0);
    prev.est_s = +((prev.est_s || 0) + (cur.est_s || 0)).toFixed(1);
    const pool = Object.values(LINES_OF_ARC).flat().sort((x, y) => x.no - y.no);
    ARCS.splice(i, 1);                                  // 删掉被并弧
    ARCS.forEach((a, k) => { a.arc_id = 'A' + (k + 1); });
    // 重编号后按行区间重新分配句 (键也随编号更新)
    const remap = {};
    ARCS.forEach(a => {
      remap[a.arc_id] = pool.filter(l => l.no >= a.no_start && l.no <= a.no_end);
    });
    LINES_OF_ARC = remap;
    dirty.add('merge');
    renderTable(); renderAxis(); refreshDirty();
  }
});

$('tbody').addEventListener('input', ev => {
  const t = ev.target;
  if (!t.dataset?.aid) return;
  dirty.add(t.dataset.aid + '.' + t.dataset.k);
  refreshDirty();
});

async function saveConcept() {
  const isNarrRow = a => a.type !== 'hook' && a.type !== 'cta' && a.type !== 'brand';
  const arcs = ARCS.map(a => ({
    arc_id: a.arc_id, type: a.type, no_start: a.no_start, no_end: a.no_end,
    world: document.querySelector(`[data-aid="${a.arc_id}"][data-k="world"]`)?.textContent ?? a.world,
    metaphor: document.querySelector(`[data-aid="${a.arc_id}"][data-k="metaphor"]`)?.textContent ?? a.metaphor,
    // 批3: 叙事位词表可编 (非叙事位不发 → 后端原值保留)
    ...(isNarrRow(a) ? { vocab: parseVocab(
      document.querySelector(`[data-aid="${a.arc_id}"][data-k="vocab"]`)?.textContent ?? '') } : {}),
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
  runner.attach(await r.json());
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
