// ④ 导演页 (UI 定稿批: 真数据 + 空字段; 生成/保存下一批接入)
import { rail } from './rail.js';
import { createJobRunner } from './job.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

// 1105 用户令「运镜三元组 型·幅·速」× MiniMax H3 官方词表
// 官方: 动词写进句子 (The camera pans left with large amplitude at slow speed),
// 幅 small/large amplitude, 速 slow/fast speed — 中/常速 **省略不写**.
const SIZES = ['大场景', '全景', '中景', '近景', '特写'];
const SIZE_LV = { 大场景: 5, 全景: 4, 中景: 3, 近景: 2, 特写: 1 };

const CAM_DEFS = {
  '固定':   { dirs: [], static: true, en: () => 'holds a static shot' },
  '推':     { dirs: [], en: () => 'pushes in' },
  '拉':     { dirs: [], en: () => 'pulls out' },
  '变焦推': { dirs: [], en: () => 'zooms in' },
  '变焦拉': { dirs: [], en: () => 'zooms out' },
  '摇':     { dirs: ['左', '右', '上', '下'],
             en: d => (d === '上' ? 'tilts up' : d === '下' ? 'tilts down'
                       : d === '右' ? 'pans right' : 'pans left') },
  '移':     { dirs: ['左', '右'], en: d => (d === '右' ? 'trucks right' : 'trucks left') },
  '升降':   { dirs: ['上', '下'], en: d => (d === '上' ? 'pedestals up' : 'pedestals down') },
  '跟':     { dirs: ['左', '右'], en: d => (d === '右' ? 'tracks right' : 'tracks left') },
  '弧':     { dirs: [], en: () => 'arcs around the subject' },
};
const CAMS = ['待定', '固定', '推', '拉', '变焦推', '变焦拉', '摇', '移', '升降', '跟', '弧'];
const AMPS = ['小', '中', '大'];
const SPEEDS = ['慢', '中', '快'];
const AMP_LAB = { 小: '小', 中: '中(省略)', 大: '大' };
const SPD_LAB = { 慢: '慢', 中: '中(省略)', 快: '快' };
const NO_MOVE = ['待定', '固定'];
const TYPE_ZH = { narr: '叙事弧', quote: '书证镜', persona: '人设镜',
  hook: '钩子', cta: '行动召', brand: '品牌' };

let SHOTS = [], ARCS = [], STATE = {}, DIRTY = new Set(), GATES = {}, FROZEN = false,
    D_ISSUES = {};   // 1109: ④ 拦截项 (槽长超限等) — 此前只活在 toast 里, 页面零渲染
let MIN_SLOT = 6;

