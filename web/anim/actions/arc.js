// ── 场级动作: 重规划本场 / K2 本场 / 生视频本场 (自动过审待人检镜) ──
import { api } from '../api.js';
import { toast } from '../ui.js';

/**
 * @param ctx wireActions 组装的共享上下文
 */
export function wireArcActions({ getSt, startJob }) {
  return {
    async arcReplan({ arc }) {
      const arcShots = (getSt().shots || []).filter(s => s.arc_id === arc);
      if (!arcShots.length) { toast('本场没有镜', true); return; }
      const done = arcShots.filter(s => ['img_done', 'approved', 'anim_done'].includes(s.status)).length;
      const msg = `🔁 重规划本场 ${arc}: ${arcShots.length} 镜全部重切 (LLM, 1-2 分钟, 其余场不动)`
        + (done ? `\n⚠ 本场已有 ${done} 镜生成产物, 将随旧镜退役 (盘上有备份)` : '')
        + '\n规划完 → 🎨 K2 本场 → 🎬 生视频本场 — 反复磨合直到抖音级。继续?';
      await startJob('/replan', { arc_id: arc }, msg);
    },
    async arcK2({ arc }) {
      const ids = (getSt().shots || []).filter(s => s.arc_id === arc && s.status === 'planned').map(s => s.shot_id);
      if (!ids.length) { toast('本场没有待生图的镜', true); return; }
      await startJob('/k2', { only: ids }, `K2 本场 ${arc}: ${ids.length} 镜 (~15s/镜). 继续?`);
    },
    async arcRetryK2({ arc }) {
      // 0923 补生图: 打回/跳过/失败/违禁标记的; img_done+text_suspect 需先打回
      const arcShots = (getSt().shots || []).filter(s => s.arc_id === arc);
      const planned = arcShots.filter(s =>
        s.status === 'planned' &&
        ((s.attempts && s.attempts.k2 > 0) || s.reject_note || s.text_suspect));
      const suspect = arcShots.filter(s => s.status === 'img_done' && (s.text_suspect || s.reject_note));
      if (!planned.length && !suspect.length) { toast('本场没有需补生图的镜', true); return; }
      if (suspect.length && !planned.length) {
        toast(`⚠ ${suspect.length} 镜有违禁标记但已生成 — 需先在卡片上打回 (🔁) 再补生图`, true);
        return;
      }
      const ids = planned.map(s => s.shot_id);
      await startJob('/k2', { only: ids },
        `🔄 补生图 ${arc}: ${ids.length} 镜 (打回/跳过/失败/违禁重试, ~15s/镜)`);
    },
    async arcH3({ arc }) {
      const arcShots = (getSt().shots || []).filter(s => s.arc_id === arc);
      const imgDone = arcShots.filter(s => s.status === 'img_done').map(s => s.shot_id);
      const approved = arcShots.filter(s => s.status === 'approved').map(s => s.shot_id);
      const animFail = arcShots.filter(s => s.status === 'anim_fail').map(s => s.shot_id);
      if (!imgDone.length && !approved.length && !animFail.length) { toast('本场没有可生成视频的镜', true); return; }
      const ids = [...new Set([...imgDone, ...approved, ...animFail])];
      const msg = `🎬 生视频本场 ${arc}: ${ids.length} 镜 (~100s/镜)`
        + (imgDone.length ? `，含自动过审 ${imgDone.length} 镜待人检图` : '')
        + (animFail.length ? `，含重试失败镜 ${animFail.length}` : '') + '。继续?';
      if (!confirm(msg)) return;
      if (imgDone.length) await api('/approve', { ok: imgDone });   // 自动过审待人检镜
      // 0924 修: anim_fail 不走 approve (端点只认 img_done), 走 H3 的 retry_failed
      await startJob('/h3', { only: ids, retry_failed: animFail.length > 0 });
    },
  };
}
