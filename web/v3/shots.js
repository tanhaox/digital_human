// ③ 分镜文字页 (CSP: 全外置, 动态样式走 CSSOM)
// 布局: 左 = 弧分块镜头表 (读稿顺序), 右 = 总审栏 (闸态/质检/弧级提醒), 底 = 常驻操作。
import { rail } from './rail.js';
import { timeline } from './timeline.js';
import { createJobRunner } from './job.js';

const runner = createJobRunner({
  onFinished: job => {
    if (job.status === 'completed')
      toast(`✓ ${job.result?.shots ?? '?'} 镜 (质检 ${job.result?.issues ?? 0})`);
    else if (job.status !== 'unknown') toast('分镜结束: ' + (job.status || ''));
    refresh();
  },
  onBusy: busy => setBusy(busy),
});

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const ARC_COLOR = { narr: '#2f6b52', quote: '#3a5f6e', persona: '#6e3a5a',
  hook: '#3d4a63', cta: '#3d4a63', brand: '#4a3a1c' };
const TYPE_ZH = { narr: '叙事弧', quote: '书证镜', persona: '人设镜',
  hook: '钩子', cta: '行动召', brand: '品牌' };
const STYLE_ZH = { list: '清单卡', quote: '金句卡', event: '事件字', img: '配图', title: '标题卡' };

let SHOTS = [], CARDS = [], ARCS = [], ISSUES = {}, WARNINGS = [], SLOT_OVERS = [], FROZEN = false, STALE = false;
let dirty = new Set(), GATE_OPEN = false;

