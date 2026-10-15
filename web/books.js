// books 三页共享 (2026-08-20) — 书库 / 内容库 / 讲书
// books router 无 /api 前缀, 此处 api 胶水不带前缀 (与后端路径直连).
const BOOKS = { cur: null };
let _busy = false;
// 支持 # 前缀 (getElementById): $('#id') 与 $('id') 均可
const $ = id => document.getElementById(String(id).replace(/^#/, ''));

function esc(s) { return (s == null ? '' : String(s)).replace(/[<>&]/g, c => ({ '<':'&lt;', '>':'&gt;', '&':'&amp;' }[c])); }

// 0917 用户令: 展示层剥【】段标注 (【0-28 秒｜钩子】等) — 【】为标注专用符号,
// 生成侧已禁正文使用 (episode_gen 附加指令), 故剥离永不错伤; 仅用于只读展示,
// 修稿框仍看原文 (标注是 TTS/动画/换钩子的制作数据, 编辑存盘洗掉会断产线)。
function stripMarks(t) {
  return String(t == null ? '' : t)
    .replace(/^【[^】]*】[ \t]*$/gm, '')   // 整行标注 (段标题行) 整行删
    .replace(/【[^】]*】/g, '')            // 行内残留块
    .replace(/\n{3,}/g, '\n\n');          // 收起空行
}

async function api(path, opts, timeoutMs = 12 * 60 * 1000) {
  // 0908: AbortController 超时 — 后端重启/连接死时 fetch 永挂, 按钮卡死无解;
  // 长任务 (pro 生成链 2~10 分钟) 12 分钟兜底, 超时解锁按钮并提示
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let resp;
  try {
    resp = await fetch(path, { ...opts, signal: ctrl.signal });
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('请求超时 (12分钟) — 后端可能已重启, 刷新页面后重试');
    throw e;
  } finally {
    clearTimeout(timer);
  }
  if (!resp.ok) {
    let detail = '';
    try { detail = (await resp.json()).detail || ''; } catch (_) {}
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json().catch(() => ({}));
}

function toast(msg, type = 'info') {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg;
  el.style.background = type === 'error' ? '#7f1d1d' : (type === 'success' ? '#14532d' : '#1e40af');
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 4200);
}

// ── 共享渲染: 流程条 + 下一步入口 + 书名 shell ──
const JOB_STATUS = { created:'待补全', input_review:'待确认输入', roadmap_review:'待确认总纲', writing:'逐集写作中', done:'全部完成', failed:'失败' };

function flowBarHTML(prog) {
  const steps = (prog && prog.steps) || ['输入补全','评论层+素材','多集总纲','逐集确认','进产线'];
  const done = (prog && prog.done) || [false,false,false,false,false];
  const current = (prog && prog.current) || 1;
  const nDone = done.filter(Boolean).length;
  const bar = `<div class="flow-bar">
    ${steps.map((s, i) => {
      const cls = done[i] ? 'done' : (i + 1 === current ? 'active' : 'todo');
      return `<div class="flow-step ${cls}"><span class="flow-num">${i+1}</span>${s}</div>`;
    }).join('<div class="flow-arrow">→</div>')}
  </div>
  <div class="kv" style="margin:0.4rem 0"><span class="k">进度</span>${nDone}/${steps.length} 步完成 · 当前: <b>${steps[current-1]}</b></div>`;
  return bar;
}

function nextActionHTML(na) {
  if (!na) return '';
  return `<div class="next-action">
    <span class="next-label">▶ 下一步</span>
    <button class="sm" style="background:#16a34a" onclick="doNext('${na.button}', this)">${esc(na.label)}</button>
    <span class="hint">${esc(na.hint || '')}</span>
  </div>`;
}

function renderBookShell() {
  const b = BOOKS.cur;
  if (!$('book-shell')) return;
  $('book-shell').innerHTML = `
    <h1><a href="/web/books.html" style="color:#64748b;text-decoration:none">←</a> 《${esc(b.book_title)}》 <span class="badge">${JOB_STATUS[b.status] || b.status}</span></h1>
    ${flowBarHTML(b.progress)}
    ${nextActionHTML(b.progress && b.progress.next_action)}`;
}

// 加载书 → 渲染 shell + 调页面级 renderPage
async function loadBook(id) {
  BOOKS.cur = await api('/books/' + id);
  renderBookShell();
  if (typeof renderPage === 'function') renderPage(BOOKS.cur);
}

// 通用动作: 门禁已由各页控制, 此处 busy 锁 + POST + 刷新 + toast
async function run(path, btn) {
  if (_busy) { toast('有操作进行中，请等待', 'info'); return; }
  _busy = true;
  const isGen = /generate/.test(path);
  if (btn) {
    btn.disabled = true;
    btn.dataset.origLabel = btn.dataset.origLabel || btn.textContent;
    const t0 = Date.now();
    btn._timer = setInterval(() => {
      const m = Math.floor((Date.now() - t0) / 60000), s = Math.floor(((Date.now() - t0) % 60000) / 1000);
      btn.textContent = isGen && m >= 1 ? `⏳ 生成中 ${m}分${s}秒 (pro 链 2~10 分钟, 勿关页)` : `⏳ 处理中 ${m}:${String(s).padStart(2, '0')}`;
    }, 1000);
  }
  try {
    await api(`/books/${BOOKS.cur.id}/${path}`, { method: 'POST' });
    await loadBook(BOOKS.cur.id);
    toast('完成', 'success');
  } catch (e) {
    toast('失败: ' + e.message, 'error');
  } finally {
    _busy = false;
    if (btn) {
      if (btn._timer) { clearInterval(btn._timer); btn._timer = null; }
      btn.textContent = btn.dataset.origLabel || btn.textContent;
      btn.disabled = false;
    }
  }
}

// P0 下一步入口: 后端 progress.next_action 驱动; 'produce' 批量进产线后跳 audio
async function doNext(button, btn) {
  if (button === 'produce') {
    let lastSid = null;
    for (const e of BOOKS.cur.episodes || []) {
      if (e.status === 'confirmed' && !e.script_id) {
        toast(`第${e.ep}集进产线…`, 'info');
        const r = await api(`/books/${BOOKS.cur.id}/episodes/${e.ep}/produce`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ persona_id: currentPersonaId() }),
        });
        lastSid = r.script_id;
      }
    }
    await loadBook(BOOKS.cur.id);
    if (lastSid) { toast('已全部进产线，跳转音频页…', 'success'); setTimeout(() => location.href = '/web/audio.html?script_id=' + lastSid, 1000); }
    else toast('没有待进产线的已确认集', 'info');
    return;
  }
  await run(button, btn);
}

