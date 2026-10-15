// ── 极简 Store (发布订阅): 单一状态树 + 浅合并 + 订阅者错误隔离 ──
// 为什么不用全局变量: 原 ST 是裸全局, 读写散在各处, 谁改了状态、改完哪些视图
// 要刷新全靠人脑记; Store 把"改状态"和"刷视图"变成单向数据流。

/**
 * @template T
 * @param {T} initialState
 */
export function createStore(initialState = {}) {
  let state = initialState;
  const subs = new Set();

  /** 错误隔离: 一个视图挂了不能拖死其他视图与后续更新 (含订阅时的首次执行) */
  function safeRun(fn) {
    try { fn(state); }
    catch (e) { console.error('[store] subscriber failed:', e); }
  }

  return {
    /** @returns {T} */
    get: () => state,
    /** 浅合并; 传函数可基于旧态计算补丁 */
    set(patch) {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
      for (const fn of subs) safeRun(fn);
    },
    /** 立即执行一次 (视图初始化), 返回退订函数 */
    subscribe(fn) { subs.add(fn); safeRun(fn); return () => subs.delete(fn); },
  };
}
