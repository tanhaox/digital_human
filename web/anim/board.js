// ── 三层看板渲染: 场(arc) → 组(group) → 镜(shot), + 60s特区/开场/圣经/v1降级 ──
// 全部为纯函数 (st → HTML); DOM 写入只在 boardView 一处, 并保留展开态。
import { $, $$, esc, tc, statChips, gidShow, avg1 } from './util.js';
import { cardHtml } from './card.js';

const ZONE_SEC = 60;      // 60s 特区窗口 (按正文计时)
const ZONE_MAX_S = 6.05;  // 特区单镜时长展示阈值 (0.05 容差)
const EPS = 0.01;         // 时间轴比较容差
const EXCERPT = 64;       // 组口播摘录字数

/** 🔥 60s 特区横幅 (0919): 正文 [0,60)s = 全集 [opening, opening+60] — 平台推荐池 */
function zoneBanner(st) {
  if (!st.planned) return '';
  const opening = Number(st.opening_sec || 0);
  const zEnd = opening + ZONE_SEC;
  const zShots = (st.shots || []).filter(s =>
    Number(s.t_start ?? 1e9) < zEnd - EPS && Number(s.t_end ?? 0) > opening - EPS);
  if (!zShots.length) return '';
  const spans = zShots.map(s => Math.round((s.t_end - s.t_start) * 10) / 10);
  const maxS = Math.max(...spans);
  const flagged = zShots.filter(s => s.zone).length;
  const stale = zShots.length - flagged;
  return `<section class="zone-banner"><header>
    <span class="z-ico">🔥</span><b>60s 特区</b>
    <span class="z-range">${tc(opening)}–${tc(zEnd)} (正文 0–60s)</span>
    <span class="badge bd-darkred">${zShots.length} 镜</span>
    <span class="badge bd-teal">均 ${avg1(spans)}s</span>
    <span class="badge ${maxS > ZONE_MAX_S ? 'bd-amber' : 'bd-green'}">最长 ${maxS}s</span>
    ${flagged ? `<span class="badge bd-zone">槽位预装 ${flagged}</span>` : ''}
    ${stale ? `<span class="badge bd-amber" title="这些镜在特区窗内但未按新规装槽 (重规划该场可吃新规)">⚠ ${stale} 镜未装槽</span>` : ''}
    <span class="z-note">平台推荐池 · 每镜 3-6s · 组内场景不重样 · 首帧复杂度下限 — 冲平均在线时长</span>
  </header></section>`;
}

/** 开场剪辑层 (素材拼贴·剪映特效位, 非生成) */
function openingSection(st) {
  const opening = Number(st.opening_sec || 0);
  if (!(opening > 0) || !st.planned) return '';
  return `<section class="scene scene-opening"><header class="scene-head">
    <span class="scene-tag">🎬</span>
    <span class="open-title">开场剪辑层 · ${tc(0)}–${tc(opening)} (预留 ${opening}s)</span>
    <span class="scene-stats">黑底打字卡 2s → 定格 1s → 书封墙飞入+扫描 → 主书封收敛定格</span>
    <span class="hint open-note">素材拼贴·剪映特效位, 非生成 — 生成动画全部从 ${tc(opening)} 后开始, 草稿自动装配 (书封←书库 epub)</span>
  </header></section>`;
}

/** 🎨 美术圣经 (书级锁定) */
function bibleSection(st) {
  const b = st.bible || {};
  if (!b.style_prefix) return '';
  return `<details class="bible" data-dkey="bible"><summary>🎨 美术圣经 · 书级锁定 (点击展开)</summary>
    <div class="body">风格: ${esc(b.style_prefix || '—')}<br>色彩: ${esc(b.color_palette || '—')}<br>
    质感: ${esc(b.texture || '—')}<br>角色: ${esc(b.character_policy || '—')}<br>
    氛围: ${esc(b.mood_keywords || '—')}</div></details>`;
}

/** 组行 (剧本文档版式) */
function groupRow(g, shots, busy) {
  const gs = shots.filter(s => s.group_id === g.group_id);
  if (!gs.length) return '';
  const dur = Math.round((g.t_end - g.t_start) * 10) / 10;
  const narr = g.narration || '';
  return `<article class="group-row">
    <div class="tc">${tc(g.t_start)}–${tc(g.t_end)}<br><span class="dur">${dur}s</span></div>
    <div class="gbody">
      <div class="ghead"><b>${esc(gidShow(g.group_id))}</b> <span class="cam">[${esc(g.camera || '')}]</span>
        ${esc(narr.slice(0, EXCERPT))}${narr.length > EXCERPT ? '…' : ''}
        <span class="hint">${gs.map((_, i) => 'p' + (i + 1)).join('·')} · ${statChips(gs)}</span></div>
      ${g.keyframe_zh ? `<span class="kf">${esc(g.keyframe_zh)}</span>` : ''}
      ${g.motion_zh ? `<div class="mo">🏃 ${esc(g.motion_zh)}</div>` : ''}
      <div class="plots">${gs.map(s => cardHtml(s, busy)).join('')}</div>
    </div></article>`;
}

