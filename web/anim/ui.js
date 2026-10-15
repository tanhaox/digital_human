// ── UI 基础设施: toast / 原生 <dialog> 封装 / Disposer 资源回收 ──
import { $ } from './util.js';

/**
 * Disposer: 定时器/EventSource/DOM 监听等一次性资源的统一回收站。
 * 为什么: 原页面的 pollTimer / es / hide 定时器散在 3 个全局变量里各自为政,
 * 换任务或重渲染时极易漏清 — "任务面板被上一个任务的定时器误关"就是这类事故。
 */
export function createDisposer() {
  const fns = new Set();
  return {
    /** @param {() => void} fn 幂等回收动作 */
    add(fn) { fns.add(fn); return () => fns.delete(fn); },
    /** 注册 addEventListener 并纳入回收 */
    on(target, type, fn, opts) {
      target.addEventListener(type, fn, opts);
      fns.add(() => target.removeEventListener(type, fn, opts));
    },
    /** 注册 setInterval 并纳入回收 */
    every(fn, ms) { const id = setInterval(fn, ms); fns.add(() => clearInterval(id)); },
    /** 注册 setTimeout 并纳入回收 */
    delay(fn, ms) { const id = setTimeout(fn, ms); fns.add(() => clearTimeout(id)); },
    dispose() {
      for (const f of fns) { try { f(); } catch { /* 回收动作不允许抛 */ } }
      fns.clear();
    },
  };
}

let toastTimer = null;

/** 全局轻提示 (role=status 屏幕阅读器可感知; 原版定时器未 clear, 连续提示互相抢 class) */
export function toast(msg, isErr = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.style.borderColor = isErr ? '#e87878' : 'var(--gold)';
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), isErr ? 6000 : 3500);
}

let lastFocus = null;    // 弹窗关闭后焦点归还触发按钮 (键盘/读屏用户不迷失)
let triggerEl = null;    // 委托层禁用按钮会抢走焦点, 须在禁用前登记触发者

/** 委托层在 disable 按钮前调用 — 保住"关闭后焦点归还"的真正目标 */
export function setDialogTrigger(el) { triggerEl = el; }

/** 弹窗初始化: 原生 <dialog> 自带焦点圈定 / ESC 关闭 / 顶层遮挡, 替代手搓 .ovl */
export function initDialog() {
  const d = $('#dlg');
  // 点击 ::backdrop (事件 target === dialog 本身) 关闭; 点内容不受影响
  d.addEventListener('click', e => { if (e.target === d) closeDialog(); });
  // Enter 提交: 输入框回车 → 点主按钮 (button.primary); textarea 回车=换行不拦
  d.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || !e.target.matches('input')) return;
    const btn = d.querySelector('button.primary');
    if (btn) { e.preventDefault(); btn.click(); }
  });
  // 原生 ESC/cancel 关闭也走统一清理: 清内容 + 焦点归还 (只挂 closeDialog 会漏这条路)
  d.addEventListener('close', cleanupDialog);
}

/**
 * 打开弹窗。
 * @param {string} html 弹窗内容 (调用方负责 esc 转义动态文本)
 * @returns {HTMLElement} 弹窗根节点
 */
export function openDialog(html) {
  const d = $('#dlg');
  lastFocus = triggerEl || document.activeElement;
  triggerEl = null;
  d.innerHTML = '<div class="box">' + html + '</div>';
  if (!d.open) d.showModal();
  const f = d.querySelector('input:not([type=hidden]), textarea, select');
  if (f) f.focus();
  return d;
}

function cleanupDialog() {
  const d = $('#dlg');
  d.innerHTML = '';
  lastFocus?.focus?.();
  lastFocus = null;
}

export function closeDialog() {
  const d = $('#dlg');
  if (d.open) d.close();        // 触发 'close' 事件 → cleanupDialog
  else cleanupDialog();
}
