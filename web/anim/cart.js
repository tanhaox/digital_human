// ── 重做购物篮 (0921): 多镜勾选 + 底部悬浮篮条 + ♻️列队重做 ──
// sel 是纯 UI 态 (模块级 Set, 不进 store): 勾一格就 store.set 会整板重渲染 63 卡,
// 视频/图元素重建闪烁 — 勾选只动该 checkbox + 篮条; 板重渲染 (任务刷新) 后由
// syncBoard 把已勾态回画, 顺带 prune 已不存在的 sid (镜号重排/重生)。
// checkbox 走 change 冒泡而非 data-act click 中枢: 中枢会 disabled 元素, checkbox 会坏。
// localStorage 持久化 (键按书+集): F5/换页再回来篮子不丢 — 购物篮语义;
// 存取全包 try/catch (隐私窗口/禁存储也不炸页面), syncBoard 会 prune 已不存在的 sid。
import { BOOK, EP } from './context.js';
const LS_KEY = `anim_cart_${BOOK}_${EP}`;

function loadSel() {
  try {
    const v = JSON.parse(localStorage.getItem(LS_KEY) || '[]');
    return new Set(Array.isArray(v) ? v : []);
  } catch { return new Set(); }
}
function saveSel() {
  try { localStorage.setItem(LS_KEY, JSON.stringify([...sel])); } catch { /* 存不了就算了 */ }
}

let sel = loadSel();
let lastSt = {};   // 订阅时缓存最近状态 — cartClear/Dump 无 store 也能刷篮条

/** 板重渲染后回画勾选态 + prune 死 sid + 刷篮条。订阅顺序在 boardView 之后 (app.js 装配)。 */
function syncBoard(st) {
  const alive = new Set((st.shots || []).map(s => s.shot_id));
  if ([...sel].some(sid => !alive.has(sid))) {
    sel = new Set([...sel].filter(sid => alive.has(sid)));
    saveSel();
  }
  for (const ck of document.querySelectorAll('#board input[data-sid]')) {
    ck.checked = sel.has(ck.dataset.sid);
  }
}

function renderBar(st) {
  const bar = document.getElementById('cartbar');
  if (!bar) return;
  const busy = !!st.running_job;
  bar.hidden = sel.size === 0;
  document.getElementById('cart-count').textContent = `🧺 已选 ${sel.size} 镜`;
  const redo = bar.querySelector('[data-act="cartRedo"]');
  redo.disabled = busy;
  redo.title = busy
    ? '任务进行中 — 完成或 ⛔停止后再列队'
    : '逐镜 LLM重设计→生图→生视频 一条龙 (跳败续跑, 结束汇总)';
  const redoImg = bar.querySelector('[data-act="cartRedoImg"]');
  if (redoImg) {
    redoImg.disabled = busy;
    redoImg.title = busy
      ? '任务进行中 — 完成或 ⛔停止后再列队'
      : '逐镜 LLM重设计→生图 后停在人检 (✅过审/🔁打回), 不跑视频 — 看过图再决定';
  }
  // 1018 用户令: 修脸/压字独立按钮 — 四流程各管一层, 不联动 (顺序: 修脸→压字→生视频 自排)
  const face = bar.querySelector('[data-act="cartFace"]');
  if (face) {
    face.disabled = busy;
    face.title = busy ? '任务进行中 — 完成或 ⛔停止后再修脸'
      : '勾选镜逐脸局部重绘 (~10-60s/镜, 保构图身份); 已修过/无脸镜自动跳过';
  }
  const text = bar.querySelector('[data-act="cartText"]');
  if (text) {
    text.disabled = busy;
    text.title = busy ? '任务进行中 — 完成或 ⛔停止后再压字'
      : '勾选中申报了图内字的镜写入文字 (~15s/镜); 未申报镜自动跳过 — 建议在生视频前压';
  }
}

/**
 * 装配购物篮 (app.js 在三个视图订阅之后调用 — syncBoard 必须晚于 boardView)。
 * @param {{store: object}} deps
 */
export function initCart({ store }) {
  document.getElementById('board').addEventListener('change', ev => {
    const t = ev.target;
    if (!t.matches('input[data-sid]')) return;
    if (t.checked) sel.add(t.dataset.sid); else sel.delete(t.dataset.sid);
    saveSel();
    renderBar(store.get().st || {});
  });
  store.subscribe(({ st }) => { if (st) { lastSt = st; syncBoard(st); renderBar(st); } });
}

