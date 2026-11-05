// v3 分诊页逻辑 (外置: CSP 禁内联)
import { rail } from './rail.js';
const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m]));
const MODE_ZH = { hook:'钩子', quote:'书证', comm:'评论', persona:'人设', cta:'CTA',
  brand:'牌', card:'卡指令', img:'图指令', section:'段标', unknown:'未分诊' };
let LINES = [], dirty = new Set();

async function refresh() {
  const d = await (await fetch(`${BASE}/triage`)).json();
  LINES = d.lines || [];
  const st = await (await fetch(`${BASE}/state`)).json();
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  const nb = LINES.filter(l => l.flag?.level === 'block').length;
  const nw = LINES.filter(l => l.flag?.level === 'warn').length;
  const ns = LINES.filter(l => (l.suggs || []).length).length;
  $('counts').innerHTML =
    (nb ? `<span class="badge bad">🔴 ${nb}</span>` : '<span class="badge ok">🔴 0</span>') +
    (nw ? `<span class="badge warn">🟡 ${nw}</span>` : '') +
    (ns ? `<span class="badge">💡 ${ns}</span>` : '') +
    `<span class="badge">${LINES.length} 行</span>`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'triage' });
  renderGate(d.gates?.triage);
  renderList();
}

function renderGate(open) {
  $('gate').className = 'gatebar' + (open ? '' : ' locked');
  $('gate').innerHTML = open
    ? `<b style="color:var(--gold)">◉ 稿净</b> <span class="note">分诊闸已开 → </span>` +
      `<a href="./concept.html${location.search}">② 立意 →</a>`
    : `<b class="note">🔒 分诊闸未开</b> <span class="note">— 还有未处置的 🔴 拦截行</span>`;
}

function renderList() {
  $('doclist').innerHTML = LINES.map(l => {
    if (!l.text.trim()) return `<div class="docline blank"><span class="ln">${l.no}</span><span></span><span class="txt">${esc(l.text)||'&nbsp;'}</span><span></span></div>`;
    const flg = l.flag && l.disp !== 'cleaned' && l.disp !== 'converted';
    const sugg = (l.suggs || []).length;
    const cls = l.disp === 'routed' ? 'routed' : (flg ? 'flg' : (sugg ? 'sugg' : ''));
    const iss = l.flag && flg
      ? `<div class="iss">${l.flag.level === 'block' ? '🔴' : '🟡'} ${esc(l.flag.msg)} · 建议: ${esc(l.flag.sugg)}
           <span class="btns">${l.flag.level==='block' ? '<button class="act" data-a="clean" data-no="'+l.no+'">清洗</button><button class="act" data-a="conv" data-no="'+l.no+'">转为卡</button>' : '<button class="act" data-a="ok" data-no="'+l.no+'">知道了</button>'}</span></div>` : '';
    const sgs = (l.suggs || []).map(s =>
      `<div class="iss c">💡 ${esc(s.text)} <span class="btns"><button class="act" data-a="sugg" data-no="${l.no}" data-k="${s.kind}">采纳</button><button class="act" data-a="dismiss" data-no="${l.no}" data-k="${s.kind}">忽略</button></span></div>`).join('');
    const route = l.disp === 'routed' ? '<span class="note">→ 已路由</span>' : '';
    return `<div class="docline ${cls}" data-no="${l.no}">
      <span class="ln">${l.no}</span>
      <button class="tag" data-a="mode" data-no="${l.no}">${MODE_ZH[l.mode] || l.mode}</button>
      <span class="txt" contenteditable="true" data-no="${l.no}">${esc(l.text)}</span>
      <span>${route}</span>${iss}${sgs}</div>`;
  }).join('');
}

$('doclist').addEventListener('input', ev => {
  const no = +ev.target.dataset.no; dirty.add(no);
  $('n-dirty').textContent = dirty.size;
  $('dirty').innerHTML = `改稿 <b style="color:var(--gold)">${dirty.size} 处未保存</b>`;
});
$('doclist').addEventListener('click', async ev => {
  const b = ev.target.closest('button'); if (!b) return;
  const no = +b.dataset.no, a = b.dataset.a;
  const ln = LINES.find(l => l.no === no); if (!ln) return;
  if (a === 'mode') {
    const keys = Object.keys(MODE_ZH);
    ln.mode = keys[(keys.indexOf(ln.mode) + 1) % keys.length];
    b.textContent = MODE_ZH[ln.mode]; dirty.add(no);
    $('n-dirty').textContent = dirty.size;
  } else if (a === 'clean') { ln.disp = 'cleaned'; ln.text = ''; await save(); }
  else if (a === 'conv') { ln.disp = 'converted'; await save(); }
  else if (a === 'ok') { ln.flag = null; dirty.add(no); }
  else if (a === 'sugg') { ln.suggs = ln.suggs.filter(s => s.kind !== b.dataset.k); dirty.add(no); }
  else if (a === 'dismiss') { ln.suggs = ln.suggs.filter(s => s.kind !== b.dataset.k); dirty.add(no); }
});
$('btn-save').onclick = save;
async function save() {
  const lines = LINES.map(l => ({
    no: l.no,
    text: (document.querySelector(`.txt[data-no="${l.no}"]`)?.textContent ?? l.text).trim(),
    mode: l.mode, disp: l.disp }));
  const r = await (await fetch(`${BASE}/triage/save`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }) })).json();
  dirty.clear(); $('n-dirty').textContent = 0; $('dirty').textContent = '改稿 0 处未保存';
  await refresh();
  if (r.gate) toast('✓ 稿净 — 分诊闸已开');
}
$('btn-run').onclick = async () => {
  if (dirty.size && !confirm('有未保存改动, 先保存再跑?')) return;
  if (dirty.size) await save();
  const j = await (await fetch(`${BASE}/v3_triage`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
  toast(`${j.phase} 已启动 (${j.id.slice(0,8)}) — LLM 标注约 1 分钟`);
  poll(j.id);
};
async function poll(id) {
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const d = await (await fetch(`/api/anim/job/${id}`)).json();
    if (d.status !== 'running') { toast(`分诊完成: ${JSON.stringify(d.result)}`); refresh(); return; }
  }
}
function toast(m) { const t = document.createElement('div');
  t.textContent = m; t.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#14532d;color:#fff;padding:8px 18px;border-radius:9px;z-index:9';
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500); }
refresh();
