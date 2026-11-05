// util 纯函数必测集 (node --test, 无 DOM 依赖)
import test from 'node:test';
import assert from 'node:assert/strict';
import { esc, tc, statTxt, statChips, gidShow, avg1, stLabel, scenesReady } from '../../anim/util.js';

test('esc: 全危险字符 + 容错输入', () => {
  assert.equal(esc('&<>"\''), '&amp;&lt;&gt;&quot;&#39;');
  assert.equal(esc(null), '');
  assert.equal(esc(undefined), '');
  assert.equal(esc(0), '0');
  assert.equal(esc(7), '7');
  assert.equal(esc('<img onload=x>'), '&lt;img onload=x&gt;');
});

test('tc: 60s 进位回归 (原实现 119.6 → "01:60")', () => {
  assert.equal(tc(0), '00:00');
  assert.equal(tc(undefined), '00:00');
  assert.equal(tc(NaN), '00:00');
  assert.equal(tc(-5), '00:00');
  assert.equal(tc(59.4), '00:59');
  assert.equal(tc(59.9), '01:00');    // 进位
  assert.equal(tc(119.6), '02:00');   // H-1 回归用例
  assert.equal(tc(3600), '60:00');
});

test('stLabel/statTxt: 状态统计', () => {
  assert.equal(stLabel('planned'), '待生图');
  assert.equal(stLabel('unknown_key'), 'unknown_key');
  assert.equal(statTxt([]), '');
  assert.equal(statTxt([{ status: 'planned' }]), '待生图 1');
  assert.equal(
    statTxt([{ status: 'planned' }, { status: 'planned' }, { status: 'approved' }]),
    '待生图 2 · 已批 1');
});

test('statChips: 统计 chip 可点击定位版 (0920)', () => {
  assert.equal(statChips([]), '');
  const html = statChips([{ status: 'planned' }, { status: 'approved' }]);
  assert.ok(html.includes('data-act="statLocate" data-st="planned"'));
  assert.ok(html.includes('待生图 1</button>'));
  assert.ok(html.includes('data-st="approved"'));
  assert.ok(html.includes(' · '));                       // chip 间分隔保留
  // 状态值是内部枚举 — 非法值也不应产生可逃逸属性
  const weird = statChips([{ status: 'x"><script>' }]);
  assert.ok(!weird.includes('<script>'));
});

test('gidShow: S 前缀组号压缩', () => {
  assert.equal(gidShow('S3'), 's3');
  assert.equal(gidShow('s12'), 's12');
  assert.equal(gidShow('g_other'), 'g_other');
  assert.equal(gidShow(''), '?');
  assert.equal(gidShow(null), '?');
});

test('avg1: 保留 1 位小数', () => {
  assert.equal(avg1([3, 4]), 3.5);
  assert.equal(avg1([3.33, 3.33, 3.34]), 3.3);
});

test('scenesReady: 立意待续跑态判定', () => {
  assert.equal(scenesReady(null), false);
  assert.equal(scenesReady({ arcs: [], total: 0 }), false);
  // 1105 批1 夹具修: 原对象漏 shots 键 — "a1 已有镜"意图靠 total 暗示, 实现按
  // shots 判 → 此断言在 HEAD 上已红 (夹具缺陷, 非实现回归); 补 shots 对齐意图
  assert.equal(scenesReady({ arcs: [{ arc_id: 'a1' }], total: 5, shots: [{ arc_id: 'a1', shot_id: 's1' }] }), false);
  assert.equal(scenesReady({ arcs: [{ arc_id: 'a1' }], total: 0 }), true);
  assert.equal(scenesReady({ arcs: [{ arc_id: 'a1' }] }), true);   // total 缺省按 0
});

test('scenesReady: 后端 resume_todo 单一事实源 (1105 批1)', () => {
  // 带病场重开: 全场有镜 (老口径 false) 但后端判定有可跑 → true (闭环修复)
  assert.equal(scenesReady({ resume_todo: { ready: true, todo: ['a2'] }, arcs: [{ arc_id: 'a1' }], total: 9 }), true);
  // 全场有镜且过闸 → 后端 ready=false, 不再靠"缺镜"猜测
  assert.equal(scenesReady({ resume_todo: { ready: false, todo: [] }, arcs: [{ arc_id: 'a1' }], total: 0 }), false);
  // resume_todo 缺失 (旧后端) → 不吃它, 不炸 (回退老口径)
  assert.equal(scenesReady({ resume_todo: undefined, arcs: [{ arc_id: 'a1' }], total: 0 }), true);
});
