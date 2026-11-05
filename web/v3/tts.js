// ⑥ TTS 页 (V6 薄壳: 复用音频线, 只读展示 + 试听 + 深链)
import { rail } from './rail.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/tts`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  rail($('rail'), { line: st.line, gates: st.gates, active: 'tts' });
  $('btn-audio').href = `/web/audio.html?book_id=${encodeURIComponent(B)}&ep_index=${E}`;

  if (!d.has_audio) {
    $('counts').innerHTML = '<span class="badge warn">无音频</span>';
    $('body').innerHTML = `<div class="panel empty-audio">
      <b>本集还没有产线音频包</b>
      <div class="note">${esc(d.reason || '去音频工坊生成')}<br>
      路径: 讲书页 → 🔊音频 → 生成/验收/钉选, 完成后回此页刷新。⑦ 盖章 (V5) 前必须就位。</div></div>`;
    $('stat').textContent = '🔒 ⑦ 盖章需要音频包 (实测时长来源)';
    return;
  }
  $('counts').innerHTML =
    `<span class="badge ok">✓ 音频包</span>` +
    `<span class="badge">${d.blocks.length} 块</span>` +
    `<span class="badge">~${d.total_s}s</span>` +
    (d.pinned ? '<span class="badge warn">钉选版</span>' : '');
  $('body').innerHTML = d.blocks.map(b => `
    <div class="blk">
      <span class="bno">b${String(b.i).padStart(2, '0')}</span>
      <span class="btx">${esc(b.text)}</span>
      <span class="bdur">${b.dur_s ?? '?'}s</span>
      <audio controls preload="none" src="${BASE}/tts/file/${b.i}"></audio>
    </div>`).join('');
  $('stat').textContent = `${d.blocks.length} 块 · ~${d.total_s}s — ${d.pinned ? '钉选版' : '最新验收版'} · 可进 ⑦ 盖章 (V5)`;
}

$('btn-refresh').onclick = boot;
boot();