// 讲书页覆盖: 当前选中人设 id (books_story.js 定义)
function currentPersonaId() {
  const sel = $('#produce-persona');
  return sel ? (sel.value || null) : null;
}

// ── 书库页专属 (books.html) ──
let BOOKS_LIST = [], _logCount = 0, _logTimer = null;
const KIND_BADGE = { essence: ['精华', ''], full: ['全书', 'need'], distilled: ['蒸馏', 'draft'] };
const EP_BADGE = { pending:'待生成', generating:'生成中', draft:'待确认', confirmed:'已确认' };

async function loadBooks() {
  // 2026-08-22: 并发取 book-sources, 建"已蒸馏"映射 (绿标签判定)
  const [bRes, srcRes] = await Promise.all([
    api('/books'),
    api('/book-sources').catch(() => ({ sources: [] })),
  ]);
  BOOKS_LIST = bRes.books;
  const _bsrc = srcRes.sources || [];
  const _bBase = fn => String(fn || '').replace(/\.蒸馏/, '').replace(/\.(txt|md|epub)$/i, '').replace(/\(zhihailib\.com\)/i, '').trim();
  const _distilled = new Set(_bsrc.filter(s => s.kind === 'distilled').map(s => _bBase(s.filename)));
  const _clean = t => String(t || '').replace(/\(zhihailib\.com\)/i, '').trim();  // 屏蔽 (zhihailib.com) 只显书名
  const JOB = { created:'待补全', input_review:'待确认输入', roadmap_review:'待确认总纲', writing:'逐集写作中', done:'全部完成', failed:'失败' };
  const TIER_ICON = { green: '🟢', yellow: '🟡', red: '🔴' };  // 安全评级颜色图标 (2026-08-22)
  $('#book-list').innerHTML = BOOKS_LIST.map(b => {
    const eps = b.episodes || [];
    const nConf = eps.filter(e => e.status === 'confirmed').length;
    const prog = b.progress || { done: [false,false,false,false,false], current: 1 };
    const nDone = prog.done.filter(Boolean).length;
    const dots = prog.done.map(d => `<span class="dot ${d ? 'on' : ''}"></span>`).join('');
    // 已蒸馏: source_path 指向蒸馏 或 书名匹配到蒸馏 txt
    const hasDistill = (b.source_path && b.source_path.includes('蒸馏'))
      || _distilled.has(_bBase(_clean(b.book_title)));
    return `
    <div class="book-card" onclick="location.href='/web/books_content.html?book_id=${b.id}'">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">
        <h3 style="margin:0 0 0.4rem">${TIER_ICON[b.tier] || ''}《${esc(_clean(b.book_title))}》 <span class="badge">${JOB[b.status] || b.status}</span>${hasDistill ? '<span class="badge distilled">已蒸馏</span>' : ''}${b.l0_distilled ? '<span class="badge l0">L0</span>' : ''}</h3>
        <button class="sm secondary" onclick="event.stopPropagation();deleteBook('${b.id}','${esc(_clean(b.book_title))}')" style="flex-shrink:0" title="删除本书">删除</button>
      </div>
      <div class="kv"><span class="k">作者</span>${esc(b.author || '-')}</div>
      <div class="kv"><span class="k">进度</span>${nDone}/5 步 <span class="mini-dots">${dots}</span> ${eps.length ? `· 逐集已确认 ${nConf}/${eps.length}` : '· 未生成总纲'}</div>
      <div class="eps">${eps.map(e => `<span class="ep-dot ${e.status}" title="${EP_BADGE[e.status] || e.status}">E${e.ep}</span>`).join('') || '<span class="hint">未生成总纲</span>'}</div>
    </div>`;
  }).join('') || '<div class="hint">暂无项目</div>';
}