export const cartSize = () => sel.size;
export const cartClear = () => { sel.clear(); saveSel(); renderBar(lastSt); };

/** 取走篮内 sid 并清空 (发起列队时调用)。 */
export const cartDump = () => { const out = [...sel]; sel.clear(); saveSel(); renderBar(lastSt); return out; };

// ── 动作层: 挂进 actions.js 的 data-act 委托 ──
import { toast } from './ui.js';

/**
 * @param {{startJob: Function, shot: Function}} ctx actions.js 组装的共享上下端
 */
export function wireCartActions({ startJob, shot }) {
  return {
    cartClear: () => { cartClear(); toast('篮已清空'); },
    async cartRedo() {
      const n = cartSize();
      if (!n) { toast('篮是空的 — 先勾选镜头', true); return; }
      const mins = Math.max(1, Math.round(n * 3.5));
      if (!confirm(`♻️ 列队重做 ${n} 镜 — 一条龙:\n每镜 LLM重设计(30-60s) → 生图(~15s) → 自动过审 → 生视频(~100s)\n`
        + `全程约 ${mins} 分钟 · 进度看任务面板 · 镜间可 ⛔停止\n`
        + `时间槽/口播/兄弟镜不动, 每镜自动备份; 单镜失败跳过, 结束汇总。继续?`)) return;
      const sids = cartDump();
      await startJob('/redo-queue', { sids });
    },
    async cartRedoImg() {  // 0927 购物篮新路线: 到生图为止, K2 停 img_done 走人检
      const n = cartSize();
      if (!n) { toast('篮是空的 — 先勾选镜头', true); return; }
      const mins = Math.max(1, Math.round(n * 1.2));
      if (!confirm(`🎨 重做到生图 ${n} 镜:\n每镜 LLM重设计(30-60s) → 生图(~15s) → 停在待人检\n`
        + `全程约 ${mins} 分钟 · 进度看任务面板 · 镜间可 ⛔停止\n`
        + `不跑视频 — 图过目 ✅过审 后再决定生不生; 时间槽/口播/兄弟镜不动。继续?`)) return;
      const sids = cartDump();
      await startJob('/redo-queue', { sids, to_image: true });
    },
    // 1018 用户令: 修脸/压字独立流程 — 只对自己这一层负责, 不联动
    // 1018 修 (s48 实锤): 前置过滤已处理/未申报镜 — 否则秒完成零反馈, 用户以为没启动
    async cartFace() {
      const sids = [...sel];
      if (!sids.length) { toast('篮是空的 — 先勾选镜头', true); return; }
      const cand = sids.filter(sid => {
        const s = shot(sid);
        return s && s.image_file && !s.face_fixed;
      });
      if (!cand.length) { toast('勾选镜都已修过脸 — 无需再修 (要重修先 ♻️ 重绘换新图)', true); return; }
      if (!confirm(`👤 修脸 ${cand.length}/${sids.length} 镜:\n逐脸局部重绘 (guide 768 / denoise 0.5, 保构图身份)\n`
        + `~10-60s/镜 · 无脸镜自动跳过 · 镜间可 ⛔停止\n`
        + `注意: 建议在压字/生视频之前修 (先修脸后文字, 顺序自排)。继续?`)) return;
      cartDump();
      await startJob('/face-fix', { sids: cand });
    },
    async cartText() {
      const sids = [...sel];
      if (!sids.length) { toast('篮是空的 — 先勾选镜头', true); return; }
      const elig = sids.filter(sid => {
        const s = shot(sid);
        return s && s.image_file && (s.in_image_text || []).length && !s.text_passed;
      });
      if (!elig.length) { toast('勾选镜无需压字 — 未申报图内字, 或已压过 (已压的看图验收即可)', true); return; }
      if (!confirm(`🔤 压字 ${elig.length}/${sids.length} 镜:\n申报了图内字的镜把字写进画面 (~15s/镜)\n`
        + `未申报的镜自动跳过 · 镜间可 ⛔停止\n`
        + `注意: 建议在生视频之前压 (图内字烧进首帧, 后补无效)。继续?`)) return;
      cartDump();
      await startJob('/text-pass', { sids: elig });
    },
  };
}
