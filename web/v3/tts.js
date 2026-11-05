// ⑥ TTS 页 — 复用音频线 + 块级操作 (重roll/变速/还原/基准/屏蔽/编辑, v2 同款端点)
import { rail } from './rail.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const ver = {};                     // id → 版本戳 (重roll/变速后换 URL)

async function api(method, url, body) {
  const r = await fetch(url, { method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.status);
  return d;
}

function releasePlayers() {         // Win 文件锁: 服务端换 wav 前先断流 (v2 同坑)
  document.querySelectorAll('audio').forEach(a => {
    try { a.pause(); a.removeAttribute('src'); a.load(); } catch { /* noop */ }
  });
}

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
  const vQ = ver.__combined ? `?v=${ver.__combined}` : '';
  const combinedRow = d.combined ? `
    <div class="blk combined">
      <span class="bno">🎙️</span>
      <div class="bmain"><div class="btx"><b>完整段落音频</b> (${d.combined.dur_s}s)</div></div>
      <button class="ob" id="btn-repin" title="段级 reroll/修复后从当前段 wav 重建大段, 并回写时长">🔄重拼</button>
      <span class="bdur">${d.combined.dur_s}s</span>
      <audio controls preload="none" src="${BASE}/tts/combined${vQ}"></audio>
    </div>` : '';
  $('body').innerHTML = combinedRow + d.blocks.map(b => `
    <div class="blk" data-id="${esc(b.id)}">
      <span class="bno">b${String(b.i).padStart(2, '0')}</span>
      <div class="bmain">
        <div class="btx">${esc(b.text)}</div>
        <div class="bops">
          <button class="ob" data-op="reroll" title="采样重生成 → 自动回听复检; 语气词/插字/吞字类大概率消失">↻重roll</button>
          <button class="ob" data-op="edit" title="统一编辑: 改字 + 停顿(气口) + 注音 — 一个文本框全搞定">✎编辑</button>
          <button class="ob" data-op="faster" title="技术变速 atempo (变速不变调): 每点快~5%, 从原始一次成型零损耗">⏩加速</button>
          <button class="ob" data-op="slower" title="减速一档 (x0.95), 回到1.0x自动还原原始">↩减速</button>
          <button class="ob" data-op="reset" title="还原原始 take, 清除累计倍率">⟲还原</button>
          <button class="ob" data-op="anchor" title="登记语速基准: 实测本行含气口字/s, 按区位写入音色 — 同区行自动校正回锚">📌基准</button>
          <button class="ob ob-warn" data-op="mute" title="本单移出播放+大段, 文稿段反勾选 (重生成不再出镜); 恢复=音频工坊重新勾选">🔇屏蔽</button>
        </div>
      </div>
      <span class="bdur">${b.dur_s ?? '?'}s</span>
      <audio controls preload="none" src="${BASE}/tts/file/${b.i}${ver[b.id] ? '?v=' + ver[b.id] : ''}"></audio>
    </div>`).join('');
  bindOps(d.blocks);
  const rp = $('btn-repin');
  if (rp) rp.onclick = async () => {
    rp.disabled = true; rp.textContent = '⏳';
    releasePlayers();
    try {
      const r = await api('POST', `/api/audio/jobs/${d.job_id}/rebuild-combined`);
      ver.__combined = Date.now();
      toast(`重拼 ✓ ${r.segments} 段 / ${r.duration}s`);
      await boot();
    } catch (e) { toast('重拼失败: ' + e.message); rp.disabled = false; rp.textContent = '🔄重拼'; }
  };
  $('stat').textContent = `${d.blocks.length} 块 · ~${d.total_s}s — ${d.pinned ? '钉选版' : '最新验收版'} · 可进 ⑦ 盖章 (V5)`;
}

function bindOps(blocks) {
  document.querySelectorAll('.blk[data-id]').forEach(row => {
    const id = row.dataset.id;
    if (!id) return;                       // combined 行无 data-id, 不属块操作族
    const b = blocks.find(x => String(x.id) === id) || {};
    if (!b.seg) {                   // 无段映射 (v2 同判): 不可重roll族, 只留试听
      row.querySelector('.bops').innerHTML =
        '<span class="bops-na">— 无段映射 (史前包), 编辑族去音频工坊 —</span>';
      return;
    }
    row.querySelectorAll('.ob[data-op]').forEach(btn => {
      btn.onclick = () => op(btn.dataset.op, id, btn, b);
    });
  });
}