/* ── 数据 ─────────────────────────────────────────────── */
async function refresh() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/shots`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; CARDS = d.cards || []; ARCS = d.arcs || [];
  ISSUES = d.issues || {}; WARNINGS = d.warnings || []; SLOT_OVERS = d.slot_overs || [];
  STALE = !!(d.script_stale ?? st.script_stale);   // 源稿指纹闸 (1109): 快照过期全页亮灯
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  renderHeader(); renderRail(st, d.gates); renderAxis();
  if (st.running_job) runner.attach(st.running_job);   // 刷新重挂
  FROZEN = !!st.frozen;
  renderSysWarn(); renderBlocks(); renderSide(); renderGate(d.gates?.shots, d.ready);
  refreshDirty();
  if (FROZEN) {   // 放行冻结: 手改面转只读 (手改=作废英化); 🎨 重跑=官方解冻路, 常亮 (1109 用户令)
    document.querySelectorAll('[data-k="keyframe_zh"]').forEach(el => {
      el.contentEditable = 'false';
    });
    $('btn-save').disabled = true;
    $('save-label').textContent = '🔒 已放行冻结';
    $('btn-run').title = '已放行英文化 — 重跑会作废 ④ 参数/英化/K2 渲染状态 (后端自动清戳解冻)';
  } else {
    $('btn-run').title = '';
  }
}

/* 系统自查: 叙事/书证/人设弧缺「视觉世界」→ 一键补全 (用户令: 缺字段是系统的锅) */
const NEED_WORLD = ['narr', 'persona', 'quote'];
function missingWorlds() {
  return ARCS.filter(a => NEED_WORLD.includes(a.type) && !String(a.world || '').trim());
}

function renderSysWarn() {
  const miss = missingWorlds();
  const box = $('syswarn');
  if (!miss.length) { box.innerHTML = ''; box.hidden = true; return; }
  box.hidden = false;
  box.className = 'syswarn';
  box.innerHTML = `<span class="txt">⚠ ${miss.length} 条弧缺「视觉世界」(${miss.map(a => a.arc_id).join('/')})
    — 写手没有世界可继承, 画面句会退化成通用场景</span>
    <button class="btn-fix" id="btn-fillworld" title="由系统调 LLM 定点补全这些弧的视觉世界 (只补缺的)">🔧 系统补全</button>`;
  const b = document.getElementById('btn-fillworld');
  b.onclick = async () => {
    b.disabled = true; b.textContent = '⏳ 补全中…';
    try {
      const r = await (await fetch(`${BASE}/concept/fill-worlds`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
      await refresh();
      toast(r.remaining
        ? `补全 ${r.filled} 条, 仍缺 ${r.remaining} 条`
        : `✓ 补全 ${r.filled} 条视觉世界 — 重跑分镜后画面句才会用上`);
    } catch (e) {
      toast('补全失败: ' + e.message);
      b.disabled = false; b.textContent = '🔧 系统补全';
    }
  };
}

function shotIssues(sid) { return ISSUES[sid] || []; }
function arcIssues() { return ISSUES['弧级'] || []; }

function renderHeader() {
  const nIss = SHOTS.reduce((a, s) => a + shotIssues(s.sid).length, 0);
  const nArc = arcIssues().length;
  const secs = Math.round(SHOTS.reduce((s, x) => s + (x.est_s || 0), 0));
  $('counts').innerHTML =
    `<span class="badge">${SHOTS.length} 镜</span>` +
    `<span class="badge">${CARDS.length} 卡</span>` +
    `<span class="badge ${nIss ? 'bad' : 'ok'}">镜级 ${nIss || '✓'}</span>` +
    (nArc ? `<span class="badge warn">弧级 ${nArc}</span>` : '') +
    `<span class="badge">~${secs}s</span>`;
}

function renderRail(st, gates) { rail($('rail'), { line: st.line, gates, active: 'shots' }); }

/* ── 顶轴: 一镜一段, 点击定位 ─────────────────────────── */
function renderAxis() {
  if (!SHOTS.length) return;
  const total = SHOTS.reduce((s, x) => s + (x.est_s || 1), 0) || 1;
  const segs = []; let acc = 0;
  for (const s of SHOTS) {
    const w = +((s.est_s || 1) / total * 100).toFixed(2);
    segs.push({ l: +acc.toFixed(2), w,
                c: shotIssues(s.sid).length ? '#8a2e2e' : (ARC_COLOR[s.arc_type] || '#334155'),
                title: `${s.sid} · ${s.arc_id} · ~${s.est_s}s${shotIssues(s.sid).length ? ' · 质检 ' + shotIssues(s.sid).length : ''}` });
    acc += w;
  }
  timeline($('axis'), { arc: false, shots: segs, axisLabel: `~${Math.round(total)}s 估`,
    caption: '一镜一段 (红=有质检问题) · 点任意段跳到该镜' });
  const els = $('axis').querySelectorAll('.lane.shots .shot');
  els.forEach((el, i) => {
    if (!SHOTS[i]) return;
    el.classList.add('clickable');
    el.onclick = () => jumpTo(SHOTS[i].sid);
  });
}

function jumpTo(sid) {
  const el = document.querySelector(`[data-shot="${sid}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 1300);
}

/* ── 左区: 弧分块 ─────────────────────────────────────── */
function arcMeta(aid) { return ARCS.find(a => a.arc_id === aid) || {}; }

