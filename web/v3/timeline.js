// ── v3 渐进时间轴共享组件 (从 UI 定稿移植, 1105 V0) ──────────────────────
// 基准九段带 (三型弧模型) 一次定义, 八页按层配置叠加 — 规划规范: 一轴到底。
// 用法: timeline(el, { bands: BANDS, layers: [{type:'script'|'arc'|'shots'|...}] })
export const BANDS = [
  { id: 'hook',  label: '钩',   w: 6,  color: '#22304a', kind: 'off' },
  { id: 'sq1',   label: '书证①', w: 8,  color: '#3a5f6e', kind: 'quote' },
  { id: 'A1',    label: 'A1',   w: 19, color: '#2f6b52', kind: 'narr' },
  { id: 'per1',  label: '人①',  w: 6,  color: '#6e3a5a', kind: 'persona' },
  { id: 'sq2',   label: '书证②', w: 7,  color: '#3a5f6e', kind: 'quote' },
  { id: 'A2',    label: 'A2',   w: 26, color: '#6b572f', kind: 'narr' },
  { id: 'sq3',   label: '书证③', w: 6,  color: '#3a5f6e', kind: 'quote' },
  { id: 'A3',    label: 'A3',   w: 17, color: '#5c3f6b', kind: 'narr' },
  { id: 'cta',   label: 'CTA牌', w: 5, color: '#22304a', kind: 'off' },
];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

function laneSegs(lane) {
  return lane.map(b =>
    `<div class="seg" style="width:${b.w}%;background:${b.color}">` +
    (b.w >= 6 ? `<span class="lb">${esc(b.label)}</span>` : '') + `</div>`).join('');
}

/**
 * 渲染渐进轴。
 * @param {Element} el 容器
 * @param {{arc?:boolean, arcGhost?:boolean, shots?:Array, shotsGhost?:boolean,
 *          script?:Array, blocks?:boolean, axisLabel?:string, caption?:string}} cfg
 * bands 可覆写 (真实数据时按 doc.v3.arcs 生成); shots: [{l,w,c,cls,title}]
 */
export function timeline(el, cfg = {}) {
  const bands = cfg.bands || BANDS;
  let html = '<div class="lanes">';
  if (cfg.script) html += `<div class="lane script">${laneSegs(cfg.script)}</div>`;
  if (cfg.arc !== false) {
    const ghost = cfg.arcGhost ? ' ghost' : '';
    html += `<div class="lane arc${ghost}">${laneSegs(bands)}</div>`;
  }
  if (cfg.shots) {
    const g = cfg.shotsGhost ? ';opacity:.55' : '';
    const divs = cfg.shots.map(s =>
      `<div class="shot${s.cls ? ' ' + s.cls : ''}" style="left:${s.l}%;width:${s.w}%;${s.c ? 'background:' + s.c + g : ''}"` +
      (s.title ? ` title="${esc(s.title)}"` : '') + '></div>').join('');
    html += `<div class="lane shots">${divs}</div>`;
  }
  if (cfg.blocks) html += '<div class="blklane"></div>';
  html += '</div>';
  html += `<div class="axisline"><span>0s</span><span>60</span><span>120</span><span>180</span>` +
          `<span>${esc(cfg.axisLabel || '~234s 估')}</span></div>`;
  if (cfg.caption) html += `<div class="cap">${esc(cfg.caption)}</div>`;
  el.innerHTML = html;
}
