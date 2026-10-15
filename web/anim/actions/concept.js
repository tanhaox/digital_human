// ── 立意面板动作: 编辑切换 / 单场保存 / 整案保存 / 单场重掷 ──
import { $ } from '../util.js';
import { api } from '../api.js';
import { media } from '../context.js';
import { toast } from '../ui.js';
import { saveConcept, toggleConceptEdit, resetConceptPanel, toggleCollapse, forceOpen } from '../concept.js';

/**
 * @param ctx wireActions 组装的共享上下文
 */
export function wireConceptActions({ refresh }) {
  /** 保存成功后的统一收尾: 破缓存 (立意.md 内容变了) → 回只读态 → 刷状态 */
  function afterConceptWrite(msg) {
    toast(msg);
    media.bump();
    resetConceptPanel();
    refresh();
  }

  return {
    conceptToggle: () => toggleConceptEdit(),
    conceptCollapse: () => toggleCollapse(),  // 0927 收起/展开 (腾分镜预览空间)
    conceptForce: () => { forceOpen(); },      // 1005 具象化改判: 手动唤起/收起立意面板
    async conceptSaveOne({ arc }) {
      const card = document.querySelector(`#concept-edit details[data-arc="${CSS.escape(arc)}"]`);
      if (!card) return;
      await saveConcept(card);
      afterConceptWrite(`${arc} 立意已保存`);
    },
    async conceptSaveAll() {
      await saveConcept(null);
      afterConceptWrite('立意已保存 (整案)');
    },
    async conceptReroll(_, btn) {
      const arc = $('#concept-arc').value, dir = $('#concept-dir').value.trim();
      if (!arc) return;
      if (!confirm(`🎲 重掷 ${arc} 立意 (只动这一场, ~30s)?${dir ? '\n方向: ' + dir : ''}`)) return;
      btn.textContent = '⏳ 重掷中…';
      try {
        const j = await api('/concept-reroll', { arc_id: arc, direction: dir });
        afterConceptWrite(`${arc} 新隐喻: ${j.visual_metaphor}`);
      } finally { btn.textContent = '重掷此场'; }
    },
  };
}
