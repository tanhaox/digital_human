// v3 分诊页逻辑 (外置: CSP 禁内联)
import { rail } from './rail.js';
const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m]));
const MODE_ZH = { hook:'钩子', quote:'书证', comm:'评论', persona:'人设', cta:'行动召',
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
  renderGate(d.gates?.triage, d.triaged);
  $('btn-run').textContent = d.triaged ? '🩺 重新分诊' : '🩺 开始分诊';
  renderList();
  // 页面重载时若有分诊 job 在跑 → 续接轮询 (进度可见)
  if (st.running_job && st.running_job.phase === 'v3_triage') poll(st.running_job.id);
}

function renderGate(open, triaged) {
  const nx = $('btn-next');
  if (nx) {
    nx.disabled = !open;
    nx.title = open ? '进入 ② 立意 (语态驱动切弧)'
                    : (triaged ? '还有未处置的 🔴 拦截行' : '先完成分诊');
  }
  $('gate').className = 'gatebar' + (open ? '' : ' locked');
  $('gate').innerHTML = open
    ? `<b class="gold-t">◉ 稿净</b> <span class="note">分诊闸已开 → </span>` +
      `<a href="./concept.html${location.search}">② 立意 →</a>`
    : (triaged
        ? `<b class="note">🔒 分诊闸未开</b> <span class="note">— 还有未处置的 🔴 拦截行</span>`
        : `<b class="note">🔒 未分诊</b> <span class="note">— 先点「开始分诊」</span>`);
}

let RUNNING = false, _runT0 = 0;
function setRunning(on) {
  RUNNING = on;
  const b = $('btn-run');
  b.disabled = on;
  b.textContent = on ? '⏳ 分诊中…' : (LINES.length ? '🩺 重新分诊' : '🩺 开始分诊');
  if (on && !LINES.length) {
    _runT0 = Date.now();
    const tick = setInterval(() => {
      if (!RUNNING || document.hidden) return;
      const s = Math.round((Date.now() - _runT0) / 1000);
      const h = document.getElementById('run-state');
      if (h) h.textContent = `⏳ 分诊中… ${s}s (LLM 标注, 静默属正常)`;
    }, 1000);
    setTimeout(() => clearInterval(tick), 300000);
  }
}

function renderList() {
  if (!LINES.length) {   // 空态 (未分诊): 给明确首跑入口, 不留死胡同
    $('doclist').innerHTML =
      '<div class="hero">' +
      (RUNNING ? '<div id="run-state" class="hero-t gold">⏳ 分诊中…</div>'
               : '<div class="hero-t">本集尚未分诊</div>' +
                 '<div class="note hero-s">载入文稿 → 逐行语态标注 + 病检 + 五类建议 (一次 LLM, 约 1 分钟)</div>' +
                 '<button class="go hero-b" id="hero-run">🩺 开始分诊</button>') +
      '</div>';
    const hero = document.getElementById('hero-run');
    if (hero) hero.onclick = () => $('btn-run').click();
    return;
  }
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

/** 重渲染前把行内编辑现值回写 LINES — 防 renderList 吞掉未保存改动 */
function syncText() {
  document.querySelectorAll('.txt[data-no]').forEach(el => {
    const ln = LINES.find(l => l.no === +el.dataset.no);
    if (ln && el.textContent !== ln.text) { ln.text = el.textContent; dirty.add(ln.no); }
  });
}
function refreshDirty() {
  $('n-dirty').textContent = dirty.size;
  $('dirty').innerHTML = dirty.size
    ? `改稿 <b class="gold-t">${dirty.size} 处未保存</b>` : '改稿 0 处未保存';
}

$('doclist').addEventListener('input', ev => {
  if (!ev.target.dataset.no) return;
  dirty.add(+ev.target.dataset.no);
  refreshDirty();
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
  else if (a === 'ok') {
    syncText(); ln.flag = null; dirty.add(no); renderList(); refreshDirty();
  } else if (a === 'sugg' || a === 'dismiss') {
    // 采纳/忽略: 该条建议从行上移除 (采纳=已记入 v3.cards 待 ③ 消费, 忽略=丢弃)
    syncText();
    ln.suggs = (ln.suggs || []).filter(x => x.kind !== b.dataset.k);
    if (a === 'sugg') (ln.adopted = ln.adopted || []).push(b.dataset.k);
    dirty.add(no);
    renderList(); refreshDirty();
  }
});
$('btn-save').onclick = save;
async function save() {
  syncText();
  const lines = LINES.map(l => ({
    no: l.no, text: l.text.trim(), mode: l.mode, disp: l.disp,
    adopted: l.adopted || [] }));
  const r = await (await fetch(`${BASE}/triage/save`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }) })).json();
  dirty.clear(); $('n-dirty').textContent = 0; $('dirty').textContent = '改稿 0 处未保存';
  await refresh();
  if (r.gate) toast('✓ 稿净 — 分诊闸已开');
}
$('btn-next').onclick = async () => {
  if (dirty.size) await save();          // 有未保存改动先落盘 (防丢)
  const next = './concept.html' + location.search;
  try {
    const r = await fetch('./concept.html', { method: 'HEAD' });
    if (r.ok) { location.href = next; return; }
  } catch { /* 探测失败按未上线处理 */ }
  toast('② 立意页建设中 (V2 批次) — 分诊结果已保存, 进度见 docs/improvements/进行中-20261105-分镜续跑升级.md');
};

$('btn-run').onclick = async () => {
  if (RUNNING) return;
  if (dirty.size && !confirm('有未保存改动, 先保存再跑?')) return;
  if (dirty.size) await save();
  const r = await fetch(`${BASE}/v3_triage`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail, true); return; }
  const j = await r.json();
  setRunning(true);
  if (LINES.length) toast('分诊中 (约 1 分钟) — 完成自动刷新');
  poll(j.id);
};
async function poll(id) {
  setRunning(true);
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 3000));
    let d; try { d = await (await fetch(`/api/anim/job/${id}`)).json(); }
    catch { continue; }
    if (d.status !== 'running') {
      setRunning(false);
      if (d.status === 'completed') toast('✓ 分诊完成');
      else toast('分诊失败: ' + (d.error || d.status));
      refresh(); return;
    }
  }
  setRunning(false);
}
function toast(m) { const t = document.createElement('div');
  t.textContent = m; t.className = 'toast';
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500); }
refresh();

// autostart (无头/CI 验证): ?autostart=1 载入即点开始分诊
if (new URLSearchParams(location.search).get('autostart') === '1') {
  const t = setInterval(() => {
    const b = document.getElementById('btn-run');
    if (b && !b.disabled && !RUNNING) { clearInterval(t); b.click(); }
  }, 800);
  setTimeout(() => clearInterval(t), 30000);
}
