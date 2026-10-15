// ── 镜槽卡片: 纯 HTML 生成, 不绑事件 (事件由 actions.js 统一委托) ──
import { esc, tc, STNAME } from './util.js';
import { fileUrl } from './api.js';
import { media } from './context.js';

/** 徽章片段 */
const bd = (cls, txt, title) =>
  `<span class="badge ${cls}"${title ? ` title="${esc(title)}"` : ''}>${txt}</span>`;

/** 1008 P8: 法层标签行 (灰) — ELS·鸟瞰·窗光 + 道具 + 术层 action */
const ANGLE_ZH = { birds_eye: '鸟瞰', high_angle: '高角', slight_high: '微俯',
  eye_level: '平视', slight_low: '微仰', low_angle: '仰视', dutch: '斜角' };

/** 1009 寄存器中文短名 (拆书导演模型: 主体类型一眼可见) */
const REG_ZH = { metaphor: '概念镜', pain_point: '痛点镜', case_narrative: '案例镜',
  author_cite: '引用镜', host_brand: '品牌镜', contrast: '对比镜', recap: '展馆镜' };

function faTagsHtml(s) {
  const fa = s.fa;
  if (!fa) return '';
  // 1008 电影感: location(场空间)·micro(微位) 入标签 — 空间连续一眼可见
  const loc = [fa.location, fa.micro].filter(Boolean).join('·');
  // 1009 寄存器: 主体类型打头 (概念镜/引用镜/…), 倒挂一眼可查
  const reg = fa.register
    ? REG_ZH[fa.register] || fa.register + (fa.reg_ref ? `·${fa.reg_ref}` : '') : '';
  const segs = [reg, fa.size, ANGLE_ZH[fa.angle] || fa.angle, loc,
    fa.mood || fa.light].filter(Boolean);
  const props = (fa.props || []).map(p =>
    `<span class="fa-prop" title="法层道具">${esc(p.split('(')[0])}</span>`).join('');
  const action = s.creative_slots?.action
    ? `<span class="fa-action" title="术层创意槽 action">🎨 ${esc(s.creative_slots.action)}</span>` : '';
  if (!segs.length && !props && !action) return '';
  return `<div class="fa-tags">${segs.map(x => `<span>${esc(x)}</span>`).join('<i>·</i>')}`
    + `${props ? ' <span class="fa-props">🪝 ' + props + '</span>' : ''}`
    + `${action ? ' ' + action : ''}</div>`;
}

/**
 * 动作按钮。为什么 data-act + data-sid 而不是拼 onclick 字符串:
 * ① id 拼进属性里的 JS 字符串, 引号即逃逸 (注入面)
 * ② 内联 handler 要求函数挂在全局 ③ CSP 不兼容。
 * dataset 读取的值经过 HTML 实体解码, 天然安全。
 * on: 可选参数位 (brandTag 标/剥) — 0921 修: 此前 act() 不输出 data-on,
 * handler 恒读到 undefined→当 false, "标品牌"永远执行剥标 (真凶级 bug)。
 */
const act = (name, sid, cls, label, title, on) =>
  `<button class="sm ${cls}" data-act="${name}" data-sid="${esc(sid)}"` +
  `${on !== undefined ? ` data-on="${on}"` : ''}` +
  `${title ? ` title="${esc(title)}"` : ''}>${label}</button>`;

function mediaHtml(s) {
  const u = rel => fileUrl(rel, media.ver);
  if (s.video_file) {
    // preload=none: 46 镜首屏并行拉视频头是数 MB 无效流量;
    // poster 复用首帧图 (大概率已在缓存), 点击再按需拉流, 体感更快
    return `<video src="${u(s.video_file)}" controls muted preload="none"`
      + (s.image_file ? ` poster="${u(s.image_file)}"` : '') + '></video>'
      + (s.image_file
        ? `<a class="thumblink" href="${u(s.image_file)}" target="_blank" rel="noopener">原图 ↗</a>`
        : '');
  }
  if (s.image_file) {
    return `<img src="${u(s.image_file)}" onclick="lbOpen(this.src, '镜 ' + this.alt)" style="cursor:zoom-in" loading="lazy" alt="镜 ${esc(s.shot_id)} 生成图">`;
  }
  return '<div class="noimg">🖼 图片槽 · K2 生成后填充</div>';
}

function badgesHtml(s) {
  return [
    // 1010 R 标 (用户令): 列队重做成功的镜 — 红底白字 R, 点播放看过/过审/打回即消
    s.redo_review ? bd('bd-r', 'R', '重做待复查: 此镜刚被列队重做 — 点播放看过后消除 (✅过审/打回也消)') : '',
    // 1013 P0: 单写者降级可见性 — writer 两败退现行链 (器层模板产物, 人检)
    s.writer_source === 'fallback' ? bd('bd-r', 'FB', 'fallback: 单写者两把均败, 本场退现行器层链 (模板句质量, 可🔁重掷)') : '',
    s.camera ? bd('bd-cam', esc(s.camera), '运镜') : '',
    bd('', esc(s.page_type || '')),
    `<span class="badge st-${esc(s.status)}">${STNAME[s.status] || esc(s.status)}</span>`,
    s.zone ? bd('bd-zone', '🔥特区' + (s.scene_tag ? '·' + esc(s.scene_tag) : ''),
      '60s特区镜: 槽位预装 3-6s · 场景不重样 · 复杂度下限') : '',
    s.brand_card ? bd('bd-brand', '🏔品牌卡', '品牌卡镜: 零GPU直通, 草稿插定稿卡') : '',
    s.overrun_s ? bd('bd-over', s.overrun_s + 's 定格', '音频时长超 H3 上限, 差额草稿定格吸收') : '',
    // 0927 P1-B 视频机检 (观察模式): 乱码/崩坏/首帧漂移 → 建议重roll
    s.video_suspect ? bd('bd-suspect', '🎥机检' + (s.video_suspect.kinds || []).join('/'),
      '视频机检标记: ' + esc((s.video_suspect.what || '') + ' — 人眼确认后可重roll')) : '',
  ].join('');
}

