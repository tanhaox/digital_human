// ── v3 八步轨 (页面共享; 状态数据源 /api/anim/v3/ep/{b}/{e}/state) ─────────
export const STEPS = [
  { n: '①', id: 'triage', label: '文稿分诊' },
  { n: '②', id: 'concept', label: '立意' },
  { n: '③', id: 'shots', label: '分镜文字' },
  { n: '④', id: 'direct', label: '导演' },
  { n: '⑤', id: 'k2', label: 'K2 生图' },
  { n: '⑥', id: 'tts', label: 'TTS' },
  { n: '⑦', id: 'stamp', label: '盖章' },
  { n: '⑧', id: 'h3', label: 'H3 视频' },
];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

/**
 * @param {Element} el
 * @param {{line:string, gates:Object<string,boolean>, active?:string}} st
 * gates 键 = triage/arcs/shots/direct/stamp (k2/tts/h3 走 v2 状态, 无闸时按前面解锁推)
 */
export function rail(el, st) {
  const unlockedUpTo = (id) => {
    const order = ['triage', 'arcs', 'shots', 'direct'];
    const i = order.indexOf(id);
    if (i < 0) return st.line === 'v3';
    for (let k = 0; k <= i; k++) if (st.gates && st.gates[order[k]]) return true;
    return i === 0 && st.line === 'v3';
  };
  el.innerHTML = STEPS.map(s => {
    const on = s.id === st.active;
    const done = st.gates && st.gates[s.id];
    const lock = !on && !done && !unlockedUpTo(s.id);
    const cls = on ? 'now' : (done ? 'done' : (lock ? 'lock' : ''));
    const badge = done ? '<span class="st">✓</span>'
      : (lock ? '<span class="st">🔒</span>' : (on ? '<span class="st">进行中</span>' : ''));
    return `<a class="step ${cls}" href="#${s.id}"><span class="n">${s.n}</span>${esc(s.label)}${badge}</a>`;
  }).join('<span class="sep">→</span>');
}
