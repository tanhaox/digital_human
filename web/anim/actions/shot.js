// ── 镜级动作: 过审/打回/重roll/AI修/重规划/品牌标/文字层/动画拍 ──
import { $ } from '../util.js';
import { api } from '../api.js';
import { toast, closeDialog } from '../ui.js';
import {
  redoDialog, textLayerDialog, animEditDialog, aiFixDialog, tlRow,
  collectTextLayer, collectAnimMotions,
} from '../dialogs.js';

/**
 * @param ctx wireActions 组装的共享上下文
 */
export function wireShotActions({ store, refresh, shot, shotApi, startJob }) {
  return {
    async approve({ sid }) {
      const r = await api('/approve', { ok: [sid] });
      toast(`${sid} 已过审 · ${r.summary}`);
      refresh();
    },
    redo({ sid }) { redoDialog(sid, shot(sid)); },
    async redoSubmit({ sid }) {
      const note = $('#rd-note').value.trim();
      const promptTxt = $('#rd-prompt').value.trim();
      const neg = $('#rd-neg') ? $('#rd-neg').value.trim() : '';
      if (!note) { toast('打回必须填理由 (留痕)', true); return; }
      const r = await api('/approve', { redo: [{ sid, note,
        negative: neg || undefined, image_prompt_zh: promptTxt || undefined }] });
      closeDialog();
      const how = promptTxt ? '(定向: 描述已更新)'
        : (neg ? `(负面词剔除: ${neg})` : '(换seed重抽卡)');
      toast(`${sid} 已打回 ${how} · ${r.summary}`);
      refresh();
    },
    // 0920 单镜生视频: approved 卡的下一步出口 (配套 card.js shotH3 按钮)
    shotH3: ({ sid }) => startJob('/h3', { only: [sid] },
      `🎬 生成 ${sid} 动画 (H3 图生视频, ~100s, GPU)?`),
    reroll: ({ sid }) => startJob('/h3', { reroll: [sid], only: [sid] },
      `重roll ${sid} 视频 (换 h3_seed, 只跑该镜)?`),
    aiFix({ sid }) { aiFixDialog(sid); },
    async aiFixSubmit({ sid }) {
      const note = $('#af-note').value.trim();
      closeDialog();
      toast(`${sid} AI 医生思考中… (10-30s)`);
      const r = await shotApi(sid, 'ai-fix-motion', { note });
      toast(`${sid} ✓ ${r.notes} · 换seed已换 — 重roll启动`);
      await startJob('/h3', { reroll: [sid], only: [sid] });
    },
    async shotReplan({ sid }) {
      if (!confirm(`♻️ 重规划此镜 ${sid} — 一条龙:\nLLM重设计(30-60s) → 生图(~15s) → 自动过审 → 生视频(~100s)`
        + '\n时间槽/口播/兄弟镜全不动 (自动备份)。全程约3分钟, 进度看下面任务面板。继续?')) return;
      toast(`${sid} ♻️ 单镜重规划中… (LLM 30-60s, 请稍候)`);
      await shotApi(sid, 'replan', {});
      toast(`${sid} ✓ 重设计完成 — 生图+生视频一条龙启动`);
      await startJob('/k2', { only: [sid], chain_h3: true });
    },
    async brandTag({ sid, on }) {
      const flag = on === 'true';
      if (!confirm(`${flag ? '🏔 标为品牌卡镜 ' : '🗑 剥掉品牌卡标 '}${sid}\n`
        + `${flag ? '→ 零 GPU 直通, 草稿插定稿卡' : '→ 回普通生成队列 (走 K2/H3)'} (自动备份)。继续?`)) return;
      const r = await shotApi(sid, `brand-tag?on=${flag}`, {});
      toast(`${sid} ✓ brand_card=${r.brand_card} — 重建草稿生效`);
      refresh();
    },
    // 0921 品牌镜单镜直通 (配套 card.js brandPass 按钮): 零 GPU 秒级, 不走任务面板
    async brandPass({ sid }) {
      const r = await shotApi(sid, 'brand-pass', {});
      toast(`${sid} ✓ ${r.noop ? '已是定稿卡' : '品牌卡直通完成 (零GPU)'} — 重建草稿生效`);
      refresh();
    },
    textLayer({ sid }) { textLayerDialog(sid, shot(sid)); },
    tlAdd({ col }) {
      document.getElementById(col === 'cap' ? 'tl-cap' : 'tl-fx')
        .insertAdjacentHTML('beforeend', tlRow({}, col));
    },
    tlRowDel(_, btn) { btn.closest('.tl-row').remove(); },
    async tlSave({ sid }) {
      const entries = collectTextLayer($('#dlg'));   // 圈定弹窗作用域, 防串数据
      const r = await shotApi(sid, 'text-layer', { entries });
      closeDialog();
      toast(`${sid} 文字层已存 (字幕 ${entries.filter(e => e.kind === 'caption').length}`
        + ` / 花字 ${entries.filter(e => e.kind !== 'caption').length})`);
      refresh();
    },
    animEdit({ sid }) {
      if (!animEditDialog(sid, shot(sid))) toast('该镜无动画节拍', true);
    },
    async animSave({ sid }) {
      const motions = collectAnimMotions($('#dlg'));
      const r = await shotApi(sid, 'anim', { motions });
      closeDialog();
      toast(`${sid} 动画已改 (${r.updated}/${r.beats} 拍) — 🎲重roll 生效`);
      refresh();
    },
  };
}
