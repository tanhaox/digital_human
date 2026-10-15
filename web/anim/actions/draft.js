// ── 剪映草稿与杂项动作 ──
import { $ } from '../util.js';
import { api } from '../api.js';
import { toast } from '../ui.js';

/**
 * @param ctx wireActions 组装的共享上下文
 */
export function wireDraftActions({ runner }) {
  return {
    async draft() {
      if (!confirm('装配剪映草稿? (开场套件+真片+品牌卡+口播音轨全挂上, 缺片镜用占位卡)')) return;
      toast('装配中…');
      let r;
      try {
        r = await api('/draft', {});
      } catch (err) {
        // 409占用/500骨架无镜等硬拦 — 此前无 catch 直接吞进控制台, 用户只见"装配中…"不落稿
        runner.appendLog('== 装配失败 ==\n' + (err.message || err));
        toast('装配失败: ' + (err.message || err), true);
        return;
      }
      runner.appendLog('== 剪映草稿 ==\n' + r.draft_name
        + `\n真材 ${r.real}/${r.real + r.placeholder}`
        + (r.short_holds?.length ? '\n定格补偿: ' + r.short_holds.join(', ') : '')
        + '\n→ 同名覆盖: 每集在剪映列表里始终只有这一份 (旧的时间戳草稿可在剪映里手动删)');
      // 0918 用户令: 装配完成给醒目提示 (别去草稿箱猜哪份最新)
      const badge = $('#latest-draft');
      badge.hidden = false;
      badge.textContent = `📄 草稿: ${r.draft_name} (点击复制)`;
      badge.dataset.name = r.draft_name;
      toast(`「${r.draft_name}」✓ 同名覆盖 · 剪映里就这一份 (0s 起 = 标题底图开场)`);
    },
    copyDraft(_, btn) {
      navigator.clipboard.writeText(btn.dataset.name || '')
        .then(() => toast('草稿名已复制'))
        .catch(() => toast('复制失败', true));
    },
  };
}