function actsHtml(s) {
  const sid = s.shot_id, a = [];
  if (s.status === 'img_done') {
    a.push(act('approve', sid, 'go', '✅ 过审'));
    a.push(act('redo', sid, 'warn', '🔁 打回'));
  }
  // 0920 用户令: 过审后卡上要有生视频按钮 — 此前 approved 卡零动作, 人检后
  // 无单镜出路 (只有场头/顶部批量), 用户实锤"卡死循环"
  if (s.status === 'approved') {
    a.push(act('shotH3', sid, 'violet', '🎬 生视频',
      '生成这一镜的动画 (H3 图生视频 ~100s); 批量走顶部 🎬H3 或场头 🎬生视频本场'));
  }
  if (s.status === 'anim_done' && !s.brand_card) {
    a.push(act('aiFix', sid, '', '🩹 AI修动画',
      'AI动画医生: 图和背景动画不动, LLM按文字纪律重写动画提示词 → 自动换seed重roll'));
    a.push(act('reroll', sid, 'violet', '🎲 重roll', '换seed重新生成该镜视频'));
  }
  // 0921 品牌镜单镜直通: 标品牌后卡上曾零生成出路 (只能回顶栏三步批) —
  // 品牌卡本就零 GPU, 单镜直通秒级完成, 没理由走任务面板。
  // 0921b 换片态: 已完成镜手标品牌 = 哑标 (装配端生成片优先) → 也给按钮,
  // 一键把生成片换成定稿卡; video 已是 _资产 定稿卡 (含路径标记) 时才隐藏
  const brandAssetOn = (s.video_file || '').includes('_资产');
  if (s.brand_card && !brandAssetOn) {
    a.push(act('brandPass', sid, 'go', '🏔 直通定稿卡',
      s.status === 'anim_done'
        ? '生成片 → 定稿品牌卡 (零GPU秒级换片; 原 mp4 留盘, shots.json 有备份)'
        : '一键插定稿品牌卡 (零GPU秒级): 首帧+视频直接用 _资产 定稿卡 → 动画成'));
  }
  // 0916 品牌卡镜重规划放开 (用户令: 有错的时候要能重规划) — 品牌镜走此路会先剥品牌标
  a.push(act('shotReplan', sid, '', '♻️ 重规划此镜',
    '只重新设计这一镜 (LLM): 时间槽/口播/兄弟镜不动, 图重生成'
    + (s.brand_card ? '; 品牌卡镜: 先剥品牌标再重设计' : '')));
  a.push(s.brand_card
    ? act('brandTag', sid, 'warn', '🗑 剥品牌标', '剥掉品牌卡标 → 回普通生成队列 (误标纠偏)', 'false')
    : act('brandTag', sid, '', '🏔 标品牌', '标为品牌卡镜 → 零 GPU 直通, 草稿插定稿卡 (漏标补上)', 'true'));
  const tlN = (s.text_layer || []).length;
  a.push(act('textLayer', sid, '', tlN ? '✍️ 花字 ' + tlN : '✍️ 花字', '加/改这一镜的花字 (剪映字幕层)'));
  a.push(act('animEdit', sid, '', '🎞 动画', '图不动, 只改动画文字结构 (一镜一事) → 重roll生效'));
  return a.join('');
}

/**
 * 单张镜卡片 HTML。
 * @param {import('./util.js').Shot} s
 * @param {boolean} busy 有任务运行中 (隐藏全部动作按钮)
 * @returns {string}
 */
export function cardHtml(s, busy) {
  const a = s.attempts || {};
  const lines = [
    s.motion_zh ? `<div class="mo-line">🏃 ${esc(s.motion_zh)}</div>` : '',
    s.reject_note ? `<div class="creject">打回: ${esc(s.reject_note)}</div>` : '',
    s.error ? `<div class="creject" title="${esc(s.error)}">⚠ ${esc(String(s.error).slice(0, 90))}</div>` : '',
  ].join('');
  const acts = busy ? '' : actsHtml(s);
  // 0921 重做购物篮: 卡头勾选 (busy 隐藏, 与动作按钮同闸)。勾选态不在此渲染 —
  // cart.js 持有 Set 并在板重渲染后回画 (cardHtml 保持纯函数, 不引购物篮依赖)
  const ck = busy ? '' : `<label class="cart-ck" title="勾选加入重做篮 (多选 → 底部♻️列队重做)">`
    + `<input type="checkbox" data-sid="${esc(s.shot_id)}"></label>`;
  // id=shot-<sid>: 统计 chip 定位 / 深链锚点 (生产 ID 即锚)
  return `<div class="card" id="shot-${esc(s.shot_id)}" title="生产ID ${esc(s.shot_id)} (${esc(s.group_id || '-')} · ${esc(s.arc_id || '-')})">`
    + `<div class="chead">${ck}<span class="cid">${esc(s.shot_id)}</span>${badgesHtml(s)}</div>`
    + faTagsHtml(s)
    + mediaHtml(s)
    + `<div class="narr">${esc(s.narration || '')}</div>`
    + lines
    + `<div class="cmeta">${tc(s.t_start)}–${tc(s.t_end)} · ${(a.k2 ?? 0)}/${(a.h3 ?? 0)} 试</div>`
    + (acts ? `<div class="acts">${acts}</div>` : '')
    + '</div>';
}
