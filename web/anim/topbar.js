// ── 顶栏视图: 标题/副行/计数徽章/按钮可用态/qc 链接 (store 订阅者) ──
// 按钮态全部来自 store (含 h3-retry), 不再回读 DOM — 单向数据流 (原 M10)
import { $, esc, stLabel, scenesReady } from './util.js';
import { fileUrl } from './api.js';
import { media, BOOK } from './context.js';

const STORY_URL = `/web/books_story.html?book_id=${encodeURIComponent(BOOK)}`;

/**
 * @param {{st: object|null, h3Retry: boolean}} state
 */
export function topbarView(state) {
  const st = state.st;
  if (!st) return;
  $('#title').textContent = `${st.book_title} · 第${st.ep}集`;
  $('#sub').innerHTML = sublineHtml(st);
  renderCounts(st.counts || {});
  renderButtons(st, state.h3Retry);
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
  if (st.planned) {
    return `${esc(st.ep_title || '')} · ${st.total} 镜${st.aligned ? ' · 已对齐' : ''} · ${back}`;
  }
  return st.audio_ready
    ? `音频已就绪 ✓ 可规划 · ${back}`
    : `音频先行: 先到讲书页「🔊 TTS音频」生成并人耳验收口播 · ${back}`;
}

function renderCounts(c) {
  // 0921: 计数徽章可点 — 连点循环定位该状态的镜 (已批=缺视频, 待生图=缺图, 直达页内位置)
  $('#counts').innerHTML = Object.entries(c)
    .map(([k, v]) => `<button type="button" class="badge st-${esc(k)}" data-act="statLocate"`
      + ` data-st="${esc(k)}" title="点击定位: 依次跳到「${esc(stLabel(k))}」的镜 (连点轮换)">${stLabel(k)} ${v}</button>`).join('');
}

function renderButtons(st, h3Retry) {
  const c = st.counts || {};
  const busy = !!st.running_job;
  const noAudio = !st.audio_ready;
  $('#btn-plan').disabled = busy || noAudio;
  $('#btn-plan').title = noAudio ? '音频先行 (0912 架构令): 先在讲书页生成口播并人耳验收' : '';
  $('#btn-plan').textContent = st.planned ? '🧭 重新规划-全清(立意闸)' : '🧭 规划(立意闸)';
  $('#btn-plan-scenes').hidden = !scenesReady(st);
  $('#btn-plan-scenes').disabled = busy;
  $('#btn-concept-open').hidden = !((st.arcs || []).length);  // 1005 具象化改判: 有骨架即可唤起立意面板
  $('#btn-k2').disabled = busy || !(c.planned > 0);
  $('#btn-approve-all').disabled = !(c.img_done > 0);
  $('#btn-h3').disabled = busy ||
    !((c.approved || 0) > 0 || (h3Retry && c.anim_fail > 0));
  $('#btn-align-dry').disabled = $('#btn-align').disabled = busy || !st.planned;
  $('#btn-draft').disabled = busy || !st.planned;
}