/** 场级动作按钮 (重规划/K2/生视频本场) */
function arcActs(arc, shots, busy) {
  if (busy) return '';
  const inArc = pred => shots.filter(s => s.arc_id === arc.arc_id && pred(s));
  const planned = inArc(s => s.status === 'planned');
  const imgDone = inArc(s => s.status === 'img_done');
  const approved = inArc(s => s.status === 'approved');
  const out = [`<button class="sm" data-act="arcReplan" data-arc="${esc(arc.arc_id)}"
    title="只重新规划本场 (组/镜全部重切, 其余场不动; 旧版自动备份)">🔁 重规划本场</button>`];
  if (planned.length) out.push(`<button class="sm go" data-act="arcK2" data-arc="${esc(arc.arc_id)}"
    title="只生成本场的镜 (一场一验, 不用全程跑)">🎨 K2 本场 ${planned.length}</button>`);
  // 0923 补生图: 打回的 + 跳过/失败的 + 违禁标记的 (planned 且有重试信号)
  // 也显示 img_done + text_suspect (违禁标记但已重跑过的 — 提示可能需再打回)
  const retriable = inArc(s =>
    (s.status === 'planned' &&
     ((s.attempts && s.attempts.k2 > 0) || s.reject_note || s.text_suspect)) ||
    (s.status === 'img_done' && (s.text_suspect || s.reject_note)));
  if (retriable.length) out.push(`<button class="sm" data-act="arcRetryK2" data-arc="${esc(arc.arc_id)}"
    title="补生图: 重试被打回/跳过/失败/违禁标记的镜; img_done+违禁标记 = 需先打回再补">🔄 补生图 ${retriable.length}</button>`);
  const animFail = inArc(s => s.status === 'anim_fail');
  if (imgDone.length || approved.length || animFail.length) out.push(`<button class="sm violet" data-act="arcH3" data-arc="${esc(arc.arc_id)}"
    title="生成本场视频: 自动过审待人检镜 + 生成已批镜 + 重试失败镜">🎬 生视频本场 ${imgDone.length + approved.length + animFail.length}</button>`);
  return out.join('');
}

/** 主形态: 场 → 组 → 镜 */
function arcsWithGroups(st) {
  const shots = st.shots || [];
  const busy = !!st.running_job;
  return (st.arcs || []).map(arc => {
    const groups = (st.groups || []).filter(g => g.arc_id === arc.arc_id);
    const arcShots = shots.filter(s => s.arc_id === arc.arc_id);
    if (!groups.length || !arcShots.length) return '';
    const t0 = arc.t_start ?? arcShots[0].t_start ?? 0;
    const t1 = arc.t_end ?? arcShots[arcShots.length - 1].t_end ?? 0;
    return `<section class="scene" id="arc-${esc(arc.arc_id || '')}">
      <header class="scene-head"><span class="scene-tag">${esc((arc.arc_id || '?').toLowerCase())}</span>
      <span class="scene-stats">${groups.length} 组 · ${arcShots.length} 镜 · ${statChips(arcShots)} · ${tc(t0)}–${tc(t1)}</span>
      ${arcActs(arc, shots, busy)}
      <details data-dkey="arc-${esc(arc.arc_id || '')}"><summary>▸ 场说明</summary><div class="body">
        <b class="arc-name">${esc(arc.arc_id || '')} · ${esc(arc.narrative || '')}</b><br>
        ${arc.motion ? '🏃 ' + esc(arc.motion) : ''}</div></details></header>
      ${groups.map(g => groupRow(g, shots, busy)).join('')}</section>`;
  }).join('');
}

/** 无组结构形态: 场 → 镜 */
function arcsFlat(st) {
  const shots = st.shots || [];
  return (st.arcs || []).map(arc => {
    const g = shots.filter(s => s.arc_id === arc.arc_id);
    if (!g.length) return '';
    return `<section class="scene"><header class="scene-head">
      <span class="scene-tag">${esc((arc.arc_id || '?').toLowerCase())}</span>
      <span class="scene-stats">${g.length} 镜 · ${statChips(g)}</span>
      <details data-dkey="arc-${esc(arc.arc_id || '')}"><summary>▸ 场说明</summary><div class="body">
        ${esc(arc.narrative || '')}${arc.motion ? '<br>🏃 ' + esc(arc.motion) : ''}</div></details></header>
      <div class="plots">${g.map(s => cardHtml(s, !!st.running_job)).join('')}</div></section>`;
  }).join('');
}

/** v1 降级: 无场结构时按 label 连续分组 */
function legacyGroups(st) {
  const groups = [];
  let cur = null;
  for (const s of (st.shots || [])) {
    const lab = s.label || '未分组';
    if (!cur || cur.label !== lab) { cur = { label: lab, shots: [] }; groups.push(cur); }
    cur.shots.push(s);
  }
  return groups.map(g => `<section class="scene"><header class="scene-head"><span class="scene-tag">🎞</span>
    <span class="scene-stats legacy">${esc(g.label)} · ${statChips(g.shots)}</span></header>
    <div class="plots">${g.shots.map(s => cardHtml(s, !!st.running_job)).join('')}</div></section>`).join('');
}

/** 看板入口 (纯函数)
 * @param {object} st status_view 输出 @returns {string} */
export function renderBoard(st) {
  const shots = st.shots || [];
  const hasArcs = (st.arcs || []).length > 0 && shots.some(s => s.arc_id);
  const hasGroups = (st.groups || []).length > 0 && shots.some(s => s.group_id);
  if (hasArcs && hasGroups) return openingSection(st) + zoneBanner(st) + bibleSection(st) + arcsWithGroups(st);
  if (hasArcs) return openingSection(st) + zoneBanner(st) + bibleSection(st) + arcsFlat(st);
  return openingSection(st) + legacyGroups(st);
}

/** 看板视图 (store 订阅者): 重渲染时保留 <details> 展开态与滚动位置 */
export function boardView(state) {
  const board = $('#board');
  const st = state.st;
  if (!st) { board.innerHTML = ''; return; }
  const scroll = window.scrollY;
  const wasOpen = new Set($$('details[data-dkey][open]', board).map(d => d.dataset.dkey));
  board.innerHTML = renderBoard(st);
  for (const d of $$('details[data-dkey]', board)) {
    if (wasOpen.has(d.dataset.dkey)) d.open = true;
  }
  window.scrollTo(0, scroll);
}
