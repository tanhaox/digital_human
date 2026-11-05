// v3 入口页逻辑 (外置: CSP script-src 'self' 禁内联 — anim 家规)
import { timeline } from './timeline.js';
import { rail, firstPending, stepDone, STEPS } from './rail.js';
const qs = new URLSearchParams(location.search);
const b = qs.get('book_id') || '', e = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(b)}/${e}`;
const el = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const MODE_COLOR = { hook: '#22304a', quote: '#3a5f6e', comm: '#2f6b52',
  persona: '#6e3a5a', cta: '#22304a', brand: '#4a3a1c' };
const TYPE_COLOR = { narr: '#2f6b52', quote: '#3a5f6e', persona: '#6e3a5a',
  hook: '#22304a', cta: '#22304a', brand: '#4a3a1c' };

// 四道文档闸步的说明 (入口页进度条文案)
const ZH = {
  triage: { label: '文稿分诊', hint: c => `${c.lines ?? '—'} 行稿 · ${c.cards ?? '—'} 条上屏建议` },
  concept: { label: '立意', hint: c => `${c.arcs ?? '—'} 弧 · 世界观与隐喻` },
  shots: { label: '分镜文字', hint: c => `${c.shots ?? '—'} 镜 · ${c.cards ?? '—'} 张卡 · 质检 ${c.shot_issues ?? '—'} 处待改` },
  direct: { label: '导演', hint: () => '景别 / 运镜三元组 / 光影 — V4 建设中' },
};

/** 真实弧带 (② 放行后): 按弧内字数占比铺带, 上层叠稿层语态 */
function realAxis(d) {
  const arcs = d.arcs || [], byArc = d.lines_of_arc || {};
  const rows = [];
  arcs.forEach(a => (byArc[a.arc_id] || []).forEach(l => rows.push({ ...l, aid: a.arc_id })));
  if (!rows.length) return null;
  const chars = rows.map(r => Math.max(1, String(r.text || '').length));
  const total = chars.reduce((x, y) => x + y, 0);
  const script = rows.map((r, i) => ({
    label: '', w: +(chars[i] / total * 100).toFixed(2),
    color: MODE_COLOR[r.mode] || '#334155',
  }));
  const bands = arcs.map(a => {
    const len = chars.reduce((s, c, i) => s + (rows[i].aid === a.arc_id ? c : 0), 0);
    return {
      label: a.world ? a.arc_id + ' ' + a.world.slice(0, 6) : a.arc_id,
      w: +(len / total * 100).toFixed(2),
      color: TYPE_COLOR[a.type] || '#22304a',
    };
  });
  return { script, bands, est: Math.round(total / 4.5), n: arcs.length };
}

function progressRows(d) {
  const G = d.gates || {}, C = d.counts || {};
  const rows = STEPS.filter(s => s.gate && ['triage', 'arcs', 'shots', 'direct'].includes(s.gate));
  return rows.map(s => {
    const done = stepDone(s, G);
    const meta = ZH[s.id] || { label: s.label, hint: () => '' };
    const hint = meta.hint(C);
    const cls = done ? 'prow done' : (s.id === d._nextId ? 'prow now' : 'prow');
    const st = done ? '✓ 已放行 · 进入 →'
      : (s.id === d._nextId ? '当前这一步 →' : (s.page ? '待前段 →' : '待建'));
    const inner = `<span class="pn">${s.n}</span><span class="plabel">${esc(meta.label)}</span>`
      + `<span class="pv">${esc(hint)}</span><span class="pst">${st}</span>`;
    return s.page
      ? `<a class="${cls}" href="${esc(s.page + location.search)}">${inner}</a>`
      : `<div class="${cls}">${inner}</div>`;
  }).join('');
}

async function refresh() {
  try {
    const d = await (await fetch(`${BASE}/state`)).json();
    el('sub').textContent = `${d.book_title} · 第${d.ep}集`;
    el('line-badge').innerHTML = d.line === 'v3'
      ? '<span class="badge ok">v3 线 ✓</span>'
      : '<span class="badge warn">未开通 v3 (缺省 v2)</span>';

    if (d.line !== 'v3') {
      rail(el('rail'), { line: d.line, gates: {} });
      timeline(el('axis'), { caption: '本书走 v2 工坊线 (存量不动) — 开通 v3 后此处显示真实弧带' });
      el('body').innerHTML = `<div class="note">本书走 <b>v2 工坊线</b> (存量不动)。开通 v3: 写
         outputs/动画/_资产/产线绑定_${esc(d.book_title)}.json → {"line":"v3"} 后刷新。</div>`;
      return;
    }

    const pend = firstPending(d.gates || {});
    d._nextId = pend ? pend.id : null;
    rail(el('rail'), { line: d.line, gates: d.gates, active: pend ? pend.id : undefined });

    // 轴: 有弧 → 真实弧带; 无 → 基准九段带占位
    let axis = null;
    if ((d.counts || {}).arcs) {
      try { axis = realAxis(await (await fetch(`${BASE}/concept`)).json()); } catch (_) { axis = null; }
    }
    if (axis) {
      timeline(el('axis'), {
        script: axis.script, bands: axis.bands,
        axisLabel: `~${axis.est}s 估`,
        caption: `真实弧带 · ${axis.n} 弧 (上层=稿层语态, 下层=弧型)`,
      });
    } else {
      timeline(el('axis'), { caption: '基准九段带 (三型弧模型) — 真实弧带在 ②立意 放行后生成' });
    }

    const head = pend
      ? `<b>下一步: ${pend.n} ${esc(ZH[pend.id] ? ZH[pend.id].label : pend.label)}</b> — `
        + (pend.page ? `<a href="${esc(pend.page + location.search)}">进入 →</a>`
                     : '<span class="note">该页建设中 (V4)</span>')
      : '<b>文字阶段四步已放行</b> — 可进 ⑤K2 / ⑥TTS (建设中)';
    el('body').innerHTML = `<div class="progress">${progressRows(d)}</div>
      <div class="nextline">${head}</div>
      <div class="note">闸态看顶部八步轨 (🔒待前段 / ✓已放行) — 规范见 docs/improvements/进行中-20261105-分镜续跑升级.md</div>`;
  } catch (err) {
    el('sub').textContent = '状态加载失败: ' + err.message;
  }
}
refresh();
