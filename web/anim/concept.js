// ── 立意人闸面板: 六拍分配表只读展示 / 结构化编辑 / 保存与单场重掷 ──
import { $, esc, scenesReady } from './util.js';
import { api } from './api.js';

let editData = null;  // 编辑器结构化数据缓存 (只读表拉取时顺带填充, 保存/重掷后失效)

// 0927 收起态: 用户手动收起腾预览空间 (立意面板常驻=挤分镜区); 面板因 scenesReady
// 变 false 隐藏时自动复位展开 — 人闸再现时默认要被看见
let collapsed = false;

// 1005 具象化改判: 全场有镜后 scenesReady=false 面板整体隐藏 → 图生成后发现立意
// 本身不对时无入口改。forced=手动唤起 (✏️立意 钮), 显隐改 ready||forced。
let forced = false;
let lastSt = null;

/** 手动唤起/收起立意面板 (actions/concept.js conceptForce 调; 传 false 只关) */
export function forceOpen(v) {
  forced = (v === undefined) ? !forced : !!v;
  if (lastSt) conceptView({ st: lastSt });
}

/** store 订阅者: 面板显隐 + 收起态 + 状态说明 + 只读内容 + 场下拉 */
export function conceptView(state) {
  const st = state.st;
  lastSt = st;
  const ready = scenesReady(st) && !st?.running_job;
  const panel = $('#concept-panel');
  panel.hidden = !(ready || forced);
  if (panel.hidden) { collapsed = false; return; }
  renderCollapse();
  // 状态说明 (0927: 面板为什么还显示 — 立意闸等待确认 / 缺镜场待续跑;
  // 1005: 手动唤起时给改判路径指路 — 具象化后发现立意不对的完整链)
  const missing = (st.arcs || []).filter(
    a => !(st.shots || []).some(s => String(s.arc_id) === String(a.arc_id)));
  const el = $('#concept-status');
  if ((st.total || 0) === 0) {
    el.textContent = '⏸ 立意闸: 确认立意后点 ▶️分镜续跑';
  } else if (missing.length) {
    el.textContent = `⚠ 还有 ${missing.length} 场待分镜: ${missing.map(a => a.arc_id).join('/')} (续跑只补缺场)`;
  } else if (!ready) {
    el.textContent = '✏️ 改判模式: 改立意(或重掷此场) → 💾保存 → 该场 🔁重规划本场 — 新立意下重生镜头';
  } else {
    el.textContent = '';
  }
  if (collapsed) return;  // 收起态不拉表格 (省一次请求)
  loadReadOnly();
  $('#concept-arc').innerHTML = (st.arcs || [])
    .map(a => `<option value="${esc(a.arc_id)}">${esc(a.arc_id)}</option>`).join('');
}

/** 收起/展开渲染 (toggleCollapse 落 DOM; conceptView 每次刷新同步) */
function renderCollapse() {
  const body = $('#concept-body'), btn = $('#concept-collapse-btn');
  if (!body || !btn) return;
  body.hidden = collapsed;
  btn.textContent = collapsed ? '▸ 展开' : '▾ 收起';
}

/** 收起切换 (actions/concept.js 调) */
export function toggleCollapse() {
  collapsed = !collapsed;
  renderCollapse();
}

/**
 * 只读区 = 六拍分配表 (0926 用户令表格化: 原来整页倒 立意.md 纯文本"看的想吐")。
 * 数据源 /concept (结构化), 同一份缓存喂编辑器 (editData) — 一拉两用。
 */
async function loadReadOnly() {
  const ro = $('#concept-ro');
  const token = ++roToken;
  try {
    const d = await api('/concept');
    if (token !== roToken) return;            // 过期响应丢弃
    editData = d;
    ro.innerHTML = renderConceptTable(d);
  } catch (e) {
    if (token === roToken) ro.textContent = '(立意读取失败 — 点✏️编辑)';
  }
}
let roToken = 0;/** 六拍分配表: 世界库 chips + 拍/时段/视觉世界/隐喻核心 + 宪法警告 */
function renderConceptTable(d) {
  const wl = d.world_library || [];
  const chips = wl.map(w => `<span class="wl-chip">${esc(w)}</span>`).join('');
  const rows = (d.scenes || []).map(s => {
    const t0 = Math.round(s.t_start || 0), t1 = Math.round(s.t_end || 0);
    const core = s.metaphor_core || s.visual_metaphor || '(缺)';
    return `<tr><td>${esc(s.arc_id)}</td><td class="c-span">${t0}–${t1}s</td>`
      + `<td class="c-world">${esc(s.visual_world || '未定')}</td>`
      + `<td>${esc(core)}</td></tr>`;
  }).join('');
  const warns = (d.warnings || []).map(w => `<div class="c-warn">⚠ ${esc(w)}</div>`).join('');
  return (wl.length
    ? `<div class="wl"><b>全书视觉世界库 (${wl.length})</b><div class="wl-list">${chips}</div></div>`
    : '')
    + `<table class="concept-tbl"><thead><tr><th>拍</th><th>时段</th><th>视觉世界</th><th>隐喻核心</th></tr></thead>`
    + `<tbody>${rows}</tbody></table>`
    + (d.ep_summary ? `<div class="c-strat">本集策略: ${esc(d.ep_summary)}</div>` : '')
    + warns
    + `<div class="c-hint">✏️ 逐场编辑/单场重掷进编辑模式 · 完整明细见 立意.md</div>`;
}