/* 任务面板 (1105 移植 v2 模式): busy 禁操作钮, 结束刷新, 刷新重挂 */
const runner = createJobRunner({
  onFinished: job => {
    if (job.status === 'completed')
      toast(`✓ ${job.result?.shots ?? '?'} 镜 (运镜 ${job.result?.moves ?? 0}, 待清 ${job.result?.issues ?? 0})`);
    else if (job.status !== 'unknown') toast('生成结束: ' + (job.status || ''));
    boot();
  },
  onBusy: busy => {
    ['btn-run', 'btn-save', 'btn-next'].forEach(id => { $(id).disabled = busy; });
    if (busy) $('btn-run').textContent = '⏳ 生成中…';
    else $('btn-run').textContent = '🎬 生成导演';
  },
});

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/shots`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; ARCS = d.arcs || []; GATES = d.gates || {}; FROZEN = !!st.frozen;
  D_ISSUES = d.direct_issues || {};
  // 词表以服务端为准 (单一事实源: 后端 CAM_DEFS = H3 壳 = 校验器 = 本页预览)
  if (d.cam_defs) {
    Object.keys(CAM_DEFS).forEach(k => delete CAM_DEFS[k]);
    Object.assign(CAM_DEFS, d.cam_defs);
    CAMS.length = 0; CAMS.push('待定', ...Object.keys(CAM_DEFS));
  }
  if (d.sizes) { SIZES.length = 0; SIZES.push(...d.sizes); }
  d.sizes.forEach(z => { SIZE_LV[z] = d.sizes.length - d.sizes.indexOf(z); });
  MIN_SLOT = d.min_move_slot_s || MIN_SLOT;
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  for (const s of SHOTS) {
    const saved = s.direct || {};
    STATE[s.sid] = {
      size: saved.size || s.size_guess || '中景',
      // 未生成 → 运镜「待定」而不是假装「固定」; 一旦选了运动, 幅速默认 大·慢
      cam: { kind: (saved.cam && saved.cam.kind) || '待定',
             dir: (saved.cam && saved.cam.dir) || '无',
             amp: (saved.cam && saved.cam.amp) || '大',
             speed: (saved.cam && saved.cam.speed) || '慢' },
      motion: saved.motion || '', light: saved.light || '',
      motion_en: saved.motion_en || '',
      filled: !!s.direct,
    };
  }
  rail($('rail'), { line: st.line, gates: GATES, active: 'direct' });
  if (st.running_job) runner.attach(st.running_job);   // 刷新重挂 (v2 同模式)
  renderBlocks(); renderRhythm(); renderCounts(); renderSide(); refreshDirty();
  paintAllClauses();                                   // 运镜句预览 (服务端单源)
  const run = $('btn-run'), save = $('btn-save');
  run.disabled = false; run.title = 'LLM 按官方词表填四元组 (程序管步数/工位)';
  save.disabled = false; save.title = '保存导演参数 + 复检放行';
  run.onclick = runDirect; save.onclick = saveDirect;
  const nx = $('btn-next');                    // 1108 用户令: ④ 下一步 = ⑤ TTS
  nx.disabled = !GATES.direct;                 // (1106 站序: TTS 便宜先定稿, K2 烧卡殿后)
  nx.title = GATES.direct ? '进入 ⑤ TTS (配音定稿后再烧卡)'
    : (SHOTS.every(s => !STATE[s.sid] || !STATE[s.sid].filled)
        ? '④ 参数空 (③ 重跑已作废) — 先 🎬 生成导演 → 保存 → 确认放行'
        : '先保存并放行 (触发英文化批)');
  nx.onclick = async () => {
    if (DIRTY.size) await saveDirect();
    try {
      const r = await fetch('./tts.html', { method: 'HEAD' });
      if (r.ok) { location.href = './tts.html' + location.search; return; }
    } catch { /* 未上线 */ }
    toast('⑤ TTS 页建设中 — 导演参数已保存');
  };
}

/* ── 生成 / 保存 ──────────────────────────────────────── */
async function runDirect() {
  const r = await fetch(`${BASE}/v3_direct`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
}

async function saveDirect() {
  const confirmMode = !DIRTY.size && GATES.direct;
  const nFilled = SHOTS.filter(s => STATE[s.sid] && STATE[s.sid].filled).length;
  // 1109 修 (⑤ 不亮实锤): ③ 重跑会作废清零 ④ 参数 — 空表保存只会得到
  // gate:false + 「已保存」误导 toast, 按钮永不亮。空表直接指路, 不发请求。
  if (!nFilled && !confirmMode) {
    toast(`0/${SHOTS.length} 镜已填 — 先点 🎬 生成导演 (③ 重跑后 ④ 参数已作废清零), 完成后再保存放行`);
    return;
  }
  $('save-label').textContent = '⏳ 保存中…';
  const direct = {};
  for (const s of SHOTS) {
    const st = STATE[s.sid];
    if (!st.filled) continue;
    direct[s.sid] = { size: st.size,
      cam: { kind: st.cam.kind === '待定' ? '固定' : st.cam.kind,
             dir: st.cam.dir || '', amp: NO_MOVE.includes(st.cam.kind) ? '' : st.cam.amp,
             speed: NO_MOVE.includes(st.cam.kind) ? '' : st.cam.speed },
      motion: st.motion, light: st.light };
  }
  try {
    const r = await (await fetch(`${BASE}/direct/save`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direct }) })).json();
    DIRTY.clear();
    if (r.gate && confirmMode) {
      // 放行动作 = 英文化批 (ID-131): keyframe_en/motion_en + 冻结戳
      toast('放行中: 英文化批 (34 镜翻译, 约 1 分钟)…');
      try {
        const f = await (await fetch(`${BASE}/direct/finalize`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
        toast(f.already ? '已放行过 (幂等)' : `✓ 放行完成: 英文化 ${f.translated}/${f.total} 镜 — 可进 ⑤`);
      } catch (e) { toast('英文化失败: ' + e.message); }
    } else if (r.gate) toast('✓ 达标 — 点「确认放行」触发英文化');
    else {
      const first = Object.entries(r.issues || {}).find(([k]) => k !== '弧级');
      if (first) toast(`待清: ${first[1][0]}`);
      else if ((r.saved || 0) < SHOTS.length)
        toast(`已存 ${r.saved}/${SHOTS.length} 镜 — 未全覆盖 (先 🎬 生成导演 / ↻ 补镜), 放行前须 34 镜全填`);
      else toast('已保存 (弧级提醒不拦闸)');
    }
    await boot();
  } catch (e) { toast('保存失败: ' + e.message); }
  refreshDirty();   // 1108: 保存完成后按钮应显性变「确认放行」(原硬编码还原, 放行形态从不露面 = K2 被卡用户找不到按钮)
}

/* ── 顶栏 ─────────────────────────────────────────────── */
function renderCounts() {
  const filled = SHOTS.filter(s => STATE[s.sid].filled).length;
  $('counts').innerHTML =
    `<span class="badge">${SHOTS.length} 镜</span>` +
    `<span class="badge ${filled ? 'ok' : ''}">已填 ${filled}</span>` +
    `<span class="badge">${Math.round(SHOTS.reduce((a, s) => a + (s.est_s || 0), 0))}s</span>`;
}

/* ── 槽位节奏轴 (1106 用户令: 柱高=预计时长, 15s 红线一眼判超) ── */
const CAP_S = 15, PX_PER_S = 6.4;    // 15s → 96px 基准

function renderRhythm() {
  const box = $('rhythm');
  box.innerHTML = '';
  const bars = document.createElement('div');
  bars.className = 'rhybars';
  SHOTS.forEach(s => {
    const st = STATE[s.sid];
    const est = Number(s.est_s || 0);
    const b = document.createElement('div');
    b.className = 'bar s' + SIZE_LV[st.size] + (est > CAP_S ? ' over' : '');
    b.style.height = Math.max(3, Math.round(est * PX_PER_S)) + 'px';
    b.title = `${s.sid} · ~${est}s · ${st.size}${est > CAP_S ? ' · ⚠ 超槽位上限' : ''}`;
    b.onclick = () => {
      const el = document.querySelector(`[data-sid="${s.sid}"]`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    bars.appendChild(b);
  });
  const line = document.createElement('div');
  line.className = 'capline';
  const lbl = document.createElement('span');
  lbl.className = 'caplbl';
  lbl.textContent = `${CAP_S}s H3 上限`;
  line.appendChild(lbl);
  bars.appendChild(line);
  box.appendChild(bars);
  const q = quotas();
  const over = SHOTS.filter(s => Number(s.est_s || 0) > CAP_S);
  $('rhycap').textContent =
    `柱高=预计时长 · 柱色=景别${over.length
      ? ` · ⚠ ${over.length} 槽超限 (${over.map(x => x.sid).join('/')})`
      : ' · 全部 ≤15s'} · 大场景+全景 ${q.big}/${q.needBig} 需 · 特写 ${q.micro}/${q.maxMicro} 上限 · 相邻同景别 ${q.sameAdj} 处`;
}

/* ── 主区 ─────────────────────────────────────────────── */
function arcOf(aid) { return ARCS.find(a => a.arc_id === aid) || {}; }

function renderBlocks() {
  const byArc = new Map();
  for (const s of SHOTS) {
    if (!byArc.has(s.arc_id)) byArc.set(s.arc_id, []);
    byArc.get(s.arc_id).push(s);
  }
  const out = [];
  for (const [aid, shots] of byArc) {
    const a = arcOf(aid);
    const type = a.type || shots[0].arc_type || 'narr';
    const world = String(a.world || '').trim();
    const wCell = ['narr', 'persona', 'quote'].includes(type)
      ? `<span class="world${world ? '' : ' na'}" title="${esc(world || '视觉世界为空')}">${world ? esc(world) : '⚠ 缺视觉世界'}</span>`
      : '<span class="world na">— 非叙事位</span>';
    out.push(`<section class="arc" data-type="${esc(type)}">
      <header class="ah">
        <span class="aid">${esc(aid)}</span>
        <span class="atype">${esc(TYPE_ZH[type] || type)}</span>
        ${a.no_start ? `<span class="lspan">L${a.no_start}–${a.no_end}</span>` : ''}
        ${wCell}
        <span class="est">${shots.length} 镜</span>
      </header>
      ${shots.map(rowHTML).join('')}
    </section>`);
  }
  $('blocks').innerHTML = out.join('');
  bindRows();
}

function opts(list, cur, labels) {
  return list.map(x => `<option value="${esc(x)}"${x === cur ? ' selected' : ''}>${esc((labels && labels[x]) || x)}</option>`).join('');
}

/* 运镜句预览 (1107 批1 收单源): 四要素装配只在服务端 direct.cam_clause
   (与 ⑥ 拼合/⑧ 注入同句), 前端旧平行实现已退役 — 每次改动调 API 拿真句。 */
async function fetchClauses(sids) {
  const items = sids.map(sid => ({
    sid, cam: STATE[sid].cam, motion_en: STATE[sid].motion_en || '' }));
  try {
    const r = await (await fetch('/api/anim/v3/cam_clause', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }) })).json();
    return r.clauses || {};
  } catch { return {}; }
}
function clauseText(sid, clause) {
  if (clause) return clause;
  return STATE[sid].cam.kind === '固定'
    ? '固定镜 — 无运镜句 (固定镜无镜头槽)' : '— 未选运镜 —';
}
function paintClauseInto(sid, clause) {
  const el = document.querySelector(`.row[data-sid="${CSS.escape(sid)}"] .cam-en`);
  if (el) el.textContent = clauseText(sid, clause);
}
async function paintClause(sid) {
  const c = await fetchClauses([sid]);
  paintClauseInto(sid, c[sid]);
}
async function paintAllClauses() {
  const sids = Object.keys(STATE);
  const c = await fetchClauses(sids);
  sids.forEach(sid => paintClauseInto(sid, c[sid]));
}

function rowHTML(s) {
  const st = STATE[s.sid];
  const fixed = NO_MOVE.includes(st.cam.kind);
  const dirs = (CAM_DEFS[st.cam.kind] || { dirs: [] }).dirs;
  const noDir = dirs.length === 0;
  return `<div class="row" data-sid="${esc(s.sid)}">
    <div class="r1">
      <span class="sid">${esc(s.sid)}</span>
      <span class="dur">~${s.est_s}s</span>
      ${st.filled ? '' : '<span class="warn-dot" title="尚未生成导演参数"></span>'}
      <select class="sel-size" data-k="size" title="景别">${opts(SIZES, st.size)}</select>
      <select class="sel-cam" data-k="cam.kind" title="运镜型 (八步封顶)">${opts(CAMS, st.cam.kind)}</select>
      <select class="sel-dir" data-k="cam.dir" title="运镜方向"${noDir ? ' disabled' : ''}>${opts(dirs.length ? dirs : ['无'], noDir ? '无' : st.cam.dir)}</select>
      <select class="sel-amp" data-k="cam.amp" title="运镜幅度 (H3: small/large amplitude, 中没有词)"${fixed ? ' disabled' : ''}>${opts(AMPS, st.cam.amp, AMP_LAB)}</select>
      <select class="sel-speed" data-k="cam.speed" title="运镜速度 (H3: slow/fast speed, 常速没有词)"${fixed ? ' disabled' : ''}>${opts(SPEEDS, st.cam.speed, SPD_LAB)}</select>
      <span class="flex1"></span>
      <button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}" title="重掷此镜四元组 (一镜一批 LLM)">↻</button>
      <button class="kf-btn" data-act="kf" title="展开画面句">画面句 ▸</button>
    </div>
    <div class="r2">
      <label>主体运动</label><input type="text" data-k="motion" value="${esc(st.motion)}" placeholder="只动画面句已有的实体">
      <label>光影</label><input type="text" data-k="light" value="${esc(st.light)}" placeholder="光源方向/色温, 与相邻镜连续">
    </div>
    <div class="cam-en" title="实际发给 H3 的运镜句 (服务端单源装配)">…</div>
    <div class="kf">${esc(s.keyframe_zh || '')}</div>
  </div>`;
}

function bindRows() {
  $('blocks').querySelectorAll('.row').forEach(row => {
    const sid = row.dataset.sid;
    row.querySelectorAll('[data-k]').forEach(el => {
      el.addEventListener('change', () => {
        const k = el.dataset.k;
        if (k === 'size') STATE[sid].size = el.value;
        else if (k.startsWith('cam.')) STATE[sid].cam[k.slice(4)] = el.value;
        else STATE[sid][k] = el.value;
        STATE[sid].filled = true;
        DIRTY.add(sid);
        if (k === 'cam.kind') refreshRow(row, sid);
        else if (k.startsWith('cam.')) {
          paintClause(sid);
        }
        renderRhythm(); renderCounts(); renderSide(); refreshDirty();
      });
      el.addEventListener('input', () => {
        const k = el.dataset.k;
        if (k === 'motion' || k === 'light') {
          STATE[sid][k] = el.value; DIRTY.add(sid); refreshDirty();
        }
      });
    });
    const rb = row.querySelector('[data-act="reroll"]');
    rb.onclick = async () => {
      const sid = rb.dataset.sid;
      rb.disabled = true; rb.textContent = '⏳';
      try {
        const r = await (await fetch(`${BASE}/direct/reroll`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sid }) })).json();
        if (r.direct) { toast(`✓ ${sid} 四元组已重掷`); await boot(); }
        else toast('重掷失败: ' + (r.detail || ''));
      } catch (e) { toast('重掷失败: ' + e.message); }
      finally { rb.disabled = false; rb.textContent = '↻'; }
    };
    const btn = row.querySelector('[data-act="kf"]');
    const kf = row.querySelector('.kf');
    btn.onclick = () => {
      kf.classList.toggle('open');
      btn.textContent = kf.classList.contains('open') ? '收起 ▴' : '画面句 ▸';
    };
  });
}

function refreshRow(row, sid) {
  const kind = STATE[sid].cam.kind;
  const fixed = NO_MOVE.includes(kind);
  const as = (CAM_DEFS[kind] || {}).amp_speed;   // 律档: 摇/升降 幅速锁 大+慢
  const locked = !!as && typeof as === 'object';
  if (locked) { STATE[sid].cam.amp = as.amp; STATE[sid].cam.speed = as.speed; }
  const dirs = (CAM_DEFS[kind] || { dirs: [] }).dirs;
  row.querySelector('.sel-amp').disabled = fixed || locked;
  row.querySelector('.sel-speed').disabled = fixed || locked;
  const dirSel = row.querySelector('.sel-dir');
  dirSel.disabled = dirs.length === 0;
  if (dirs.length && !dirs.includes(STATE[sid].cam.dir)) STATE[sid].cam.dir = dirs[0];
  dirSel.innerHTML = opts(dirs.length ? dirs : ['无'], dirs.length ? STATE[sid].cam.dir : '无');
  dirSel.value = dirs.length ? STATE[sid].cam.dir : '无';
  paintClause(sid);
}

/* ── 配额 ─────────────────────────────────────────────── */
function quotas() {
  const n = SHOTS.length || 1;
  let big = 0, micro = 0, sameAdj = 0, prev = null;
  for (const s of SHOTS) {
    const sz = STATE[s.sid].size;
    if (sz === '大场景' || sz === '全景') big++;
    if (sz === '特写') micro++;
    if (prev === sz) sameAdj++;
    prev = sz;
  }
  return { n, big, micro, sameAdj,
           needBig: Math.ceil(n / 4), maxMicro: Math.ceil(n / 2) };
}

function renderSide() {
  const q = quotas();
  const rows = [
    ['大场景+全景 ≥⌈n/4⌉', `${q.big} / ${q.needBig}`, q.big >= q.needBig ? 'ok' : 'bad'],
    ['特写 ≤⌈n/2⌉', `${q.micro} / ${q.maxMicro}`, q.micro <= q.maxMicro ? 'ok' : 'bad'],
    ['相邻镜同景别', `${q.sameAdj} 处`, q.sameAdj === 0 ? 'ok' : 'warn'],
  ];
  $('quotas').innerHTML = rows.map(([k, v, cls]) =>
    `<div class="qrow ${cls}"><span>${k}</span><b>${v}</b></div>`).join('');
  // 1109 修 (⑤ 不亮实锤): direct_issues (槽长超限等拦截项) 此前只活在保存
  // toast 的 3.5 秒里, 页面零渲染 + gatecard 只看景别节奏 → 「达标却闸关」
  // 矛盾 UI。问题必须给解法 (1109 令): 运镜超限镜挂一键「改固定」。
  const blockers = Object.entries(D_ISSUES).filter(([k]) => k !== '弧级');
  $('dissues').innerHTML =
    (q.sameAdj
      ? `<div class="ditem">相邻镜景别相同 ${q.sameAdj} 处 — 每镜与上一镜至少换一档</div>` : '')
    + blockers.map(([sid, lst]) => lst.map(t => {
        const ov = /运镜镜槽长/.test(t);
        return `<div class="ditem">${sid}: ${t}${ov
          ? ` <button class="btn-fix" data-fixcam="${sid}" title="运镜改固定镜 (固定镜走 r2v 工位, 可超 10s) — 一键改+保存">⚡ 改固定</button>
             <button class="btn-fix" data-goto3="${sid}" title="槽长=口播行长度, 只有 ③ 页能拆 → 跳回 ③ 点「⚡ 一键拆行」">↩ 回③拆行</button>` : ''}</div>`;
      }).join('')).join('');
  $('dissues').querySelectorAll('[data-fixcam]').forEach(b => {
    b.onclick = async () => {
      const sid = b.dataset.fixcam;
      STATE[sid].cam.kind = '固定';
      STATE[sid].cam.dir = '无'; STATE[sid].cam.amp = ''; STATE[sid].cam.speed = '';
      DIRTY.add(sid);
      b.disabled = true; b.textContent = '⏳';
      paintClause(sid);
      await saveDirect();
    };
  });
  $('dissues').querySelectorAll('[data-goto3]').forEach(b => {
    b.onclick = () => { location.href = './shots.html' + location.search + '#' + b.dataset.goto3; };
  });

  const ok = q.big >= q.needBig && q.micro <= q.maxMicro && q.sameAdj === 0
    && !blockers.length;
  const box = $('gatecard');
  if (FROZEN) {
    box.className = 'card gatebar on';
    box.innerHTML = '<div class="gt gold-t">🔒 已放行冻结</div>'
      + '<div class="note">英文化已固化 — 改稿须重跑 ③ 分镜 (作废翻译)</div>';
    document.querySelectorAll('#blocks select, #blocks input').forEach(el => { el.disabled = true; });
    $('btn-run').disabled = true;
    return;
  }
  box.className = 'card gatebar' + (ok ? ' on' : '');
  const nFilled = SHOTS.filter(s => STATE[s.sid] && STATE[s.sid].filled).length;
  const why = !nFilled
    ? `${SHOTS.length} 镜 0 已填 — 先点 🎬 生成导演 (③ 重跑后参数作废清零)`
    : nFilled < SHOTS.length
      ? `${nFilled}/${SHOTS.length} 镜已填 — ↻ 单镜补掷或 🎬 重生成, 全填才能放行`
      : blockers.length
        ? `右栏 ${blockers.length} 镜拦截项待清 (槽长超限) — 改固定或回 ③ 拆行`
        : '先调整景别节奏 (右栏三项全绿)';
  box.innerHTML = ok
    ? '<div class="gt gold-t">◉ 导演参数达标</div><div class="note">点底部「确认放行」触发英文化批 → ⑤</div>'
    : `<div class="gt">🔒 导演未完成</div><div class="note">${why}</div>`;
}

function refreshDirty() {
  const confirmMode = !DIRTY.size && GATES.direct;
  $('save-label').textContent = confirmMode ? '✅ 确认放行导演' : '💾 保存导演';
  $('dirty').innerHTML = DIRTY.size
    ? `改稿 <b class="gold-t">${DIRTY.size} 处未保存</b>` : '改稿 0 处未保存';
  $('n-dirty').textContent = DIRTY.size ? ` (${DIRTY.size})` : '';
}

boot();

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}