function renderBlocks() {
  if (!SHOTS.length) {
    $('blocks').innerHTML = '<div class="arc"><div class="shotrow muted">尚无镜 — 点下方「🎨 重跑分镜」由立意弧切镜</div></div>';
    return;
  }
  const byArc = new Map();
  for (const s of SHOTS) {
    if (!byArc.has(s.arc_id)) byArc.set(s.arc_id, []);
    byArc.get(s.arc_id).push(s);
  }
  const out = [];
  for (const [aid, shots] of byArc) {
    const a = arcMeta(aid);
    const type = a.type || shots[0].arc_type || 'narr';
    const world = String(a.world || '').trim();
    const est = Math.round(shots.reduce((x, s) => x + (s.est_s || 0), 0));
    const needWorld = NEED_WORLD.includes(type);
    const wTitle = world
      ? `视觉世界: ${world}${a.metaphor ? ' / 隐喻: ' + a.metaphor : ''}`
      : (needWorld
          ? '视觉世界为空 — 写手无世界可继承, 画面句会退化成通用场景 (系统可一键补全)'
          : '非叙事位: 系列冻结语言, 不分配立意世界');
    const wCell = !needWorld
      ? `<span class="world na" title="${esc(wTitle)}">— 非叙事位</span>`
      : `<span class="world${world ? '' : ' miss'}" title="${esc(wTitle)}">${world ? esc(world) : '⚠ 缺视觉世界'}</span>`;
    out.push(`<section class="arc" data-type="${esc(type)}" data-arc="${esc(aid)}">
      <header class="ah">
        <span class="aid">${esc(aid)}</span>
        <span class="atype">${esc(TYPE_ZH[type] || type)}</span>
        ${a.no_start ? `<span class="lspan">L${a.no_start}–${a.no_end}</span>` : ''}
        ${wCell}
        <span class="est">${shots.length} 镜 · ~${est}s</span>
      </header>
      ${shots.map(shotBlock).join('')}
    </section>`);
  }
  $('blocks').innerHTML = out.join('');
}

function cardChip(c) {
  const label = c.kind || STYLE_ZH[c.style] || c.style || '卡';
  const how = STYLE_ZH[c.style] || c.style || '';
  const isImg = ['图示', '公式'].includes(c.kind) || c.style === 'img';
  const btn = !isImg ? ''
    : (c.image
        ? `<a class="kimg" href="${BASE}/shots/card/${encodeURIComponent(c.image)}" target="_blank" title="图示已生成, 点击预览">🖼</a>`
        : `<button class="iconbtn" data-act="render" data-cid="${esc(c.cid)}" title="图示 → HTML 排版出图">🖼</button>`)
      + `<button class="iconbtn" data-act="diag" data-cid="${esc(c.cid)}" title="按整段原文补全图示 (跨行清单漏项: 如「最后两项」没进图)">⟳</button>`;
  const full = String(c.text || '');
  const shown = full.length > 22 ? full.slice(0, 22) + '…' : full;
  const tip = '内容类型: ' + label + ' / 渲染: ' + how + ' · ' + full;
  return `<span class="kard kard-${esc(c.style)}" title="${esc(tip)}">`
    + `${esc(label)}「${esc(shown)}」${btn}</span>`;
}

function shotBlock(s) {
  const iss = shotIssues(s.sid);
  const kards = CARDS.filter(c => c.sid === s.sid);
  const byLine = new Map();
  const loose = [];
  for (const c of kards) {
    if (c.line_no && (s.line_refs || []).includes(c.line_no)) {
      if (!byLine.has(c.line_no)) byLine.set(c.line_no, []);
      byLine.get(c.line_no).push(c);
    } else loose.push(c);
  }
  const srcBadge = s.source === 'brand_card' || s.brand_card
    ? '<span class="src src-brand" title="品牌冻结骨架 (登山客三拍+圣经色板), LLM 零参与">🔒 品牌卡</span>'
    : ((s.arc_type === 'hook' || s.arc_type === 'cta')
        ? '<span class="src src-const" title="按特区宪法生成 (钩子/行动召既定画面语言)">📐 宪法</span>' : '');

  const sents = (s.sents || []).map(sen => {
    const cs = byLine.get(sen.no) || [];
    return `<div class="sent"><span class="ln">L${sen.no}</span><span class="tx">${esc(sen.text)}</span></div>`
      + (cs.length ? `<div class="kards">${cs.map(cardChip).join('')}</div>` : '');
  }).join('');

  return `<div class="shotrow${iss.length ? ' bad' : ''}" data-shot="${esc(s.sid)}">
    <div class="sh">
      <span class="sid">${esc(s.sid)}</span>
      <span class="dur">~${s.est_s}s</span>
      ${srcBadge}
      ${iss.length ? `<span class="qcdot" title="${esc(iss.join(' | '))}"></span>` : ''}
      <span class="flex1"></span>
      <button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}" title="按质检问题 LLM 定点重写 (保场景保构图, 最小改动)">↻ 重写</button>
    </div>
    <div class="kf" contenteditable="true" spellcheck="false" data-sid="${esc(s.sid)}"
         data-k="keyframe_zh">${esc(s.keyframe_zh || '')}</div>
    <div class="sents">${sents}</div>
    ${loose.length ? `<div class="kards">${loose.map(cardChip).join('')}</div>` : ''}
  </div>`;
}

