// api.fileUrl / store 必测集
import test from 'node:test';
import assert from 'node:assert/strict';
import { fileUrl } from '../../anim/api.js';
import { createStore } from '../../anim/store.js';

const B = '/api/anim/ep//1';   // node 环境 BOOK='' 的 BASE 形态

test('fileUrl: 空/常规/编码', () => {
  assert.equal(fileUrl('', 1), '');
  assert.equal(fileUrl('img/p1.png', 5), `${B}/file/img/p1.png?v=5`);
  assert.equal(fileUrl('img/p 1.png', 5), `${B}/file/img/p%201.png?v=5`);
  assert.equal(fileUrl('立意.md', 9), `${B}/file/%E7%AB%8B%E6%84%8F.md?v=9`);
});

test('fileUrl: 无版本不拼参 (media.ver 初始为空)', () => {
  assert.equal(fileUrl('img/a.png', ''), `${B}/file/img/a.png`);
});

test('fileUrl: 越级路径走查询参数 (合法用例)', () => {
  const u = fileUrl('../../_资产/x.png', 3);
  assert.ok(u.startsWith(`${B}/file?path=`));
  assert.ok(u.includes('v=3'));
  assert.ok(!u.includes('../'));   // 已整体 encode, 不裸奔
});

test('fileUrl: 拒绝协议 URL 与绝对路径 (前端硬拒)', () => {
  assert.equal(fileUrl('https://evil.com/x', 1), '');
  assert.equal(fileUrl('http://evil', 1), '');
  assert.equal(fileUrl('C:/win/path', 1), '');
  assert.equal(fileUrl('/etc/passwd', 1), '');
});

test('store: 浅合并 + 订阅即执行 + 退订', () => {
  const seen = [];
  const s = createStore({ a: 1, b: 1 });
  const off = s.subscribe(st => seen.push({ ...st }));
  s.set({ b: 2 });
  s.set(st => ({ a: st.a + 10 }));          // 函数补丁基于旧态
  off();
  s.set({ a: 999 });
  assert.deepEqual(seen, [{ a: 1, b: 1 }, { a: 1, b: 2 }, { a: 11, b: 2 }]);
});

test('store: 订阅者异常隔离 (一个挂了不拖死其他)', () => {
  const s = createStore({ n: 0 });
  const errs = [];
  const orig = console.error;
  console.error = (...a) => errs.push(a);
  try {
    s.subscribe(() => { throw new Error('boom'); });
    const got = [];
    s.subscribe(st => got.push(st.n));
    s.set({ n: 1 });
    assert.deepEqual(got, [0, 1]);           // 订阅即执行收到初始 0 + set 后的 1
    assert.equal(errs.length, 2);            // 异常订阅者两次执行都被捕获上报 (订阅时 + set 时)
  } finally { console.error = orig; }
});