// 单本蒸馏: 事件委托 (data-distill 书名 → 反查 path), 避免反斜杠进 HTML 属性
let _libSrcs = [];
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-distill]');
  if (btn) distillSingleByTitle(btn.dataset.distill);
});
async function loadLib() {
  const r = await api('/book-sources');
  const srcs = r.sources || [];
  _libSrcs = srcs;
  // 按书名去 .蒸馏 后缀分组: {书名: [source,...]}
  // 2026-08-22: base 同时去 (zhihailib.com) — full 文件名带域名后缀、蒸馏不带,
  // 不去会导致"已蒸馏"的书匹配不上 → 仍显示可蒸馏按钮
  const base = fn => fn.replace(/\.蒸馏/, '').replace(/\.(txt|md|epub)$/i, '').replace(/\(zhihailib\.com\)/i, '').trim();
  // 2026-08-22: 屏蔽 (zhihailib.com) 后缀只显书名
  const clean = n => String(n || '').replace(/\(zhihailib\.com\)/i, '').trim();
  // 蒸馏状态全集 (可蒸馏判定用), 但蒸馏 txt 文档本身不再显示
  const distilledBases = new Set(srcs.filter(s => s.kind === 'distilled').map(s => base(s.filename)));
  // 2026-08-22: 书库只列"全书" (full) — 精华(essence)/蒸馏 文档不再显示
  const visible = srcs.filter(s => s.kind === 'full');
  const groups = {};
  visible.forEach(s => { (groups[base(s.filename)] = groups[base(s.filename)] || []).push(s); });
  // 可蒸馏判定: full 形态且无 .蒸馏
  const isDistillable = s => s.kind === 'full' && !distilledBases.has(base(s.filename));

  // 组排序 (2026-08-22): 未蒸馏置顶 / 已蒸馏沉底, 同类按 mtime 降序 — 自然分两类, 阅读方便
  const FOLD_MS = 30 * 24 * 3600 * 1000;  // 30 天折叠阈值
  const rows = Object.entries(groups).map(([name, list]) => {
    const maxMtime = Math.max(...list.map(s => s.mtime || 0));
    const hasFull = list.some(s => s.kind === 'full');
    const distillable = list.some(isDistillable);
    // 有可蒸馏任务 → 不折叠 (新任务需可见); 否则超 30 天折叠
    const stale = !distillable && (Date.now() / 1000 - maxMtime) > FOLD_MS / 1000;
    return { name, list, maxMtime, hasFull, distillable, stale };
  }).sort((a, b) => {
    const ad = distilledBases.has(a.name) ? 1 : 0;  // 已蒸馏 → 1 (沉底)
    const bd = distilledBases.has(b.name) ? 1 : 0;
    if (ad !== bd) return ad - bd;                   // 未蒸馏(0) 在前
    return b.maxMtime - a.maxMtime;
  });

  const nDistillable = rows.filter(x => x.distillable).length;
  const nDistilled = rows.filter(x => distilledBases.has(x.name)).length;
  $('#lib-panel').innerHTML = `<h2>书库 (${rows.length} 本<span class="hint"> · ${nDistillable} 待蒸馏 · ${nDistilled} 已蒸馏</span>)</h2>` +
    rows.map(g => {
      const badges = g.list.map(s => {
        const [label, cls] = KIND_BADGE[s.kind] || ['?', ''];
        return `<span class="badge ${cls}">${label}</span>`;
      }).join('');
      // 2026-08-22: 该书有蒸馏 txt → "全书"等 badge 后加绿色"已蒸馏"标签
      const hasDistill = distilledBases.has(g.name);
      const sizeTxt = g.list.map(s => `${(s.size/1024).toFixed(0)}KB`).join(' / ');
      const previewBtn = g.list.find(s => s.kind !== 'full')
        ? `<button class="sm secondary" onclick="previewSrc('${esc(g.list[0].filename)}')">预览</button>` : '';
      const distillBtn = g.distillable
        ? `<button class="sm" style="background:#7c3aed" onclick="event.stopPropagation();distillSingleByTitle('${esc(g.name)}')">🔬 蒸馏</button>`
        : (hasDistill ? `<button class="sm secondary" onclick="event.stopPropagation();distillSingleByTitle('${esc(g.name)}')" title="已蒸馏，点击重新蒸馏(覆盖现有蒸馏稿)">🔄 重蒸馏</button>` : '');
      const ts = g.maxMtime ? new Date(g.maxMtime * 1000).toLocaleDateString('zh-CN') : '';
      const disp = clean(g.name);  // 显示名去 (zhihailib.com), data-distill 仍用原始 name 反查 path
      if (g.stale) {
        // 折叠: 单行, 点开展开
        return `<div class="lib-group stale">
          <div class="lib-group-head" onclick="this.parentElement.classList.toggle('open')">
            <span class="fold-arrow">▶</span> <b>《${esc(disp)}》</b> ${badges}${hasDistill ? '<span class="badge distilled">已蒸馏</span>' : ''}
            <span class="hint">${sizeTxt} · ${ts}</span>
            <span style="flex:1"></span>${distillBtn}
          </div>
          <div class="lib-group-body">${previewBtn}</div>
        </div>`;
      }
      return `<div class="lib-group">
        <div class="lib-group-head">
          <b>《${esc(disp)}》</b> ${badges}${hasDistill ? '<span class="badge distilled">已蒸馏</span>' : ''}
          <span class="hint">${sizeTxt} · ${ts}</span>
          <span style="flex:1"></span>${distillBtn}${previewBtn}
        </div>
      </div>`;
    }).join('') || '<div class="hint">书库为空</div>';
  return srcs;
}