/* ── 右栏: 总审 ───────────────────────────────────────── */
function shortIssue(txt) {
  const t = String(txt || '');
  const m = t.match(/^s\d+\s+(.*)$/);
  return m ? m[1] : t;
}

function renderSide() {
  const items = [];
  for (const s of SHOTS) {
    for (const t of shotIssues(s.sid)) {
      items.push(`<div class="qitem" data-jump="${esc(s.sid)}">
        <span class="sj">${esc(s.sid)}</span>
        <span class="txt">${esc(shortIssue(t)).slice(0, 120)}</span>
        <button class="iconbtn" data-fix="${esc(s.sid)}" title="LLM 按质检问题定点重写此镜 (保场景保构图, 最小改动)">🔧</button></div>`);
    }
  }
  $('qcount').textContent = items.length;
  $('qcount').className = 'badge ' + (items.length ? 'bad' : 'ok');
  $('qlist').innerHTML = items.length ? items.join('')
    : '<div class="qempty">✓ 镜级问题已清 — 可放行</div>';
  $('qlist').querySelectorAll('[data-jump]').forEach(el => {
    el.onclick = () => jumpTo(el.dataset.jump);
  });
  // 1109 用户令: 问题必须给解法 — 逐项 🔧 定点修
  $('qlist').querySelectorAll('[data-fix]').forEach(b => {
    b.onclick = () => fixText([b.dataset.fix], b);
  });

  const arc = arcIssues();
  $('acount').textContent = arc.length;
  // 1109 令54 补: 弧级问题也要给解法 — 自带「补 sN/sN」点名的挂 🔧 一键修
  $('alist').innerHTML = arc.length
    ? arc.map(x => {
        const m = String(x).match(/补 ((?:s\d+)(?:\/s\d+)*)/);
        const btn = m ? ` <button class="iconbtn" data-fix="${esc(m[1])}" title="把点名的镜改人物镜 (弧绑定角色入画, 保场景保构图)">🔧</button>` : '';
        return `<div class="aitem">${esc(x)}${btn}</div>`;
      }).join('')
    : '<div class="qempty">✓ 无</div>';
  $('alist').querySelectorAll('[data-fix]').forEach(b => {
    b.onclick = () => fixText(b.dataset.fix.split('/'), b);
  });

  // 槽长超限 = 可执行解法 (拆行), 不与普通提醒混排
  const plain = WARNINGS.filter(w => !String(w).includes('超 H3'));
  $('warncard').innerHTML =
    (STALE ? '<div class="hint">⚠ <b>稿件已改 — 本页是老稿快照</b>: 回 ① 文稿分诊重跑后, ②③ 全链重新生成 (本页任何修改都会被拒)</div>' : '')
    + plain.map(w => `<div class="hint">💡 ${esc(w)}</div>`).join('')
    + SLOT_OVERS.map(o => `<div class="hint">💡 ${esc(o.sid)} 槽长 ${(o.est_s || 0).toFixed(1)}s 超 H3 15s 上限
      <button class="btn-fix" data-split="${esc(o.sid)}" title="祸源口播行按标点对半拆 → 重装包 (未受影响镜原句保留) → 新槽画面句 LLM 补">⚡ 一键拆行</button></div>`).join('');
  $('warncard').hidden = !STALE && !plain.length && !SLOT_OVERS.length;
  $('warncard').querySelectorAll('[data-split]').forEach(b => {
    b.onclick = () => splitSlot(b.dataset.split, b);
  });
}

