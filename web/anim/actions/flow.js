// ── 整集流程动作: 规划/K2/H3/品牌卡/对齐/过审/重排/停止 ──
import { $ } from '../util.js';
import { api } from '../api.js';
import { media } from '../context.js';
import { toast } from '../ui.js';

/**
 * @param ctx wireActions 组装的共享上下文
 * @returns {Record<string, Function>} handler 表
 */
export function wireFlowActions({ store, runner, refresh, getSt, startJob }) {
  /** 对齐: v3 原生对齐单协议 (1101 H3: v1 legacy 双协议随 v1 分支退役删除) */
  async function doAlign(dry) {
    const r = await api('/align', { dry_run: dry });
    if (dry) {
      const iss = (r.issues || []).map(x => '  ⚠ ' + x).join('\n');
      const gps = (r.gaps || []).map(x => `  ⚠ 轴隙 ${x.from}→${x.to} ${x.gap_s}s`).join('\n');
      runner.appendLog(`== 时间轴体检 (v3 原生对齐) ==\n镜 ${r.stats['镜数']} · 成片 ≈ ${Math.round(r.ep_len_s)}s`
        + ` · 尾部覆盖差 ${r.stats['尾部覆盖差s']}s\n${iss}${gps}${(iss || gps) ? '' : '  全部 OK ✓'}`);
      toast(`体检: 问题 ${r.stats['硬校验问题']} · 轴隙 ${r.stats['轴隙']}`,
        (r.stats['硬校验问题'] + r.stats['轴隙']) > 0);
    } else {
      toast(`落盘 ✓ v3 原生对齐 · 备音轨 + 音效清单 ${r.stats['音效点']} 点 · 问题 ${r.stats['硬校验问题']}`);
      refresh();
    }
  }

  return {
    async plan() {
      const st = getSt();
      const wipe = st.planned ? confirm(
        '🧭 全清重来: 规划/图/视频全部清除, 整集从零重新规划 (旧版自动备份)。\n要重做个别场请改用场头 🔁 按钮。继续?') : false;
      if (st.planned && !wipe) return;
      // 0917 立意人闸: 切场+隐喻确认轮即停, 立意.md 落盘 — 人工确认后续跑 ▶️分镜
      await startJob('/plan', { force: wipe, scorched: true, concept_only: true });
    },
    planScenes: () => startJob('/plan-scenes', {},
      '▶️ 分镜续跑: 按已确认的立意.md 逐场分镜 (每场 1-2 分钟 LLM, 零 GPU)。\n续跑前请先人工过目 立意.md (隐喻撞车警告在内)。继续?'),
    enhance: () => startJob('/enhance', {},
      '🖌 手动增强 (1101 治理版): 只修非 writer 镜 (fallback/器层) 的画面丰富度与动画层 — writer 五件套镜默认跳过 (修复走单镜🔁), 多拍镜只跟锚不动拍; 触及已生成镜会清媒体回 planned 重跑。继续?'),
    k2: () => startJob('/k2', {},
      '🎨 K2 批生图: 只生 planned 状态的镜 (~15s/镜), 已有图/视频不动。继续?'),
    // retry_failed 读 store (复选框 change 即写入), 不回读 DOM — 单向数据流
    h3: () => {
      const { h3Retry, h3Reset } = store.get();
      if (h3Reset) {
        // 1101 D1: 全量重渲露出为显式动作 (reset 语义 UI 此前发不出来, confirm
        // 文案却一直声称会清全部 — 文案与行为不符实锤)。二次确认钉死破坏面。
        if (!confirm('💥 全量重渲: 清除全部已生成视频历史 (anim_done 一并回 approved 重跑),\n'
          + '图保留 · ~100s/镜 · 全集重烧 GPU。确定?')) return;
        return startJob('/h3', { reset: true });
      }
      return startJob('/h3', { retry_failed: h3Retry },
        '🎬 H3 批生视频: 只生成「已过审」镜 (~100s/镜); 已成片 (anim_done) 不动;\n'
        + (h3Retry ? '勾选了⚡连带重试败镜: anim_fail 一并重跑 (换seed)。\n' : '')
        + '要重做成片镜: 单镜🎲重roll / 购物篮♻️ / 勾选💥全量重渲。继续?');
    },
    brand: () => startJob('/brand', {},
      '🏔 生成本书品牌卡: 骨架冻结(登山客三拍) + 本书动画风格与色板 (~2min GPU, 一次生成全系列复用)。\n旧全局卡将被本书卡取代 (仪式句改后期金字)。继续?'),
    alignDry: () => doAlign(true),
    // 1101 D2: 对齐落盘是重写相位 (重钉/归真/拆镜/媒体裁决), 不是纯体检 — confirm 说清
    align: async () => {
      if (!confirm('🎚 对齐落盘: 槽镜重钉 + 句级归真 + 拆镜重设计 (LLM) + 失效媒体裁决\n'
        + '(文案变/漂移镜清图或清片) + beats 自愈 + 音效清单刷新。\n'
        + '这是重写相位不是纯体检 — 试算不跑这些步骤。继续?')) return;
      await doAlign(false);
    },
    async approveAll() {
      if (!confirm('全部 img_done 镜过审?')) return;
      const r = await api('/approve', { ok: 'all' });
      toast(`过审 ${r.approved} 镜 · ${r.summary}`);
      refresh();
    },
    async renumber() {
      if (!confirm('🔢 重排镜号 — 按时间轴把全部镜重排为 s1..sN\n已生成的图/视频文件同步改名, 自动备份。规划产物本身不动。继续?')) return;
      const r = await api('/renumber', {});
      media.bump();   // 文件同名换内容, 必须破缓存
      toast(`✓ 已重排 s1..s${r.shots} (改名 ${(r.mapping || []).length} 处) — 备份 ${r.backup}`);
      refresh();
    },
    cancel: () => runner.cancel(),
  };
}