async function distillSingleByTitle(name) {
  // 从缓存源列表反查可蒸馏的 path (full 形态且无 .蒸馏)
  // 2026-08-22: base 同步去 (zhihailib.com), 与 loadLib 分组键一致
  const base = fn => fn.replace(/\.蒸馏/, '').replace(/\.(txt|md|epub)$/i, '').replace(/\(zhihailib\.com\)/i, '').trim();
  const cand = _libSrcs.find(s => s.kind === 'full' && base(s.filename) === base(name) + '');
  if (!cand) { toast('未找到该书源', 'error'); return; }
  const path = cand.path;
  const pname = name.replace(/ \([^)]*\)$/, '');  // 去 (zhihailib.com) 后缀显示
  // 2026-08-22: 已蒸馏 → 提示覆盖; 0925: force=true 放行重蒸 (旧稿废时唯一重建路)
  const hasD = _libSrcs.some(x => x.kind === 'distilled' && base(x.filename) === base(cand.filename));
  if (!confirm(`🔬 对《${pname}》启动本地蒸馏？\n\n将拉起本地 Qwen3.6 (占 28G 显存)，对该书全文蒸馏出精华稿。\n结束后自动释放显存。${hasD ? '\n\n⚠️ 已存在蒸馏稿，重新蒸馏将覆盖！' : ''}`)) return;
  try {
    await api('/distill/start-single?path=' + encodeURIComponent(path) + (hasD ? '&force=true' : ''), { method: 'POST' });
    toast(`《${pname}》蒸馏已启动`, 'info');
    _distillStart = Date.now();
    _logCount = 0;
    connectDistillSSE();
    pollDistill();
  } catch (e) {
    toast('蒸馏启动失败: ' + e.message, 'error');
  }
}

