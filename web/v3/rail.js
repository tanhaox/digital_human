// ── v3 八步轨 (页面共享; 状态数据源 /api/anim/v3/ep/{b}/{e}/state) ─────────
// gate = /state 返回的闸态键 (文档闸链 triage→arcs→shots→direct; stamp 链外等音频);
// k2/tts/h3 无 doc 闸 → gate:null, 以四道文档闸全开为解锁条件。
// page = 已建成页面的相对路径 (未建的留 null, 轨上不可点 — 防 404)。
export const STEPS = [
  { n: '①', id: 'triage',  gate: 'triage', page: './triage.html',  label: '文稿分诊' },
  { n: '②', id: 'concept', gate: 'arcs',   page: './concept.html', label: '立意' },
  { n: '③', id: 'shots',   gate: 'shots',  page: './shots.html',   label: '分镜文字' },
  { n: '④', id: 'direct',  gate: 'direct', page: './director.html', label: '导演' },
  { n: '⑤', id: 'k2',      gate: 'direct', page: './k2.html',      label: 'K2 生图' },
  { n: '⑥', id: 'tts',     gate: 'direct', page: './tts.html',     label: 'TTS' },
  { n: '⑦', id: 'stamp',   gate: 'stamp',  page: null,             label: '盖章' },
  { n: '⑧', id: 'h3',      gate: null,     page: null,             label: 'H3 视频' },
];

// 文档闸链 (顺序即定义; 用于推导解锁)
export const CHAIN = ['triage', 'arcs', 'shots', 'direct'];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

/** 某步是否已放行 */
export function stepDone(s, gates) { return !!(s.gate && (gates || {})[s.gate]); }

/** 某步是否可进入 (前置闸全开; 链外/无闸步按四闸全开) */
export function stepUnlocked(s, gates, line) {
  if (line !== 'v3') return false;
  const G = gates || {};
  if (!s.gate) return CHAIN.every(k => G[k]);
  const j = CHAIN.indexOf(s.gate);
  if (j < 0) return true;                     // stamp: 链外, 不挡
  return j === 0 || CHAIN.slice(0, j).every(k => G[k]);
}

/** 第一个未放行的文档闸步 (入口页「下一步」) */
export function firstPending(gates) {
  return STEPS.find(s => s.gate && CHAIN.includes(s.gate) && !stepDone(s, gates)) || null;
}

/**
 * @param {Element} el
 * @param {{line:string, gates:Object<string,boolean>, active?:string}} st
 */
export function rail(el, st) {
  const G = st.gates || {};
  el.innerHTML = STEPS.map(s => {
    const on = s.id === st.active;
    const done = stepDone(s, G);
    const lock = !done && !stepUnlocked(s, G, st.line);
    const cls = done ? 'done' : (lock ? 'lock' : (on ? 'now' : ''));
    const badge = done ? '<span class="st">✓</span>'
      : (lock ? '<span class="st">🔒</span>'
              : (on ? '<span class="st">进行中</span>'
                    : (s.page ? '' : '<span class="st">待建</span>')));
    const href = s.page ? s.page + location.search : '';
    const attrs = href ? ` href="${esc(href)}"` : ' aria-disabled="true"';
    return `<a class="step ${cls}"${attrs}><span class="n">${s.n}</span>${esc(s.label)}${badge}</a>`;
  }).join('<span class="sep">→</span>');
}