async function op(opk, id, btn, b) {
  try {
    if (opk === 'edit') return openEditor(id, b);
    if (opk === 'mute'
        && !confirm(`屏蔽本行? (不再出镜)\n「${(b.text || '').slice(0, 40)}」\n\n本单移出播放+大段; 文稿段同步反勾选, 未来重生成也跳过。`)) return;
    if (opk === 'anchor'
        && !confirm('📌 把本行登记为语速基准? (实测含气口字/s, 按区位写入音色 — 重roll/重生成的同区行将自动校正回锚)')) return;
    if (opk === 'reroll' && !confirm('重roll 本行? (采样重生成 → 自动回听复检; 语气词/插字/吞字类大概率消失)')) return;
    const t0 = btn.textContent; btn.disabled = true; btn.textContent = '⏳';
    releasePlayers();
    const url = `/api/audio/files/${id}/${opk === 'faster' || opk === 'slower' || opk === 'reset' ? 'tempo' : opk}`;
    const payload = (opk === 'faster' || opk === 'slower' || opk === 'reset')
      ? { op: opk } : {};
    const r = await api('POST', url, payload);
    ver[id] = Date.now();
    const cps = r.cps ? ` ${r.cps}字/s` : '';
    toast(opk === 'reset' || (r.factor ?? 1) <= 1
      ? `${r.file || ''} ⟲已还原 ${r.duration ?? '?'}s${cps}`
      : opk === 'reroll' ? `✓ 重roll ${r.duration ?? '?'}s${(r.verdict?.diff || []).length ? ' · 疑点:' + r.verdict.diff.join('/') : ''}`
      : opk === 'anchor' ? `锚已存 ✓ ${r.rate}字/s (${ { hook: '钩子', transition: '过渡', cruise: '巡航' }[r.zone] || r.zone })`
      : opk === 'mute' ? '已屏蔽 (恢复=音频工坊重新勾选)'
      : `${r.file || ''} ${(r.factor ?? 0).toFixed(2)}x → ${r.duration ?? '?'}s${cps}${r.capped ? ' (已到上限)' : ''}`);
    await boot();
  } catch (e) { toast('失败: ' + e.message); btn.disabled = false; }
}

/* ✎编辑 modal (CSP: 全类名, 零内联 — v2 版是逐元素写内联样式, 不能照抄) */
function openEditor(id, b) {
  let ov = $('seg-editor');
  if (!ov) {
    ov = document.createElement('div'); ov.id = 'seg-editor'; ov.className = 'sekov';
    ov.innerHTML = `<div class="sebox">
      <div class="sehead"><b>✎ 段编辑 <span class="sesub">改字 · 注音 · 停顿</span></b>
        <button class="ob" id="se-close">✕</button></div>
      <textarea id="se-text" rows="5" class="seta"
        placeholder="直接改字; 任意位置插 -0.5s- 或 -1s- 垫停顿 (停顿垫在标记位置)"></textarea>
      <div class="sequick">气口快插:
        <button class="ob" data-mk="-0.5s-">↩ -0.5s-</button>
        <button class="ob" data-mk="-1s-">↩ -1s-</button>
        <span class="sesub">问句自动 1.2s · 大段落末自动 1s · -Xs- 光标处生效 (不用手动)</span></div>
      <input id="se-py" class="sepy" placeholder="注音 (可选): 字=拼音 空格分隔, 如 恰=QIA4 份=FEN4 — 系统性读错时用">
      <div class="sefoot"><span class="sesub">改字→三处同步(段/稿/书) · 停顿→合成后垫静音 · 注音→字级拼音标注</span>
        <button class="ob ob-go" id="se-go">重合成</button></div>
    </div>`;
    document.body.appendChild(ov);
    $('se-close').onclick = () => { ov.classList.remove('on'); };
    ov.querySelectorAll('.sequick [data-mk]').forEach(q => {
      q.onclick = () => {
        const ta = $('se-text');
        const pos = ta.selectionStart, mk = q.dataset.mk;
        ta.value = ta.value.slice(0, pos) + mk + ta.value.slice(ta.selectionEnd);
        ta.focus(); ta.setSelectionRange(pos + mk.length, pos + mk.length);
      };
    });
    $('se-go').onclick = () => submitEditor(id);
  }
  const ta = $('se-text');
  ta.value = String(b.text || '').replace(/-\d+(?:\.\d+)?s-\s*$/gm, '').trimEnd();
  $('se-py').value = '';
  ov.classList.add('on');
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
}

async function submitEditor(id) {
  const text = $('se-text').value.trim();
  const pyRaw = $('se-py').value.trim();
  if (!text) { toast('文本不能为空'); return; }
  const annotate = {};
  for (const m of pyRaw.matchAll(/([一-鿿])=([A-Za-z]+\d)/g)) annotate[m[1]] = m[2].toUpperCase();
  if (pyRaw && !Object.keys(annotate).length) {
    if (!confirm('注音没认出来。格式: 字=拼音 空格分隔 (如 恰=QIA4)\n\n确定 = 忽略注音, 只按改字重合成')) return;
  }
  const go = $('se-go'); go.disabled = true; go.textContent = '⏳';
  releasePlayers();
  try {
    const payload = { text };
    if (Object.keys(annotate).length) payload.annotate = annotate;
    const r = await api('POST', `/api/audio/files/${id}/reroll`, payload);
    ver[id] = Date.now();
    const notes = [];
    if (r.duration) notes.push(`${r.duration}s`);
    if ((r.verdict?.diff || []).length) notes.push(`疑点:${r.verdict.diff.join('/')}`);
    const pauses = (text.match(/-(\d+(?:\.\d+)?)s-/g) || []).length;
    if (pauses) notes.push(`气口×${pauses}`);
    toast(`✓ 编辑重合成 ${notes.join(' | ') || '完成'}`);
    $('seg-editor').classList.remove('on');
    await boot();
  } catch (e) { toast('编辑重合成失败: ' + e.message); }
  go.disabled = false; go.textContent = '重合成';
}

$('btn-refresh').onclick = boot;

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}
boot();