async function previewSrc(fn) {
  const r = await api('/book-sources/preview?filename=' + encodeURIComponent(fn));
  $('#pv-title').textContent = '预览: ' + r.filename;
  $('#pv-body').textContent = r.preview;
  $('#preview-modal').classList.add('active');
}

// 源头书选题池 (2026-08-23): 书库缺失的源头书 → 下一批拆书候选
async function loadPool() {
  const el = $('#pool-panel');
  if (!el) return;
  let pool = [];
  try { pool = (await api('/book-sources/pool')).pool || []; } catch (_) {}
  if (!pool.length) { el.style.display = 'none'; return; }
  el.style.display = '';
  $('#pool-body').innerHTML = pool.map(c => {
    const cited = (c.cited_by || []).map(x => `《${esc(x.book)}》第${(x.chapters || []).join('、')}章`).join(' · ');
    const okAuthor = c.author && !['未知', '未提及', '无', '不详'].includes(c.author);
    return `<div class="lib-group"><div class="lib-group-head">
      <b>《${esc(c.name)}》</b>
      ${okAuthor ? `<span class="hint">${esc(c.author)}</span>` : ''}
      <span class="badge">★${c.weight} 被引用</span>
      <span class="hint">${cited}</span>
    </div></div>`;
  }).join('');
}

function renderLog(events) {
  if (!events.length) return;
  $('#distill-log').style.display = '';
  $('#distill-log-body').innerHTML = events.map(e =>
    `<span class="hint">${e.ts}</span> <span style="${e.level === 'ok' ? 'color:#86efac' : e.level === 'error' ? 'color:#fca5a5' : 'color:#94a3b8'}">${esc(e.msg)}</span>`).join('\n');
  $('#distill-log-body').scrollTop = 99999;
  _logCount = events.length;
}

// 蒸馏进度显示 (2026-08-20): 点击启动时记录开始时间, pollDistill 算百分比+耗时
let _distillStart = null;
let _wasRunning = false;  // 完成检测: running→false 时 toast 提示

async function pollDistill() {
  const st = await api('/distill/status');
  renderLog(st.events || []);
  // 确认按钮: 运行中隐藏, 完成后显示 (2026-08-20)
  const doneBtn = $('#distill-done-btn');
  if (doneBtn) doneBtn.style.display = st.running ? 'none' : (st.events && st.events.length ? '' : 'none');
  const liveBadge = $('#distill-live');
  if (liveBadge) liveBadge.style.display = st.running ? '' : 'none';
  let label = '';
  if (st.running) {
    const prog = st.progress || {};
    const book = esc(prog.book || '');
    const ch = String(prog.chapter || '');   // 形如 "41/58"
    const m = ch.match(/(\d+)\s*\/\s*(\d+)/);
    let pct = '';
    if (m) {
      const cur = parseInt(m[1], 10), tot = parseInt(m[2], 10);
      if (tot > 0) pct = ` (${Math.round(cur / tot * 100)}%)`;
    }
    // 耗时: 本页启动记录 → 刷新后从首条事件时间推断
    let dur = '';
    const stTs = _distillStart || (st.events && st.events[0] ? evTs(st.events[0].ts) : null);
    if (stTs) {
      const sec = Math.floor((Date.now() - stTs) / 1000);
      if (sec > 0 && sec < 24 * 3600) {
        const h = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
        dur = ` · 已耗时 ${h ? h + 'h ' : ''}${mm}min ${ss}s`;
      }
    }
    label = `蒸馏中: ${book} ${esc(ch)}${pct}${dur}`;
  } else {
    label = st.events.length ? '批次结束 (显存已释放)' : '';
    // 完成检测 (2026-08-20): running 从 true → false 时, 弹出完成提示
    if (_wasRunning) {
      const doneOk = (st.done || []).filter(x => !/FAILED/.test(x)).length;
      const failed = (st.done || []).filter(x => /FAILED/.test(x)).length;
      toast(st.error ? `蒸馏失败: ${st.error}` : `蒸馏完成 ✓ 成功 ${doneOk}${failed ? ` / 失败 ${failed}` : ''}`, st.error ? 'error' : 'success');
      // 完成后自动刷新书库, 显示新蒸馏产物
      loadLib(); loadBooks();
    }
    if (!st.running) _distillStart = null;  // 完成后清计时
  }
  _wasRunning = st.running;
  $('#distill-state').textContent = label;
  $('#btn-distill').disabled = st.running;
  if (st.running || (st.events && st.events.length)) { if (!_logTimer) _logTimer = setTimeout(pollDistill, 2000); }
  else { _logTimer = null; }
  return st;
}

