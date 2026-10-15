// ── 动作中枢: 组装各域 handler + 全页唯一 click 委托 (data-act) ──
// 为什么委托而不是内联 onclick: ① id 拼进属性 JS 字符串 = 注入面
// ② 内联 handler 要求全局函数 ③ CSP 不兼容。dataset 取参天然安全。
import { api } from './api.js';
import { toast, closeDialog, setDialogTrigger } from './ui.js';
import { stLabel } from './util.js';
import { wireFlowActions } from './actions/flow.js';
import { wireShotActions } from './actions/shot.js';
import { wireArcActions } from './actions/arc.js';
import { wireConceptActions } from './actions/concept.js';
import { wireDraftActions } from './actions/draft.js';
import { wireCartActions } from './cart.js';

/**
 * @param {{store: object, runner: object, refresh: () => void}} deps
 */
export function wireActions({ store, runner, refresh }) {
  const getSt = () => store.get().st || {};
  const shot = sid => (getSt().shots || []).find(x => x.shot_id === sid) || {};
  const shotApi = (sid, tail, body) => api(`/shot/${encodeURIComponent(sid)}/${tail}`, body);

  /** 发起后端任务: 确认 → 清日志 → 调用 → 挂面板 → 稍后刷状态 */
  async function startJob(path, body, msg) {
    if (msg && !confirm(msg)) return;
    runner.clearLog();                  // 先清: api() 失败时不残留上个任务的日志误导排查
    const j = await api(path, body || {});
    runner.attach(j);
    toast(`${j.phase} 已启动 (${j.id.slice(0, 8)})`);
    setTimeout(refresh, 800);
  }

  // 各域 handler 合并 (域内函数 ≤50 行, 中枢只做装配)
  const ctx = { store, runner, refresh, getSt, shot, shotApi, startJob };
  // 循环定位游标: 同一 chip 连点依次跳下一镜; 重渲染换 DOM 后自动回退第一镜
  let lastLoc = null;
  const H = {
    ...wireFlowActions(ctx),
    ...wireShotActions(ctx),
    ...wireArcActions(ctx),
    ...wireConceptActions(ctx),
    ...wireDraftActions(ctx),
    ...wireCartActions(ctx),
    dlgClose: closeDialog,
    // 统计 chip 定位 (0920): 点"待生图 2"→滚到该范围此状态的镜并闪两下。
    // 0921 升级循环定位: 连点依次跳下一镜 (顶栏"已批 4"一路点下去把缺视频的镜挨个过一遍)。
    // 范围随 chip 所在层级: 顶栏=全板, 组头=本组, 场头=整场。data-st 是内部状态枚举,
    // 白名单校验后才进 class 选择器 (不信任任何拼进 selector 的字符串)。
    statLocate: (ds, el) => {
      if (!/^[a-z_]+$/.test(ds.st || '')) return;
      const scope = el.closest('.group-row') || el.closest('.scene')
        || document.getElementById('board') || document.body;
      const cards = [...scope.querySelectorAll('.card')]
        .filter(c => c.querySelector(`.badge.st-${ds.st}`));
      if (!cards.length) { toast('该范围内没有这种状态的镜', true); return; }
      const i = lastLoc && lastLoc.st === ds.st && scope.contains(lastLoc.el)
        ? (cards.indexOf(lastLoc.el) + 1) % cards.length : 0;
      const card = cards[i];
      lastLoc = { st: ds.st, el: card };
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      for (const c of document.querySelectorAll('.card.locate-flash')) c.classList.remove('locate-flash');
      void card.offsetWidth;                     // 重启动画 (连点同 chip 也闪)
      card.classList.add('locate-flash');
      toast(`${stLabel(ds.st)} ${i + 1}/${cards.length} · ${card.id.slice(5)}`);
    },
  };

  // 1010 R 标清除 (用户令): 点播放 = "看过了" → 撤该镜的重做待复查标。
  // media 的 play 事件不冒泡, 只能走捕获; 只在卡上真有 R 标时动作 (无标零请求),
  // 本地先摘标保即时反馈, 后端失败静默 (刷新会按盘上真值重画, 自愈)。
  document.addEventListener('play', ev => {
    const card = ev.target.closest?.('.card');
    const badge = card?.querySelector('.badge.bd-r');
    if (!badge) return;
    badge.remove();
    api(`/shot/${encodeURIComponent(card.id.slice(5))}/viewed`, {}).catch(() => {});
  }, true);

  // 统一错误边界 + 防双击: handler 想抛就抛, 这里兜底 toast
  document.addEventListener('click', async ev => {
    const t = ev.target.closest('[data-act]');
    if (!t || t.disabled) return;
    const h = H[t.dataset.act];
    if (!h) return;
    const text = t.textContent, disabled = t.disabled;
    setDialogTrigger(t);                    // 禁用会抢焦点, 先登记 (弹窗关闭后归还)
    t.disabled = true;                       // 防重复提交
    try { await h(t.dataset, t); }
    catch (e) {
      // fetch 网络层失败 (断网/超时/服务未起) 抛 TypeError — 提示可重试; HTTP 业务错误走 ApiError
      toast(e instanceof TypeError ? `网络异常 (可重试): ${e.message}` : e.message, true);
    }
    finally { t.disabled = disabled; t.textContent = text; }
  });
}