/* 1109 用户令: 一键修 — LLM 按质检问题定点重写 (最小改动) */
async function fixText(sids, btn) {
  if (!sids.length) return;
  const old = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await (await fetch(`${BASE}/shots/fix_text`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sids }) })).json();
    if (r.detail) { toast('失败: ' + r.detail); return; }
    const nFix = (r.fixed || []).length, nBad = Object.keys(r.unfixed || {}).length;
    toast(nFix ? `✓ 已修 ${nFix} 镜` + (nBad ? ` · ${nBad} 镜仍有残留 (再点一轮或手改)` : '')
               : 'LLM 未产出 — 手改画面句');
    await refresh();
  } catch (e) { toast('修复失败: ' + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}

/* 1109 用户令: 超限槽一键拆行 (结构重排, 未放行前安全) */
async function splitSlot(sid, btn) {
  if (!confirm(`⚡ 拆行 ${sid}：祸源口播行按标点对半拆成两行 → 槽位重排
(未受影响的镜画面句原样保留, 拆出的新槽由 LLM 补画面句)。
若已进 ④, 导演参数/英化将作废重跑。确认？`)) return;
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '⏳ 拆行中…';
  try {
    const r = await (await fetch(`${BASE}/shots/split_slot`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid }) })).json();
    if (r.detail) { toast('失败: ' + r.detail); return; }
    toast(`✓ L${r.split.line} 已拆两行 → ${r.shots} 镜重排, 新槽补句 ${r.filled} 镜`);
    await refresh();
  } catch (e) { toast('拆行失败: ' + e.message); }
  finally { btn.disabled = false; btn.textContent = old; }
}

