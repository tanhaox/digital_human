// ── 纯工具函数: 无 IO / 无状态, 可直接 node --test 单测 ──

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** HTML 文本/属性值转义。注意: 只用于元素内容与常规属性, 不用于内嵌 JS 字符串上下文。 */
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

/** 镜状态 → 展示名 */
export const STNAME = {
  planned: '⏳待生图', img_done: '🔍待人检', approved: '✅已批',
  anim_done: '🎬动画成', anim_fail: '⚠️动画败',
};

/** 状态名去 emoji 前缀 (用于统计拼接) */
export const stLabel = k => (STNAME[k] || k).replace(/^[^一-龥\w]+/, '');

/**
 * 秒 → mm:ss 时间码。
 * 先整体取整再拆分: 逐段 Math.round 会在 59.6s 产生 "00:60" 这类非法时间码
 * (原实现 bug, 全站时间显示受影响 — 回归用例见 tests/util.test.js)。
 */
export const tc = s => {
  const total = Math.max(0, Math.round(s || 0));
  const m = Math.floor(total / 60), ss = total % 60;
  return String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
};

/** 一组镜的状态统计文本: "待生图 3 · 已批 5" */
export function statTxt(shots) {
  const gc = {};
  for (const s of shots) gc[s.status] = (gc[s.status] || 0) + 1;
  return Object.entries(gc).map(([k, v]) => `${stLabel(k)} ${v}`).join(' · ');
}

/** 状态统计 chip (可点击定位版): 数据同 statTxt, 每段可点跳到该范围此状态的镜
 *  (0920 用户令: 看"待生图 1"想直达位置; 0921 起连点循环轮换)。
 *  data-act=statLocate 走统一 click 委托。 */
export function statChips(shots) {
  const gc = {};
  for (const s of shots) gc[s.status] = (gc[s.status] || 0) + 1;
  return Object.entries(gc).map(([k, v]) => {
    const lab = esc(stLabel(k));   // 非枚举 status 会原样透传 — 属性/正文都要 esc
    return `<button type="button" class="stat-chip" data-act="statLocate" data-st="${esc(k)}"`
      + ` title="定位到该范围下一镜「${lab}」(连点轮换)">${lab} ${v}</button>`;
  }).join(' · ');
}

/** 组号显示: S3 → s3, 其余原样 */
export const gidShow = gid =>
  (/^S\d+$/i.test(gid || '') ? 's' + parseInt(gid.slice(1), 10) : (gid || '?'));

/** 均值保留 1 位小数 (调用方保证数组非空) */
export const avg1 = arr => Math.round(arr.reduce((a, b) => a + b, 0) / arr.length * 10) / 10;

/** 数组去重 (保序) */
export const uniq = arr => [...new Set(arr)];

/** 立意待续跑态: 有场结构且全场无镜 (0917 两级规划第二段入口)。
 *  放在 util 而非某个视图模块 — topbar 与 concept 都要用, 视图层不互相依赖。 */
export const scenesReady = st =>
  !!st && (st.arcs || []).length > 0 &&
  ((st.total || 0) === 0 ||
   (st.arcs || []).some(a => !(st.shots || []).some(s => String(s.arc_id) === String(a.arc_id))));

/**
 * @typedef {Object} Shot
 * @property {string} shot_id @property {string} status @property {string} [arc_id]
 * @property {string} [group_id] @property {string} [camera] @property {string} [narration]
 * @property {number} [t_start] @property {number} [t_end] @property {string} [image_file]
 * @property {string} [video_file] @property {boolean} [brand_card] @property {boolean} [zone]
 * @property {Array<{kind:string,text:string,t_start:number,t_end:number}>} [text_layer]
 */
