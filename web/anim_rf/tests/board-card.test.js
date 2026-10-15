// board / card 渲染必测集: 三种形态 + 特区边界 + 卡片动作矩阵 (纯字符串, 无 DOM)
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderBoard } from '../../anim/board.js';
import { cardHtml } from '../../anim/card.js';

const shot = (o = {}) => ({
  shot_id: 'p1', status: 'planned', arc_id: 'a1', group_id: 'S1',
  t_start: 0, t_end: 5, narration: '口播', ...o,
});

test('renderBoard: 主形态 场→组→镜', () => {
  const st = {
    planned: true, opening_sec: 5.5,
    arcs: [{ arc_id: 'a1', narrative: 'N', motion: 'M', t_start: 5.5, t_end: 30 }],
    groups: [{ group_id: 'S1', arc_id: 'a1', t_start: 5.5, t_end: 30, camera: 'wide', narration: 'G' }],
    shots: [shot({ t_start: 5.5, t_end: 10 }), shot({ shot_id: 'p2', t_start: 10, t_end: 20 })],
  };
  const html = renderBoard(st);
  assert.ok(html.includes('id="arc-a1"'));
  assert.ok(html.includes('group-row'));
  assert.ok(html.includes('scene-opening'));
  assert.ok(html.includes('zone-banner'));   // 有镜落在特区窗口内
  assert.ok(!html.includes('scene-stats legacy'));
});

test('renderBoard: 无组形态 场→镜', () => {
  const st = {
    planned: true,
    arcs: [{ arc_id: 'a1', narrative: 'N' }],
    groups: [],
    shots: [shot({ group_id: '' })],
  };
  const html = renderBoard(st);
  assert.ok(html.includes('scene"'));
  assert.ok(!html.includes('group-row'));
});

test('renderBoard: v1 降级 label 分组', () => {
  const st = {
    planned: true, arcs: [],
    shots: [shot({ label: 'A' }), shot({ label: 'A' }), shot({ label: 'B', shot_id: 'p2' })],
  };
  const html = renderBoard(st);
  assert.ok(html.includes('legacy'));
  assert.ok(html.includes('A · <button'));           // 0920: 统计段改为可点 chip
  assert.ok(html.includes('data-st="planned"'));
  assert.ok(html.includes('>待生图 2</button>'));
  assert.ok(html.includes('B · <button'));
  assert.ok(html.includes('>待生图 1</button>'));
});

test('renderBoard: 特区窗口边界 (opening 5.5 → 窗 [5.5, 65.5))', () => {
  const mk = (t0, t1, sid) => shot({ t_start: t0, t_end: t1, shot_id: sid });
  const base = {
    planned: true, opening_sec: 5.5, arcs: [{ arc_id: 'a1' }], groups: [], shots: [],
  };
  // 窗内: 与 [5.5,65.5] 有交集; 窗外: t_start >= 65.49 或 t_end <= 5.49
  const inHtml = renderBoard({ ...base, shots: [mk(0, 6, 'a'), mk(64, 70, 'b')] });
  assert.ok(inHtml.includes('2 镜'));
  const outHtml = renderBoard({ ...base, shots: [mk(65.6, 70, 'x')] });
  assert.ok(!outHtml.includes('zone-banner'));
});

test('renderBoard: 特区单镜上限徽章 (6.05s 阈值)', () => {
  const base = { planned: true, opening_sec: 0, arcs: [{ arc_id: 'a1' }], groups: [] };
  const over = renderBoard({ ...base, shots: [shot({ t_start: 0, t_end: 6.1 })] });
  assert.ok(over.includes('bd-amber'));
  const ok = renderBoard({ ...base, shots: [shot({ t_start: 0, t_end: 6.0 })] });
  assert.ok(ok.includes('bd-green'));
});

test('renderBoard: 时间码进位修复可见 (119.6s → 02:00 而非 01:60)', () => {
  const st = {
    planned: true, opening_sec: 0, arcs: [{ arc_id: 'a1' }],
    groups: [{ group_id: 'S1', arc_id: 'a1', t_start: 119.6, t_end: 130 }],
    shots: [shot({ t_start: 119.6, t_end: 130 })],
  };
  const html = renderBoard(st);
  assert.ok(html.includes('02:00'));
  assert.ok(!html.includes('01:60'));
});

test('cardHtml: busy 隐藏动作; 状态决定动作集', () => {
  const imgDone = cardHtml(shot({ status: 'img_done' }), false);
  assert.ok(imgDone.includes('data-act="approve"'));
  assert.ok(imgDone.includes('data-act="redo"'));
  assert.ok(cardHtml(shot({ status: 'img_done' }), true).indexOf('data-act=') === -1);

  const animDone = cardHtml(shot({ status: 'anim_done' }), false);
  assert.ok(animDone.includes('data-act="aiFix"'));
  assert.ok(animDone.includes('data-act="reroll"'));
  // 品牌卡镜: 不给 AI修/重roll (零 GPU 直通)
  const brand = cardHtml(shot({ status: 'anim_done', brand_card: true }), false);
  assert.ok(!brand.includes('data-act="aiFix"'));
  assert.ok(brand.includes('剥品牌标'));

  const planned = cardHtml(shot({ status: 'planned' }), false);
  assert.ok(!planned.includes('data-act="approve"'));
  assert.ok(planned.includes('data-act="shotReplan"'));
});

test('cardHtml: 视频 preload=none + 首帧图 poster; 注入面封死', () => {
  const s = shot({ status: 'anim_done', video_file: 'vid/p1.mp4', image_file: 'img/p1.png' });
  const html = cardHtml(s, false);
  assert.ok(html.includes('preload="none"'));
  assert.ok(html.includes('poster='));
  // 带引号的 shot_id 只会出现在属性文本里, 不会拼进 JS 字符串 (无 onclick)
  assert.ok(!html.includes('onclick'));
  const evil = cardHtml(shot({ shot_id: `x'onclick=alert(1)` }));
  assert.ok(evil.includes('data-sid="x&#39;onclick=alert(1)"'));   // 引号被实体化
  assert.ok(!/\sonclick=/.test(evil));                            // 标签内无裸 onclick 属性
});
