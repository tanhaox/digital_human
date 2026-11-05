// ── 顶栏视图: 标题/副行/计数徽章/按钮可用态/qc 链接 (store 订阅者) ──
// 按钮态全部来自 store (含 h3-retry), 不再回读 DOM — 单向数据流 (原 M10)
import { $, esc, stLabel, scenesReady } from './util.js';
import { fileUrl } from './api.js';
import { media, BOOK } from './context.js';

const STORY_URL = `/web/books_story.html?book_id=${encodeURIComponent(BOOK)}`;

/**
 * @param {{st: object|null, h3Retry: boolean, h3Reset: boolean}} state
 */
export function topbarView(state) {
  const st = state.st;
  if (!st) return;
  $('#title').textContent = `${st.book_title} · 第${st.ep}集`;
  $('#sub').innerHTML = sublineHtml(st);
  renderCounts(st.counts || {});
  renderButtons(st, state.h3Retry, state.h3Reset);
  renderBrandPreview(st);
  $('#qc-link').hidden = !st.planned;
  $('#qc-link').href = fileUrl('qc_sheet.html', media.ver);
}

/** 品牌卡预览链接: 资产存在则显示 (1006 用户令: 生成后无预览入口) */
function renderBrandPreview(st) {
  const el = $('#brand-preview');
  if (!el) return;
  // 品牌卡镜已 img_done 且挂了 _资产 路径 → 资产已生成
  const brandShot = (st.shots || []).find(s => s.brand_card && s.image_file);
  if (brandShot) {
    el.hidden = false;
    el.href = fileUrl(brandShot.image_file, media.ver);
    el.title = `品牌卡已生成 (${brandShot.image_file.split('/').pop()}) — 点击预览`;
  } else {
    el.hidden = true;
  }
}

function sublineHtml(st) {
  const back = `<a href="${STORY_URL}">← 讲书页</a>`;
  // 1101 U9: 音频换轨横幅 (stale=分镜按旧音频规划 — reroll/再生成过)
  const au = st.audio;
  const auLine = au && au.stale
    ? ` · <b style="color:#f87171">⚠ 音频已换轨</b> (reroll/再生成) — <b>🎚对齐落盘</b> 拉齐 或 音频页重新验收采用`
    : (au && au.pinned ? ` · 🎧验收版 (钉选 ${au.packs} 包)` : '');
  const lost = au && au.pinned_lost ? ` · <b style="color:#f87171">验收版已被重建删除 (回退最新)</b>` : '';
  // 1105 批1: 软警告上屏 (plan_warnings 写侧早已落盘, 读侧从未投影) —
  // title 列前 3 条, 点悬停即见; 不阻断任何操作 (软项本就走人检)
  const warns = st.plan_warnings || [];
  const warnLine = warns.length
    ? ` · <span title="${esc(warns.slice(0, 3).join('\n'))}">⚠软警告 ${warns.length}</span>` : '';
  if (st.planned) {
    return `${esc(st.ep_title || '')} · 第${st.ep}集 · ${st.total} 镜${st.aligned ? ' · 已对齐' : ''}${auLine}${lost}${warnLine} · ${back}`;
  }
  return st.audio_ready
    ? `音频已就绪 ✓ 可规划${auLine} · ${back}`
    : `音频先行: 先到讲书页「🔊 TTS音频」生成并 ✔验收采用口播 · ${back}`;
}

function renderCounts(c) {
  // 0921: 计数徽章可点 — 连点循环定位该状态的镜 (已批=缺视频, 待生图=缺图, 直达页内位置)
  $('#counts').innerHTML = Object.entries(c)
    .map(([k, v]) => `<button type="button" class="badge st-${esc(k)}" data-act="statLocate"`
      + ` data-st="${esc(k)}" title="点击定位: 依次跳到「${esc(stLabel(k))}」的镜 (连点轮换)">${stLabel(k)} ${v}</button>`).join('');
}

function renderButtons(st, h3Retry, h3Reset) {
  const c = st.counts || {};
  const busy = !!st.running_job;
  const noAudio = !st.audio_ready;
  // 1101 A4: 无音频禁用全部吃音频的相位 (旧版只挡 plan — K2 点了才在闸上炸误导排障)
  const audioGate = noAudio ? '音频先行: 先到讲书页生成并 ✔验收采用口播' : '';
  $('#btn-plan').disabled = busy || noAudio;
  $('#btn-plan').title = audioGate || (noAudio ? '音频先行 (0912 架构令)' : '');
  $('#btn-plan').textContent = st.planned ? '🧭 重新规划-全清(立意闸)' : '🧭 规划(立意闸)';
  $('#btn-plan-scenes').hidden = !scenesReady(st);
  $('#btn-plan-scenes').disabled = busy || noAudio;
  // 1105 批1: tooltip 吃 resume_todo 明细 (缺 N 场 / 含带病重开 M) —
  // 音频闸文案优先 (它才是 disabled 的原因)
  $('#btn-plan-scenes').title = audioGate || (() => {
    const rt = st.resume_todo;
    if (!rt || !rt.ready) return '';
    const nSick = Object.keys(rt.sick || {}).length;
    return `续跑 ${rt.todo.length} 场${nSick ? ` (含带病重开 ${nSick})` : ''} — 每场 1-2 分钟, 零 GPU`;
  })();
  $('#btn-concept-open').hidden = !((st.arcs || []).length);  // 1005 具象化改判: 有骨架即可唤起立意面板
  $('#btn-k2').disabled = busy || !(c.planned > 0) || noAudio;
  $('#btn-k2').title = audioGate;
  $('#btn-approve-all').disabled = !(c.img_done > 0);
  // 1101 D1: h3Reset 模式下全集可重渲 (reset 清历史后全镜回 approved)
  $('#btn-h3').disabled = busy || noAudio ||
    !((c.approved || 0) > 0 || (h3Retry && c.anim_fail > 0)
      || (h3Reset && (st.total || 0) > 0));
  $('#btn-h3').title = audioGate;
  $('#btn-align-dry').disabled = $('#btn-align').disabled = busy || !st.planned;
  $('#btn-align-dry').title = $('#btn-align').title = audioGate;
  $('#btn-draft').disabled = busy || !st.planned;
  $('#btn-draft').title = audioGate;
}