function renderGate(open, ready) {
  GATE_OPEN = !!open;
  const nIss = SHOTS.reduce((a, s) => a + shotIssues(s.sid).length, 0);
  const hasFixable = SHOTS.some(s => shotIssues(s.sid).some(
    t => t.includes('文字载体') || t.includes('引号')));
  const box = $('gatecard');
  box.className = 'card gatebar' + (open ? '' : ' locked');
  box.innerHTML = open
    ? '<div class="gt gold-t">◉ 分镜已放行</div><div class="note">可进 ④ 导演 (景别/运镜/光影)</div>'
    : `<div class="gt">🔒 分镜闸未开</div><div class="note">${ready ? `镜级 ${nIss} 项待清` : '尚未切镜'}</div>`
      + (hasFixable ? '<div class="fixrow"><button class="btn-fix" id="btn-autofix" title="载体补「空白XX」+ 删引号标签/申报注释 (v3 画面里不许有字); 判不了的返回手改">🔧 一键合规</button></div>' : '')
      + ((nIss || arcIssues().some(x => /补 (?:s\d+)(?:\/s\d+)*/.test(String(x))))
         ? '<div class="fixrow"><button class="btn-fix" id="btn-fixtext" title="带质检问题的镜 (搁笔/人物残留/终态词/运镜词/长度…) 由 LLM 定点重写 — 保场景保构图, 最小改动">🔧 一键修全部</button></div>' : '')
      + (!CARDS.length ? '<div class="fixrow"><button class="btn-fix" id="btn-autocards" title="把①分诊给出的金句/清单/配图/书证/事件建议一次建成卡">＋ 一键建卡</button></div>' : '');
  const ac = document.getElementById('btn-autocards');
  if (ac) ac.onclick = async () => {
    const r = await (await fetch(`${BASE}/shots/autocards`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    toast(`新建 ${r.added} 张卡 (共 ${r.cards})`);
    await refresh();
  };
  const af = document.getElementById('btn-autofix');
  if (af) af.onclick = async () => {
    const r = await (await fetch(`${BASE}/shots/fix`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    toast(`已自动合规 ${r.fixed_sids.length} 镜` + (r.manual.length ? ` · ${r.manual.length} 项需手改` : ''));
    await refresh();
    if (r.manual.length) toast('需手改: ' + r.manual[0]);
  };
  const ft = document.getElementById('btn-fixtext');
  if (ft) ft.onclick = () => {
    // 镜级问题镜 ∪ 弧级配额点名镜 (1109 令54 补: 弧级问题同入一键修)
    const sids = new Set(SHOTS.filter(s => shotIssues(s.sid).length).map(s => s.sid));
    arcIssues().forEach(x => String(x).match(/补 ((?:s\d+)(?:\/s\d+)*)/)
      ?. [1].split('/').forEach(id => sids.add(id)));
    fixText([...sids], ft);
  };
  const nx = $('btn-next');
  nx.disabled = !open;
  nx.title = open ? '进入 ④ 导演 (景别/运镜/光影)' : (ready ? '先处置镜级问题' : '先跑分镜');
}

/* ── 编辑与保存 ───────────────────────────────────────── */
function refreshDirty() {
  const confirmMode = !dirty.size && !GATE_OPEN;
  $('save-label').textContent = confirmMode ? '✅ 确认放行分镜' : '💾 保存分镜';
  $('n-dirty').textContent = confirmMode ? '' : ` (${dirty.size})`;
  $('dirty').innerHTML = dirty.size
    ? `改稿 <b class="gold-t">${dirty.size} 处未保存</b>` : '改稿 0 处未保存';
}

$('blocks').addEventListener('input', ev => {
  const sid = ev.target.dataset?.sid;
  if (!sid) return;
  dirty.add(sid); refreshDirty();
});

$('blocks').addEventListener('click', async ev => {
  const b = ev.target.closest('button[data-act]'); if (!b) return;
  if (b.dataset.act === 'render') {
    b.disabled = true; b.textContent = '⏳';
    try {
      const r = await (await fetch(`${BASE}/shots/card/render`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cid: b.dataset.cid }) })).json();
      if (r.rendered) { toast(`✓ 已出图 ${r.image}.png`); await refresh(); }
      else toast('渲染失败: ' + (r.detail || '未知'));
    } catch (e) { toast('渲染失败: ' + e.message); }
    finally { b.disabled = false; b.textContent = '🖼'; }
    return;
  }
  if (b.dataset.act === 'diag') {
    b.disabled = true;
    try {
      const r = await (await fetch(`${BASE}/shots/card/diagram`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cid: b.dataset.cid }) })).json();
      if (r.ok) { toast('✓ 图示结构已按整段原文补全'); await refresh(); }
      else toast('补全失败');
    } catch (e) { toast('补全失败: ' + e.message); }
    finally { b.disabled = false; }
    return;
  }
  if (b.dataset.act === 'reroll') {
    // 1109 用户令: 重写真化 — 按质检问题 LLM 定点重写 (无问题也走通用重写)
    await fixText([b.dataset.sid], b);
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
  dirty.clear();
  await refresh();
  if (r.gate) toast('✓ 分镜放行 — 可进 ④');
  else if (Object.keys(r.issues || {}).length) toast('质检未过: ' + Object.keys(r.issues)[0]);
  return r;
}
$('btn-save').onclick = saveShots;

$('btn-run').onclick = async () => {
  if (FROZEN && !confirm('🔒 本集已放行英文化。\n重跑 ③ = 分镜画面句全部重新生成, ④ 导演参数 / 英文化 / K2 渲染状态将全部作废 (须重跑)。\n确认重跑？')) return;
  if (dirty.size && !confirm('有未保存改动，继续会覆盖 — 先保存?')) return;
  if (dirty.size) await saveShots();
  const r = await fetch(`${BASE}/v3_shots`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  const j = await r.json();
  runner.attach(j);
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