// 完成后确认: 隐藏蒸馏日志面板 + 清状态 + 清后端事件缓冲 (恢复正常页面)
function dismissDistill() {
  const logPanel = $('#distill-log');
  if (logPanel) logPanel.style.display = 'none';
  const state = $('#distill-state');
  if (state) state.textContent = '';
  const doneBtn = $('#distill-done-btn');
  if (doneBtn) doneBtn.style.display = 'none';
  const liveBadge = $('#distill-live');
  if (liveBadge) liveBadge.style.display = 'none';
  _logCount = 0;
  _wasRunning = false;
  if (_logTimer) { clearTimeout(_logTimer); _logTimer = null; }
  // 清后端事件缓冲, 刷新后不再显示旧日志
  api('/distill/clear', { method: 'POST' }).catch(() => {});
  loadLib(); loadBooks();
}

// HH:MM:SS 事件时间戳 → epoch ms (当日); 跨午夜不准但够用
function evTs(ts) {
  const m = String(ts || '').match(/(\d{1,2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(),
    parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)).getTime();
}

function connectDistillSSE() {
  const es = new EventSource('/distill/events');
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === 'distill_done') { toast('蒸馏完成 ✓', 'success'); es.close(); pollDistill(); }
    if (d.type === 'distill_error') { toast('蒸馏失败: ' + (d.msg || ''), 'error'); es.close(); pollDistill(); }
  };
  es.onerror = () => es.close();
}

async function startDistill() {
  const st = await api('/distill/status');
  const pend = (await api('/distill/pending')).pending;
  if (!confirm(`启动批量蒸馏 ${pend.length} 本? 占用 4090 显存, 批次结束全杀 llama-server 腾卡。`)) return;
  await api('/distill/start', { method: 'POST' });
  _distillStart = Date.now();
  _logCount = 0;
  connectDistillSSE();
  pollDistill();
}

// 源头书引用榜 (2026-08-23): 跨书汇总 → 被反复引用的源头 = 有分量
async function loadLeaderboard() {
  const el = $('#board-panel');
  if (!el) return;
  let board = [];
  try { board = (await api('/book-sources/leaderboard')).board || []; } catch (_) {}
  if (!board.length) { el.style.display = 'none'; return; }
  el.style.display = '';
  const icon = c => c.type === 'book' ? '📕' : (c.type === 'person' ? '👤' : '🔹');
  $('#board-body').innerHTML = board.map(c => {
    const okAuthor = c.author && !['未知', '未提及', '无', '不详'].includes(c.author);
    const cited = (c.cited_by || []).map(x => `《${esc(x.book)}》第${(x.chapters || []).join('、')}章`).join(' · ');
    const libTag = c.in_library === true ? '<span class="badge distilled">已入库</span>'
      : (c.in_library === false ? '<span class="badge need">可拆</span>' : '');
    return `<div class="lib-group"><div class="lib-group-head">
      <b>${icon(c)}《${esc(c.name)}》</b>
      ${okAuthor ? `<span class="hint">${esc(c.author)}</span>` : ''}
      <span class="badge">★${c.weight}</span>${libTag}
      <span class="hint">${cited}</span>
    </div></div>`;
  }).join('');
}

