// v3 入口页逻辑 (外置: CSP script-src 'self' 禁内联 — anim 家规)
import { timeline } from './timeline.js';
import { rail } from './rail.js';
const qs = new URLSearchParams(location.search);
const b = qs.get('book_id') || '', e = qs.get('ep_index') || '1';
const el = id => document.getElementById(id);
async function refresh() {
  try {
    const r = await fetch(`/api/anim/v3/ep/${encodeURIComponent(b)}/${e}/state`);
    const d = await r.json();
    el('sub').textContent = `${d.book_title} · 第${d.ep}集`;
    el('line-badge').innerHTML = d.line === 'v3'
      ? '<span class="badge ok">v3 线 ✓</span>'
      : '<span class="badge warn">未开通 v3 (缺省 v2)</span>';
    rail(el('rail'), { line: d.line, gates: d.gates, active: 'triage' });
    timeline(el('axis'), { caption: '基准九段带 (三型弧模型) — 真实弧带在 ②立意 放行后按 doc.v3.arcs 生成' });
    el('body').innerHTML = d.line === 'v3'
      ? '<b>下一步: ① 文稿分诊</b> — ' +
        (d.lines_done ? '本集已分诊过, <a href="./triage.html' + location.search + '">查看/续改 →</a>'
                      : '<a href="./triage.html' + location.search + '">进入分诊页 →</a>') +
        '<br><span class="note">放行闸: ' + JSON.stringify(d.gates) +
        ' · 规范见 docs/improvements/进行中-20261105-分镜续跑升级.md</span>'
      : `<div class="note">本书走 <b>v2 工坊线</b> (存量不动)。开通 v3: 写
         outputs/动画/_资产/产线绑定_${d.book_title}.json → {"line":"v3"} 后刷新。</div>`;
  } catch (err) {
    el('sub').textContent = '状态加载失败: ' + err.message;
  }
}
refresh();
