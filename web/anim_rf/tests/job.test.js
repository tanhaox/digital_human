// JobRunner 必测集: 轮询熔断 + 面板隐藏竞态 + 幂等 attach (DOM/时间全部注入)
import test from 'node:test';
import assert from 'node:assert/strict';
import { createJobRunner } from '../../anim/job.js';

const sleep = ms => new Promise(r => setTimeout(r, ms));

/** DOM 桩: 只实现 runner 触碰到的面 */
function stubDom() {
  return {
    panel: { hidden: true },
    phase: { textContent: '' },
    bar: { style: { width: '' } },
    prog: { textContent: '' },
    log: { textContent: '', scrollTop: 0, scrollHeight: 0 },
  };
}
const stubSse = () => ({ close() {} });
const JOB = { id: 'j1', phase: 'k2', book_id: 'book-uuid', ep: 1 };
const mk = (dom, fetchJob, onFinished, extra = {}) => createJobRunner({
  fetchJob, cancelJob: async () => ({}), onFinished,
  notify: () => {}, dom, pollMs: 1, hideMs: 15, sseFactory: stubSse,
  ...extra,
});

test('轮询熔断: 连续失败 POLL_ERR_MAX 次后结算且不再轮询', async () => {
  const dom = stubDom();
  let calls = 0;
  let finished = 0;
  const r = mk(dom, async () => { calls++; throw new Error('boom'); }, () => finished++);
  r.attach(JOB);
  await sleep(500);                  // 合跑并发时事件循环更忙, 余量给足 (10 次轮询 + 熔断)
  assert.equal(finished, 1);          // 熔断只结算一次
  assert.equal(r.activeId(), null);
  const callsAtBreak = calls;
  await sleep(200);
  assert.equal(calls, callsAtBreak);  // 熔断后不再打接口
  assert.equal(dom.panel.hidden, true);    // 驻留期 (15ms) 已过, 正常隐藏
  r.detach();
});

test('隐藏竞态: 旧任务 finish 后立刻挂新任务, 面板不被旧定时器误关', async () => {
  const dom = stubDom();
  let status = 'running';
  const r = mk(dom, async () => ({ status, progress: null }), () => {});
  r.attach(JOB);
  await sleep(5);
  status = 'done';
  await sleep(5);                     // j1 finish → 15ms 后隐藏 (定时器已挂)
  status = 'running';                 // 新任务持续运行 (要测的是旧定时器, 不是新任务结算)
  r.attach({ ...JOB, id: 'j2', phase: 'h3' });   // 立刻挂新任务: 必须撤掉旧隐藏定时器
  await sleep(60);                    // 越过旧任务的隐藏窗口
  assert.equal(dom.panel.hidden, false, '新任务面板被旧定时器误关');
  assert.equal(r.activeId(), 'j2');
  r.detach();
});

test('正常完结: onFinished 触发 + 驻留后面板隐藏', async () => {
  const dom = stubDom();
  let finished = 0;
  const r = mk(dom, async () => ({ status: 'running', progress: { done: 3, total: 10, current: 'p4' } }), () => finished++);
  r.attach(JOB);
  await sleep(5);
  assert.equal(dom.bar.style.width, '30%');
  assert.ok(dom.prog.textContent.includes('3/10'));
  r.__forceDone = true;
  // 换状态为 done 触发 finish
  await sleep(0);
  const r2 = mk(dom, async () => ({ status: 'done', progress: null }), () => finished++);
  r2.attach({ ...JOB, id: 'j9' });
  await sleep(5);
  assert.equal(finished, 1);
  await sleep(30);                    // 越过 hideMs=15
  assert.equal(dom.panel.hidden, true);
  r.detach(); r2.detach();
});

test('幂等 attach: 同一任务重复挂载不重置通道', async () => {
  const dom = stubDom();
  let sseCount = 0;
  const r = mk(dom, async () => ({ status: 'running' }), () => {}, {
    sseFactory: () => { sseCount++; return stubSse(); },
  });
  r.attach(JOB);
  r.attach(JOB);
  r.attach(JOB);
  assert.equal(sseCount, 1);          // 只开了一次 SSE
  r.detach();
});
