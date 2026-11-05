// ③ 分镜文字页逻辑 (CSP: 全外置, 动态样式走 CSSOM)
import { rail } from './rail.js';
import { timeline } from './timeline.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const ARC_COLOR = { narr: '#2f6b52', quote: '#3a5f6e', persona: '#6e3a5a',
  hook: '#22304a', cta: '#22304a', brand: '#4a3a1c' };
const STYLE_ZH = { list: '清单', quote: '金句', event: '事件', img: '配图' };

let SHOTS = [], CARDS = [], ISSUES = {}, dirty = new Set(), GATE_OPEN = false;

function refreshDirty() {
  const confirmMode = !dirty.size && !GATE_OPEN;
  $('save-label').textContent = confirmMode ? '✅ 确认放行分镜' : '💾 保存分镜';
  $('n-dirty').textContent = confirmMode ? '' : ` (${dirty.size})`;   // 常驻节点, 不重建 (防焦点/状态丢)
  $('dirty').innerHTML = dirty.size
    ? `改稿 <b class="gold-t">${dirty.size} 处未保存</b>` : '改稿 0 处未保存';
}

async function refresh() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/shots`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; CARDS = d.cards || []; ISSUES = d.issues || {};
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  const nIss = Object.entries(ISSUES)
    .filter(([k]) => k !== '弧级').reduce((a, [, v]) => a + v.length, 0);
  const nArc = (ISSUES['弧级'] || []).length;
  const nCard = CARDS.length;
  $('counts').innerHTML =
    `<span class="badge">${SHOTS.length} 镜</span>` +
    `<span class="badge">${nCard} 卡</span>` +
    `<span class="badge ${nIss ? 'bad' : 'ok'}">质检 ${nIss ? nIss : '✓'}</span>` +
    (nArc ? `<span class="badge warn">弧级 ${nArc}</span>` : '') +
    `<span class="badge">${Math.round(SHOTS.reduce((s, x) => s + (x.est_s || 0), 0))}s 估</span>`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'shots' });
  renderIssues(d.issues || {}, d.warnings || []);
  renderCardsHint(nCard);
  renderAxis();
  renderGrid();
  renderGate(d.gates?.shots, d.ready);
}

function renderCardsHint(nCard) {
  const box = $('cards-hint');
  if (nCard > 0) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="hint">💡 本集还没有大字卡 — 卡来自 ① 分诊页的建议 (金句/清单/配图/书证/事件)'
    + '<button class="iconbtn" id="btn-autocards" title="把分诊给出的全部建议一次建成卡">一键建卡</button></div>';
  const b = document.getElementById('btn-autocards');
  if (b) b.onclick = async () => {
    const r = await (await fetch(`${BASE}/shots/autocards`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    toast(`新建 ${r.added} 张卡 (共 ${r.cards})`);
    await refresh();
  };
}

function renderIssues(issues, warns) {
  const shotItems = [], arcItems = [];
  for (const [key, list] of Object.entries(issues)) {
    const bucket = key === '弧级' ? arcItems : shotItems;
    for (const x of list) bucket.push(`<div class="issue">▎${esc(x)}</div>`);
  }
  const head = shotItems.length
    ? `<div class="issue-sum">镜级 ${shotItems.length} 项 (要改)</div>` : '';
  const arcHead = arcItems.length
    ? `<div class="hint">弧级 ${arcItems.length} 项 (策略提醒 — 宪法配额, 人检可保留)</div>` : '';
  const fixBtn = shotItems.length
    ? '<div class="fixrow"><button class="go" id="btn-autofix" title="文字载体未申报 → 自动申报(内容取该镜卡/口播); 终态词需人工改">🔧 一键合规</button>'
      + '<span class="note">只处理「载体未申报」; 终态词属语义改写, 需手改</span></div>'
    : '';
  $('issues').innerHTML = head + shotItems.join('') + fixBtn + arcHead + arcItems.join('');
  const af = document.getElementById('btn-autofix');
  if (af) af.onclick = async () => {
    const r = await (await fetch(`${BASE}/shots/fix`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    toast(`已自动申报 ${r.fixed} 镜` + (r.manual.length ? ` · ${r.manual.length} 项需手改` : ''));
    await refresh();
    if (r.manual.length) console.info('需人工处理:', r.manual);
  };
  $('warns').innerHTML = (warns || []).map(w => `<div class="hint">💡 ${esc(w)}</div>`).join('');
}

function renderAxis() {
  if (!SHOTS.length) { $('axis').innerHTML = '<div class="cap">尚无镜 — 先跑「重跑分镜」</div>'; return; }
  const total = SHOTS.reduce((s, x) => s + (x.est_s || 1), 0) || 1;
  const shots = [];
  let acc = 0;
  for (const s of SHOTS) {
    const w = +((s.est_s || 1) / total * 100).toFixed(2);
    shots.push({ l: +acc.toFixed(2), w,
                 c: ISSUES[s.sid] ? '#8a2e2e' : (ARC_COLOR[s.arc_type] || '#334155'),
                 title: `${s.sid} ${s.arc_id}${ISSUES[s.sid] ? ' (质检 ' + ISSUES[s.sid].length + ')' : ''}` });
    acc += w;
  }
  timeline($('axis'), {
    arc: false, shots,
    axisLabel: `~${Math.round(total)}s 估`,
    caption: '镜段按弧型着色 (叙事绿/书证青/回忆紫/非叙事灰) · 质检有问题的镜红标',
  });
}

function cardsOf(sid) {
  return CARDS.filter(c => c.sid === sid)
    .map(c => `<span class="kard kard-${esc(c.style)}">${esc(STYLE_ZH[c.style] || c.style)}「${esc(c.text)}」</span>`)
    .join(' ');
}

function renderGrid() {
  if (!SHOTS.length) {
    $('grid').innerHTML = '<div class="row"><div class="muted">尚无镜 — 点下方「🎨 重跑分镜」由立意弧切镜</div></div>';
    return;
  }
  const rows = [`<div class="row hd"><div>弧</div><div>镜</div><div>画面句 · 口播 (写什么/说什么)</div>
    <div>大字卡</div><div>时长</div><div>质检</div></div>`];
  for (const s of SHOTS) {
    const iss = ISSUES[s.sid] || [];
    rows.push(`<div class="row shot">
      <div class="mono muted">${esc(s.arc_id)}</div>
      <div class="mono">${esc(s.sid)}</div>
      <div>${s.source === 'brand_card'
          ? '<span class="src src-brand" title="品牌冻结骨架 (登山客三拍+圣经色板), LLM 零参与">🔒 品牌卡</span>'
          : (s.arc_type === 'hook' || s.arc_type === 'cta'
              ? '<span class="src src-const" title="按特区宪法生成 (钩子/行动召既定画面语言)">📐 宪法</span>' : '')}
        <span class="editable" contenteditable="true" data-sid="${s.sid}" data-k="keyframe_zh">${esc(s.keyframe_zh || '')}</span>
        <button class="iconbtn" data-act="reroll" data-sid="${s.sid}" title="重写这一镜的画面句">↻</button></div>
      <div>${cardsOf(s.sid) || '<span class="muted">—</span>'}</div>
      <div class="mono muted">~${s.est_s}s</div>
      <div class="qc ${iss.length ? '' : 'ok'}">${iss.length ? iss.length + ' 项' : '✓'}</div>
    </div>`);
    for (const sen of (s.sents || [])) {
      rows.push(`<div class="row sent">
        <div></div><div></div>
        <div class="stext">${esc(sen.text)}</div>
        <div></div><div></div><div></div></div>`);
    }
  }
  $('grid').innerHTML = rows.join('');
}

function renderGate(open, ready) {
  GATE_OPEN = !!open;
  refreshDirty();
  const nx = $('btn-next');
  nx.disabled = !open;
  nx.title = open ? '进入 ④ 导演 (景别/运镜/光影)' : (ready ? '先处置质检问题' : '先跑分镜');
  $('gate').className = 'gatebar' + (open ? '' : ' locked');
  $('gate').innerHTML = open
    ? '<b class="gold-t">◉ 分镜放行</b> <span class="note">镜/画面句/卡内容已锁 — 下一步 ④ 导演 (零 GPU)</span>'
    : `<b class="note">🔒 分镜闸未开</b> <span class="note">— ${ready ? '有质检问题待处置' : '尚未切镜'}</span>`;
}

$('grid').addEventListener('input', ev => {
  const sid = ev.target.dataset?.sid;
  if (!sid) return;
  dirty.add(sid);
  refreshDirty();
});

$('grid').addEventListener('click', async ev => {
  const b = ev.target.closest('button'); if (!b) return;
  if (b.dataset.act === 'reroll') {
    const sid = b.dataset.sid;
    const s = SHOTS.find(x => x.sid === sid);
    if (!s) return;
    toast('单镜重写：先手改画面句，或「重跑分镜」由 LLM 重出全片 (零 GPU)');
  }
});

async function saveShots() {
  document.querySelectorAll('[data-k="keyframe_zh"]').forEach(el => {
    const s = SHOTS.find(x => x.sid === el.dataset.sid);
    if (s && el.textContent !== (s.keyframe_zh || '')) { s.keyframe_zh = el.textContent; dirty.add(s.sid); }
  });
  const r = await (await fetch(`${BASE}/shots/save`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shots: SHOTS.map(s => ({
      sid: s.sid, keyframe_zh: s.keyframe_zh,
      in_image_text: s.in_image_text || [], text_layer: s.text_layer || [] })),
      cards: CARDS }) })).json();
  dirty.clear(); refreshDirty();
  await refresh();
  if (r.gate) toast('✓ 分镜放行 — 可进 ④');
  else if (Object.keys(r.issues || {}).length) toast('质检未过: ' + Object.keys(r.issues)[0]);
  return r;
}
$('btn-save').onclick = saveShots;

$('btn-run').onclick = async () => {
  if (dirty.size && !confirm('有未保存改动，继续会覆盖 — 先保存?')) return;
  if (dirty.size) await saveShots();
  const r = await fetch(`${BASE}/v3_shots`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  const j = await r.json();
  setBusy(true); toast('分镜生成中 (LLM 写画面句)');
  for (let i = 0; i < 80; i++) {
    await new Promise(x => setTimeout(x, 3000));
    let d; try { d = await (await fetch(`/api/anim/job/${j.id}`)).json(); } catch { continue; }
    if (d.status !== 'running') {
      setBusy(false);
      toast(d.status === 'completed'
        ? `✓ ${d.result?.shots ?? '?'} 镜 (质检 ${d.result?.issues ?? 0})`
        : '分镜失败: ' + (d.error || d.status));
      refresh(); return;
    }
  }
  setBusy(false);
};
function setBusy(on) {
  const b = $('btn-run'); b.disabled = on;
  b.textContent = on ? '⏳ 分镜生成中…' : '🎨 重跑分镜';
}

$('btn-next').onclick = async () => {
  if (dirty.size) await saveShots();
  try {
    const r = await fetch('./director.html', { method: 'HEAD' });
    if (r.ok) { location.href = './director.html' + location.search; return; }
  } catch { /* 未上线 */ }
  toast('④ 导演页建设中 (V4 批次) — 分镜已保存');
};

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

refresh();