/** 编辑/只读切换 (懒加载结构化数据) */
export async function toggleConceptEdit() {
  const ed = $('#concept-edit'), ro = $('#concept-ro'), sb = $('#concept-save-btn');
  if (ed.hidden) {
    if (!editData) {
      editData = await api('/concept');
      ed.innerHTML = renderConceptEdit(editData);
    }
    ed.hidden = false; ro.hidden = true; sb.hidden = false;
  } else {
    ed.hidden = true; ro.hidden = false; sb.hidden = true;
  }
}

const MULTI = /mappings|carrier|motifs|banned|prop_lock/;   // 多行字段 (每行一条)

function field(label, id, val, ph) {
  const rows = MULTI.test(id) ? 3 : 1;
  const v = esc(Array.isArray(val) ? val.join('\n') : (val || ''));
  return `<label class="cf"><span>${label}</span>
    <textarea data-k="${id}" rows="${rows}" placeholder="${ph || (MULTI.test(id) ? '每行一条' : '')}">${v}</textarea></label>`;
}

/** 结构化编辑器: 每场一个 details, 字段名即后端 concept-save 契约 */
export function renderConceptEdit(d) {
  return (d.scenes || []).map(s => {
    const t0 = Math.round(s.t_start || 0), t1 = Math.round(s.t_end || 0);
    return `
    <details class="cf-scene" data-arc="${esc(s.arc_id)}">
      <summary>${esc(s.arc_id)} <span class="c-span">${t0}–${t1}s</span> · <span class="cf-world">${esc(s.visual_world || '未定')}</span> · <span class="cf-sum">${esc((s.metaphor_core || s.narrative || '').slice(0, 40))}</span></summary>
      <div class="cf-body">
        ${field('这场讲什么', 'narrative', s.narrative)}
        ${field('视觉世界 (轮换宪法: 相邻场禁同世界)', 'visual_world', s.visual_world)}
        ${field('道具锁定卡 (本场主道具唯一权威描述, 跨镜逐字复用)', 'prop_lock', s.prop_lock)}
        ${field('核心映射 (A=B) — 改掉旧值会自动进禁用史', 'metaphor_core', s.metaphor_core)}
        ${field('分映射 (每行 x=y)', 'metaphor_mappings', s.metaphor_mappings)}
        ${field('口播承载句 (每行一句)', 'carrier_lines', s.carrier_lines)}
        ${field('画面母题 (每行一个可画画面)', 'visual_motifs', s.visual_motifs)}
        ${field('运动线', 'motion', s.motion)}
        ${field('人批注 (写给导演, 全链可见)', 'concept_notes', s.concept_notes)}
        <button class="sm gold" data-act="conceptSaveOne" data-arc="${esc(s.arc_id)}">💾 只保存 ${esc(s.arc_id)}</button>
      </div></details>`;
  }).join('')
    + `<div class="cf-ep"><span>本集策略 (整案)</span>
       <input data-ep="1" data-k="ep_summary" value="${esc(d.ep_summary || '')}"></div>`;
}

/** 从 DOM 收集一场编辑结果 (多行字段拆行为数组) */
export function collectScene(card) {
  const sc = { arc_id: card.dataset.arc };
  card.querySelectorAll('[data-k]').forEach(el => {
    sc[el.dataset.k] = MULTI.test(el.dataset.k)
      ? el.value.split('\n').map(x => x.trim()).filter(Boolean)
      : el.value.trim();
  });
  return sc;
}

/**
 * 保存立意。查询统一圈定在 #concept-edit 内 — 页面将来出现同名结构也不串数据。
 * @param {Element|null} scope 传 details 元素 = 单场保存; null = 整案保存
 */
export async function saveConcept(scope) {
  let body;
  if (scope) {
    body = { scenes: [collectScene(scope)], ep_summary: null };
  } else {
    const host = $('#concept-edit');
    const scenes = [...host.querySelectorAll('details[data-arc]')].map(collectScene);
    const epEl = host.querySelector('[data-ep]');
    body = { scenes, ep_summary: epEl ? epEl.value.trim() : null };
  }
  await api('/concept-save', body);
  invalidateConcept();
}

/** 编辑缓存失效 (下次进编辑重新拉取) */
export function invalidateConcept() { editData = null; }

/** 保存/重掷后回到只读态并刷新内容 (替代原 location.reload 全页刷新) */
export function resetConceptPanel() {
  invalidateConcept();
  $('#concept-edit').hidden = true;
  $('#concept-ro').hidden = false;
  $('#concept-save-btn').hidden = true;
}