async function loadAll() { await loadLib(); await loadBooks(); await loadPool(); await loadLeaderboard(); await pollDistill(); }

async function openCreate() {
  // 0907: 下拉=蒸馏合格即入列 (L0 建后自动补); 人物下拉=拆书账号 (静读书/老谭拆书)
  const r = (await api('/book-sources/ready')).ready || [];
  const sel = $('#c-title');
  if (!r.length) {
    sel.innerHTML = '<option value="">— 暂无蒸馏完成的书 —</option>';
  } else {
    sel.innerHTML = '<option value="">— 选择蒸馏完成的书 —</option>' +
      r.map(s => `<option value="${esc(s.path)}">${esc(s.book_title)}${s.l0_ready ? '' : '（L0建后自动补跑）'}</option>`).join('');
  }
  // 人物下拉: 拆书线 persona (prompt_template 含 book), 默认静读书 (0907 双人物)
  // ⚠ personas 挂在 /api 前缀下, 而本页 api() 胶水不带前缀 — 这里直连全路径
  try {
    const ps = (await api('/api/personas')).filter(p => (p.prompt_template || '').includes('book'));
    const psel = $('#c-persona');
    if (ps.length) {
      psel.innerHTML = ps.map(p => {
        const tag = p.brand_tag ? ` — ${p.brand_tag}` : '';
        return `<option value="${esc(p.id)}" ${p.brand_name === '静读书' ? 'selected' : ''}>${esc(p.name)}${esc(tag)}</option>`;
      }).join('');
    }
  } catch (_) { /* 人物加载失败保底静态选项 */ }
  $('#create-modal').classList.add('active');
}

function closeModal(id) { $(id).classList.remove('active'); }

async function createBook() {
  const sel = $('#c-title');
  const srcPath = sel.value;
  if (!srcPath) return alert('请选择蒸馏完成的书');
  const personaId = $('#c-persona') ? $('#c-persona').value : '';
  if (!personaId) return alert('请选择人物 (人设+音色+品牌)');
  const btn = document.querySelector('#create-modal .modal button[onclick="createBook()"]');
  if (btn) { btn.disabled = true; btn.textContent = '创建中…'; }
  // 书名: 从蒸馏文件路径取文件名去 .蒸馏.txt
  const fn = srcPath.split(/[\\/]/).pop() || '';
  const bookTitle = fn.replace(/\.蒸馏(\.txt)?$/, '') || '未命名';
  // 2026-08-23: 作者/卖点自动从蒸馏带出, 弹窗只留人物(0907)+卖点可选
  const body = {
    book_title: bookTitle.replace(/[《》]/g, ''),
    selling_point: $('#c-sell').value || null,
    source_path: srcPath,
    persona_id: personaId || null,
  };
  try {
    const r = await api('/books', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    closeModal('create-modal');
    const prep = r.prep || {};
    if (prep.status === 'preparing') {
      // 后台跑 L0/facing, books_content 页轮询 /prep 等就绪
      toast(`《${bookTitle}》已建，正在蒸馏 L0/facing（后台）…`, 'info');
    } else if (prep.note) {
      toast(`《${bookTitle}》已建，但${prep.note}`, 'error');
    } else {
      toast(`《${bookTitle}》已建 + 自动填充 ${(prep.filled || []).join('、') || '无'}`, 'success');
    }
    location.href = '/web/books_content.html?book_id=' + r.id;
  } catch (e) {
    toast('创建失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '创建'; }
  }
}

// 删除拆书项目: 二次确认 (破坏性, 级联删 6 集 + 产线产物)
async function deleteBook(id, title) {
  if (!confirm(`⚠️ 确定删除《${title}》？\n\n将删除全书 6 集稿、总纲、评论层/素材；若已进产线，关联的 Script/音频也会一并删除。\n\n此操作不可恢复！`)) return;
  try {
    const r = await api('/books/' + id, { method: 'DELETE' });
    toast(`已删除《${r.book_title || title}》`, 'success');
    loadBooks();
  } catch (e) {
    toast('删除失败: ' + e.message, 'error');
  }
}
