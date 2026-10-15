// ── 弹窗构建器: 打回 / 文字层 / 动画拍编辑 / AI修动画 (构建 + 收集, 提交走 actions) ──
import { esc } from './util.js';
import { openDialog } from './ui.js';

// 主提交按钮统一挂 .primary — ui.js 的 Enter 提交委托靠它定位 (L-3)

/** 打回弹窗: 理由必填留痕; 负面词直接剔除; 画面描述留空=纯换seed重抽卡, 改了=定向重生成 */
export function redoDialog(sid, shot) {
  openDialog(`
    <h2>🔁 打回 ${esc(sid)}</h2>
    <div class="row"><input type="text" id="rd-note" placeholder="打回理由 (必填, 留痕)"></div>
    <div class="hint dlg-hint">🚫 负面词 (直接剔除): 把画面描述里含这些词的<b>子句整体删掉</b>再重抽 — 例: 不要墙就填「墙」; 多个用逗号分隔。比写"不要X"可靠 (模型对否定不敏感, 词根零残留才是真删)。</div>
    <div class="row"><input type="text" id="rd-neg" placeholder="负面词, 如: 墙, 文字, 多人 (可空; 新描述优先于此)"></div>
    <div class="hint dlg-hint">画面描述: <b>留空不改 = 纯换seed重抽卡</b>; <b>改了 = 定向重生成</b> (如"构图改竖三分/人物移左")</div>
    <textarea id="rd-prompt" placeholder="该镜画面描述 (K2 提示词)">${esc(shot.image_prompt_zh || '')}</textarea>
    <div class="row dlg-foot">
      <button class="warn primary" data-act="redoSubmit" data-sid="${esc(sid)}">打回 (换seed)</button>
      <button data-act="dlgClose">取消</button>
    </div>`);
}

/**
 * 文字层一行。
 * @param {object} e 已有条目 @param {'cap'|'fx'} col cap=字幕列(kind 固定 caption) | fx=花字列(保留原 kind, 新条默认 hero_number)
 */
export function tlRow(e = {}, col) {
  const kind = col === 'cap' ? 'caption' : ((e.kind && e.kind !== 'caption') ? e.kind : 'hero_number');
  const ph = col === 'cap' ? '字幕内容 (口播原句)' : '大字/花字 (2-4字最佳)';
  return `<div class="row tl-row">
    <input type="hidden" data-k="kind" value="${esc(kind)}">
    <input type="text" data-k="text" placeholder="${ph}" value="${esc(e.text || '')}">
    <input type="number" data-k="t0" step="0.1" min="0" title="镜内起(秒)" value="${e.t_start ?? ''}" placeholder="起s">
    <input type="number" data-k="t1" step="0.1" min="0" title="镜内止(秒)" value="${e.t_end ?? ''}" placeholder="止s">
    <button class="sm warn" data-act="tlRowDel" aria-label="删除此条">✕</button></div>`;
}

/** 文字层弹窗: 字幕/花字两列 (剪映两层, 各管各的) */
export function textLayerDialog(sid, shot) {
  const all = shot.text_layer || [];
  const caps = all.filter(t => (t.kind || 'caption') === 'caption');
  const fxs = all.filter(t => (t.kind || 'caption') !== 'caption');
  const dur = Math.max((shot.t_end || 5) - (shot.t_start || 0), 1).toFixed(1);
  openDialog(`
    <h2>✍️ 文字层 · ${esc(sid)} <span class="hint">镜长约 ${dur}s · 镜内相对秒</span></h2>
    <div class="hint dlg-hint">两列各管各的: 字幕=底部白字(口播对齐); 花字=金色大字(画面主角, R9 协同挂音效点)</div>
    <div class="tl-wrap">
      <div class="tl-col">
        <div class="tl-title">🔤 字幕列 <span class="hint">(底部 · 白)</span></div>
        <div id="tl-cap">${caps.map(t => tlRow(t, 'cap')).join('')}</div>
        <button class="sm" data-act="tlAdd" data-col="cap">+ 字幕</button>
      </div>
      <div class="tl-col">
        <div class="tl-title">✨ 花字列 <span class="hint">(金色大字 · 居中)</span></div>
        <div id="tl-fx">${fxs.map(t => tlRow(t, 'fx')).join('')}</div>
        <button class="sm gold" data-act="tlAdd" data-col="fx">+ 花字</button>
      </div>
    </div>
    <div class="row dlg-foot"><span class="flex1"></span>
      <button class="gold primary" data-act="tlSave" data-sid="${esc(sid)}">保存</button>
      <button data-act="dlgClose">取消</button></div>`);
}

/**
 * 收集文字层两列条目 (空文本行丢弃)。
 * @param {Element} root 作用域根 — 调用方传 $('#dlg'), 防页面同名 class 串数据
 */
export function collectTextLayer(root) {
  const pick = sel => [...root.querySelectorAll(sel + ' .tl-row')].map(r => ({
    kind: r.querySelector('[data-k=kind]').value,
    text: r.querySelector('[data-k=text]').value,
    t_start: parseFloat(r.querySelector('[data-k=t0]').value) || 0,
    t_end: parseFloat(r.querySelector('[data-k=t1]').value) || 0,
  })).filter(e => (e.text || '').trim());
  return [...pick('#tl-cap'), ...pick('#tl-fx')];
}

/**
 * 动画拍编辑弹窗 (图不动, 只改 motion 文字结构 → 重roll 生效)。
 * @returns {boolean} false = 该镜无节拍 (调用方提示)
 */
export function animEditDialog(sid, shot) {
  const beats = (shot.anim && shot.anim.beats) || [];
  if (!beats.length) return false;
  openDialog(`
    <h2>🎞 动画描述 · ${esc(sid)} <span class="hint">${beats.length} 拍</span></h2>
    <div class="hint dlg-hint">图不动, 只改动画文字结构; <b>一镜一事</b>: 全镜最多 1 个文字事件, 数字与中文不共现, 3 字以上中文走✍️花字后期 — 改完 🎲重roll 生效</div>
    ${beats.map((b, i) => `
      <div class="am-beat">
        <div class="hint">拍 ${i + 1} · ${b.t_start}-${b.t_end}s</div>
        <textarea class="am-row" data-i="${i}">${esc(b.motion || '')}</textarea>
      </div>`).join('')}
    <div class="row dlg-foot"><span class="flex1"></span>
      <button class="gold primary" data-act="animSave" data-sid="${esc(sid)}">保存</button>
      <button data-act="dlgClose">取消</button></div>`);
  return true;
}

/** 收集动画拍文本 (按 data-i 顺序) */
export function collectAnimMotions(root) {
  return [...root.querySelectorAll('.am-row')].map(r => r.value);
}

/** AI修动画弹窗 (替代 prompt(): 可访问、可取消、不阻塞) */
export function aiFixDialog(sid) {
  openDialog(`
    <h2>🩹 AI 动画医生 · ${esc(sid)}</h2>
    <div class="hint dlg-hint">哪里不对? (一句话, 可留空自动按纪律修) 图和背景动画不动, 只重写文字动画部分, 改完自动换seed + 重roll</div>
    <textarea id="af-note" placeholder="问题描述 (可空)"></textarea>
    <div class="row dlg-foot"><span class="flex1"></span>
      <button class="gold primary" data-act="aiFixSubmit" data-sid="${esc(sid)}">🩹 开修</button>
      <button data-act="dlgClose">取消</button></div>`);
}
