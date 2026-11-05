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
  { id: 'cta',   label: '行动召·牌', w: 5, color: '#22304a', kind: 'off' },
];

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

function laneSegs(parent, lane) {   // CSSOM 赋值: CSP style-src 只拦 style 属性, 不拦 .style.x=
  for (const b of lane) {
    const seg = document.createElement('div');
    seg.className = 'seg';
    seg.style.width = b.w + '%';
    seg.style.background = b.color;
    if (b.w >= 6) { const lb = document.createElement('span'); lb.className = 'lb'; lb.textContent = b.label; seg.appendChild(lb); }
    parent.appendChild(seg);
  }
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
  el.innerHTML = '';
  const lanes = document.createElement('div');
  lanes.className = 'lanes';
  el.appendChild(lanes);
  if (cfg.script) { const l = document.createElement('div'); l.className = 'lane script'; lanes.appendChild(l); laneSegs(l, cfg.script); }
  if (cfg.arc !== false) {
    const l = document.createElement('div'); l.className = 'lane arc' + (cfg.arcGhost ? ' ghost' : '');
    lanes.appendChild(l); laneSegs(l, bands);
  }
  if (cfg.shots) {
    const shotsWrap = document.createElement('div'); shotsWrap.className = 'lane shots';
    lanes.appendChild(shotsWrap);
    for (const s of cfg.shots) {   // CSSOM 逐镜赋值 (CSP 禁 style 属性)
      const d = document.createElement('div');
      d.className = 'shot' + (s.cls ? ' ' + s.cls : '');
      d.style.left = s.l + '%'; d.style.width = s.w + '%';
      if (s.c) d.style.background = s.c;
      if (cfg.shotsGhost) d.style.opacity = '.55';
      if (s.title) d.title = s.title;
      shotsWrap.appendChild(d);
    }
  }
  if (cfg.blocks) { const b = document.createElement('div'); b.className = 'blklane'; lanes.appendChild(b); }
  const ax = document.createElement('div'); ax.className = 'axisline';
  for (const t of ['0s', '60', '120', '180', cfg.axisLabel || '~234s 估']) {
    const sp = document.createElement('span'); sp.textContent = t; ax.appendChild(sp);
  }
  el.appendChild(ax);
  if (cfg.caption) { const c = document.createElement('div'); c.className = 'cap'; c.textContent = cfg.caption; el.appendChild(c); }
}
