// 讲书页 (2026-08-20) — 多集总纲(内联编辑) + 逐集稿 + 发布挂车 + 进产线(人设下拉)
// 依赖 books.js 共享. 渲染入口: renderPage(cur). 人设下拉复用 writing.js loadHosts 模式.
// 0908 双产线分流: 老谭拆书(laotan-book)→bs1 页单工坊(全内产), 静读书→pptx上传链路(不动)

let EDIT_EP = null;
let BOOK_LINE = null;  // 'laotan' | 'jingshu' | null (gating 未跑前)

function renderPage(b) {
  const inp = b.input_json || {};
  const eps = b.episodes || [];
  const prog = b.progress || { done: [false,false,false,false,false], current: 1 };
  const s1_ok = prog.done[0], s3_ok = prog.done[1];
  const roadmap_ok = eps.length > 0;
  const any_pending = eps.some(e => e.status === 'pending' || e.status === 'generating');
  const any_draft = eps.some(e => e.status === 'draft');
  const EP_BADGE = { pending:'待生成', generating:'生成中', draft:'待确认', confirmed:'已确认' };

  // 总纲表格 (内联编辑): 列 = 集/主题/本集秘籍/对应书中内容/核心任务/卖点/承上/启下/概念
  const roadmapTable = eps.length ? `
    <table id="roadmap-table"><tr>
      <th>集</th><th>主题</th><th>本集秘籍</th><th>对应书中内容</th><th>核心任务</th><th>卖点</th><th>承上</th><th>启下</th><th>概念</th>
    </tr>${eps.map(e => {
      const r = e.roadmap || {};
      return `<tr>
        <td>${e.ep}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="主题">${esc(r.主题 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="本集秘籍" style="color:#C9A25E">${esc(r.本集秘籍 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="对应书中内容">${esc(r.对应书中内容 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="核心任务">${esc(r.核心任务 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="卖点">${esc(r.卖点 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="承上">${esc(r.承上 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="启下">${esc(r.启下 || '')}</td>
        <td class="td-edit" contenteditable="true" data-ep="${e.ep}" data-field="概念">${esc((r.概念 || []).join('、'))}</td>
      </tr>`; }).join('')}
    </table>` : '<div class="hint">未生成</div>';

  // 逐集操作行
  const epRows = eps.map(e => {
    const btns = [];
    const genAt = e.script_generated_at
      ? ` <span class="hint" title="当前稿落定时刻 (生成/修稿刷新)">成稿 ${new Date(e.script_generated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>` : '';
    // 0913 用户令: 确认后仍可直接重生成 (单集覆盖, 不级联) — 确认≠锁死, 重新生成后回 draft 需再确认
    btns.push(`<button class="sm" onclick="run('episodes/${e.ep}/generate', this)">生成 (pro)</button>`);
    btns.push(`<button class="sm secondary" onclick="openEdit(${e.ep})" ${e.script_text ? '' : 'disabled title="先生成稿"'}>修稿</button>`);
    // TTS 先行 (0912 用户令): 稿→音→人耳验收, 复用音频加工页 (2.5 说书音色走 persona 链)
    btns.push(`<button class="sm" style="background:#5b21b6" onclick="ttsPreview(${e.ep})" ${e.script_text ? '' : 'disabled title="先生成稿"'}>🔊 TTS音频</button>`);
    btns.push(`<button class="sm warn" onclick="run('episodes/${e.ep}/confirm', this)" ${e.status==='draft' ? '' : 'disabled title="稿子需先生成并人工过稿"'}>确认3</button>`);
    btns.push(`<button class="sm secondary" onclick="rerunEp(${e.ep})" ${e.status==='pending' ? 'disabled' : ''}>级联重跑≥${e.ep}</button>`);
    if (e.status === 'confirmed' && !e.script_id) {
      // 0908 双产线: 静读书=produce 进产线(audio页 pptx链); 老谭=bs1 页单工坊直跳
      btns.push(`<button class="sm ep-prod-btn" style="background:#14532d" onclick="produceEp(${e.ep})">进产线 →</button>`);
      btns.push(`<button class="sm ep-bs1-btn" style="background:#14532d;display:none"
        onclick="location.href='/web/bs1_story.html?book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${e.ep}'">📐 页单工坊 →</button>`);
    }
    if (e.script_id) {
      btns.push(`<button class="sm ep-prod-btn" style="background:#4c1d95" onclick="location.href='/web/publish_card.html?book_id=${encodeURIComponent(BOOKS.cur.id)}&ep=${e.ep}'" title="本集封面(横/竖, HF模板化) + 发布三件套(标题/简介/标签), 数据全对应本集">📋发布卡</button>`);
      btns.push(`<a class="ep-prod-btn" href="/web/audio.html?script_id=${e.script_id}&book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${e.ep}" target="_blank" style="background:#334155;color:#e2e8f0;padding:0.3rem 0.65rem;font-size:0.82rem;border-radius:4px;text-decoration:none">音频页 ↗</a>`);
      // 0914 动画产线系统化 (与 📐页单工坊 二选一): 动线=文字→音频→分镜→K2→人检→H3→剪映草稿
      btns.push(`<a class="ep-prod-btn" href="/web/anim.html?book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${e.ep}" target="_blank" style="background:#9a3412;color:#fff;padding:0.3rem 0.65rem;font-size:0.82rem;border-radius:4px;text-decoration:none" title="动画产线工坊 (与页单工坊二选一): 音频→规划(吃真实音频)→K2生图→人检→H3视频→剪映草稿">🎬 动画 ↗</a>`);
      // 重跑进产线 (2026-08-20): 删旧 Script 重建, 换新产线/发现错误时用
      btns.push(`<button class="sm warn ep-prod-btn" onclick="rerunProduce(${e.ep})" title="删旧产线产物, 重新进产线">重跑进产线</button>`);
      btns.push(`<button class="sm ep-bs1-btn" style="display:none;background:#14532d"
        onclick="location.href='/web/bs1_story.html?book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${e.ep}'">📐 页单工坊 →</button>`);
    }
    return `<div style="border-top:1px solid #334155; padding:0.7rem 0">
      <div class="kv"><b>第${e.ep}集 ${esc(e.title || '')}</b> <span class="badge ${e.status==='confirmed'?'':'need'}">${EP_BADGE[e.status] || e.status}</span>${(e.roadmap || {})._stale_outline ? '<span class="badge" style="background:#7f1d1d;color:#fecaca" title="总纲已重排,此稿基于旧总纲 — 建议级联重跑">⚠旧总纲稿</span>' : ''}${genAt}</div>
      ${e.script_text ? `<pre class="script">${esc(stripMarks(e.script_text).slice(0, 1200))}${stripMarks(e.script_text).length > 1200 ? '\n…' : ''}</pre>` : ''}
      <div class="toolbar" style="margin-top:0.5rem;margin-bottom:0">${btns.join('')}</div>
    </div>`;
  }).join('');

  // 系列预告 (E1 导读带整体系列介绍, 2026-08-22)
  const seriesPreview = eps.find(e => e.ep === 1)?.roadmap?.系列预告 || '';
  // 灵魂三问 (0908 系列总编剧产物): 系列立意卡
  const soul = (inp && inp.灵魂三问) || null;
  const soulCard = soul ? `
    <div style="background:rgba(201,162,94,.12);border:1px solid #C9A25E;border-radius:8px;padding:.6rem .9rem;margin-bottom:.8rem;font-size:.85rem">
      <b style="color:#C9A25E">🎯 灵魂三问</b> (系列立意)
      <div style="margin-top:.35rem;line-height:1.7">
        <div>❶ <b>书的核心</b>：${esc(soul.core || '')}</div>
        <div>❷ <b>帮谁最大化</b>：${esc(soul.who || '')}</div>
        <div>❸ <b>听的收益</b>：${esc(soul.gain || '')}</div>
      </div>
    </div>` : '';

  $('#page-content').innerHTML = `
    <div class="panel">
      <h2>步骤3 · 多集总纲 <span class="badge">${roadmap_ok ? eps.length + ' 集' : ''}</span> ${(b.fandeng_full || {}).exists ? `<span class="badge" style="background:#7c2d12" title="樊登全书讲述稿已就绪 — 总编剧按稿切系列">🎭全书稿 ${((b.fandeng_full.chars / 1000) || 0).toFixed(1)}k字</span>` : ''} <span class="hint">单元格可直接编辑，加入你的个人视角</span></h2>
      ${seriesPreview ? `<div class="kv" style="background:rgba(30,58,138,0.15);padding:0.5rem 0.75rem;border-radius:6px;margin-bottom:0.6rem;font-size:0.85rem">📺 <b>系列预告</b>：${esc(seriesPreview)}</div>` : ''}
      ${soulCard}
      ${roadmapTable}
      <div class="toolbar" style="margin-top:0.8rem">
        <button class="sm secondary" onclick="syncStoryL0()" title="从蒸馏 facing-units 自动编排总纲">🔄 同步蒸馏</button>
        <button class="sm" style="background:#7c2d12" onclick="genFandengFull(this)" title="樊登前置架构: 蒸馏一遍成型全书讲述稿(2-5分钟) → 总编剧按稿切系列, 逐集拐棍=切片">🎭 樊登全书稿</button>
        ${(b.fandeng_full || {}).exists ? `<button class="sm secondary" onclick="window.open('/books/${b.id}/fandeng-full', '_blank')" title="打开 data/l0/${encodeURIComponent(b.book_title)}/fandeng_full.txt (新标签, 可 Ctrl+F)">📖 全书稿</button>` : ''}
        <button class="sm warn" onclick="rerunOutline(this)" title="系列总编剧强制重排 (现读最新提示词), 覆盖现总纲+灵魂三问 (自动备份); 逐集稿需另点级联重跑">🔁 重跑总纲</button>
        <button class="sm secondary" onclick="window.open('/web/style_board.html?book_id=${b.id}', '_blank')" title="风格选型 (0922): ep1 钩子段切 3 试镜镜 → 风格库轮询 + Kimi 新配方 → 选择板点选落绑定; 动画开工硬挡板要求先绑定">🎨 风格选型</button>
        <button class="sm secondary" onclick="window.open('/web/style_bible.html?book_id=${b.id}', '_blank')" title="艺术圣经 (0922): 大纲→风格选型→圣经独立页; 选定配方后为本书产统一视觉身份 (色板/质感/角色策略), 分镜每镜必继承">📖 艺术圣经</button>
        <button class="sm secondary" onclick="window.open('/web/style_characters.html?book_id=${b.id}', '_blank')" title="角色设计台 (0923): 圣经角色卡 → 定妆照沉淀 (书级风格×look全身立绘), _资产/角色设计/, 全书人物锚">👥 人物造型</button>
        ${roadmap_ok ? `<button class="sm" style="background:#16a34a" onclick="saveRoadmap()">💾 保存修改</button>` : ''}
        <button class="sm warn" onclick="run('confirm-roadmap', this)" ${roadmap_ok ? '' : 'disabled title="需先生成总纲（蒸馏后自动）"'}>确认2 → 进入逐集</button>
      </div>
    </div>

    <div class="panel">
      <h2>步骤4 · 逐集稿 <span class="hint">${eps.filter(e=>e.status==='confirmed').length}/${eps.length} 集已确认</span></h2>
      ${eps.length ? `<div class="toolbar" style="margin-bottom:0.8rem">
        <div class="form-row">
          <div style="flex:0 0 220px;">
            <label>数字人（人物）</label>
            <select id="produce-persona" style="width:100%;padding:0.4rem 0.6rem;font-size:0.82rem;"><option value="">加载中…</option></select>
          </div>
          <span class="persona-lock" id="produce-persona-lock"></span>
        </div>
        <button class="sm" style="background:#16a34a" onclick="generateRemaining(this)" ${any_pending ? '' : 'disabled title="没有待生成的集"'}>⏭ 一键生成剩余集</button>
        <button class="sm warn" onclick="confirmRemaining(this)" ${any_draft ? '' : 'disabled title="没有待确认的集"'}>✔ 一键确认剩余</button>
      </div>` : ''}
      ${eps.length ? epRows : '<div class="hint">先生成总纲</div>'}
    </div>

    <div class="panel">
      <h2>发布 · 挂车清单</h2>
      <div class="toolbar" style="margin-bottom:0"><button class="sm secondary" onclick="showChecklist()">查看挂车清单</button></div>
      <pre class="script" id="checklist" style="display:none"></pre>
    </div>`;

  if (eps.length) loadProducePersonas(inp.persona_id);
}

// 2026-08-22: 总纲自动编排 — 从蒸馏 facing-units 生成 (免手动"生成总纲")
async function syncStoryL0() {
  if (!BOOKS.cur) return;
  try {
    const r = await api('/books/' + BOOKS.cur.id + '/auto-fill', { method: 'POST' });
    toast((r.auto_filled || []).join('、') || '已同步（无新增）', 'success');
    await loadBook(BOOKS.cur.id);
  } catch (e) { toast('同步失败: ' + e.message, 'error'); }
}

// ── 钩子三版 (0913 机制⑤⑦): 多版×KPI分化 + 人工选版 ──
async function hookVariants(n) {
  const e = BOOKS.cur.episodes.find(x => x.ep === n);
  if (!e || !e.script_text) { toast('该集无稿 (需 script_text, 未进产线的集先生成)', 'info'); return; }
  if (_busy) { toast('有操作进行中', 'info'); return; }
  _busy = true;
  toast('钩子三版生成中… (约30-60秒)', 'info');
  try {
    const r = await api(`/books/${BOOKS.cur.id}/episodes/${n}/hook-variants`, { method: 'POST' });
    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:999;display:flex;align-items:center;justify-content:center';
    const card = document.createElement('div');
    card.style.cssText = 'background:#1e293b;border:1px solid #475569;border-radius:10px;padding:1.2rem;max-width:640px;max-height:80vh;overflow:auto';
    let html = `<h3 style="margin:0 0 .3rem">🎰 钩子三版 (每版≤${r.cap}字, 点击选用)</h3>
      <div class="hint">换掉现稿钩子段 (断言→锤→认领→悬念), 其余段不动</div>`;
    r.variants.forEach((v, i) => {
      html += `<div onclick="adoptHook(${n}, ${i})" data-vi="${i}" style="margin:.7rem 0;padding:.7rem .9rem;border:1px solid #64748b;border-radius:8px;cursor:pointer">
        <b style="color:#C9A25E">${esc(v.kpi || '版' + (i + 1))}</b> <span class="hint">${esc(v.axis || '')}</span>
        <pre class="script" style="margin:.4rem 0 0;white-space:pre-wrap">${esc(v.text || '')}</pre></div>`;
    });
    html += `<button class="sm secondary" onclick="this.closest('div[id]').parentNode.remove();event.stopPropagation()" style="margin-top:.4rem">都不选, 关闭</button>`;
    card.innerHTML = html;
    ov.id = 'hv-overlay';
    ov.appendChild(card);
    ov.addEventListener('click', ev => { if (ev.target === ov) ov.remove(); });
    document.body.appendChild(ov);
    window._hvVariants = r.variants;
  } catch (err) {
    toast('钩子三版失败: ' + err.message, 'error');
  } finally { _busy = false; }
}

async function adoptHook(n, i) {
  const v = (window._hvVariants || [])[i];
  const e = BOOKS.cur.episodes.find(x => x.ep === n);
  if (!v || !e || !e.script_text) return;
  // 替换钩子段: 首标签行保留, 内容换到下一个标签前
  const t = e.script_text;
  const m1 = t.match(/^【[^】]*】\s*\n/);
  const i2 = t.indexOf('\n【', m1 ? m1[0].length : 0);
  const head = m1 ? m1[0] : '';
  const rest = i2 >= 0 ? t.slice(i2 + 1) : '';
  const nt = head + (v.text || '').trim() + '\n' + rest;
  if (!confirm(`采用该版钩子?\n\n${(v.text || '').slice(0, 60)}…`)) return;
  try {
    await api(`/books/${BOOKS.cur.id}/episodes/${n}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script_text: nt }),
    });
    document.getElementById('hv-overlay')?.remove();
    await loadBook(BOOKS.cur.id);
    toast('钩子已换 (其余段不动)', 'success');
  } catch (err) { toast('换钩失败: ' + err.message, 'error'); }
}

// ── 重跑总纲 (0912): 系列总编剧强制重排, 每次现读最新提示词; 覆盖前自动备份 ──
async function rerunOutline(btn) {
  if (!BOOKS.cur) return;
  const hadScript = (BOOKS.cur.episodes || []).some(e => e.script_text);
  const msg = '⚠️ 重跑总纲：用最新提示词强制重排系列总纲+灵魂三问（现总纲自动备份，可回滚）。'
    + (hadScript ? '逐集稿不会自动重做 — 重排后建议对受影响集「级联重跑」。\n\n确认继续？' : '\n\n确认继续？');
  if (!confirm(msg)) return;
  await run('rerun-outline', btn);
}

// ── 樊登全书稿 (0912 樊登前置架构): 蒸馏→一遍成型→总编剧切六集 ──
async function genFandengFull(btn) {
  if (!BOOKS.cur || _busy) { toast('有操作进行中，请等待', 'info'); return; }
  const has = BOOKS.cur.fandeng_full && BOOKS.cur.fandeng_full.exists;
  const msg = has
    ? `樊登全书稿已有 (${BOOKS.cur.fandeng_full.chars}字)。\n重产将覆盖现稿 (旧稿可在 data/l0 目录找回), 之后需「🔁重跑总纲」重切各集。确认重产？`
    : '生成樊登全书稿？\n蒸馏材料一遍成型全书讲述 (分段续写, 约2-5分钟)。\n完成后点「🔁重跑总纲」— 总编剧会按稿切系列, 逐集拐棍=切片。';
  if (!confirm(msg)) return;
  _busy = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 讲述中…'; }
  try {
    const r = await api(`/books/${BOOKS.cur.id}/fandeng-full`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ force: has }),
    });
    await loadBook(BOOKS.cur.id);
    toast(`樊登全书稿完成: ${r.chars}字 / ${r.segments || '?'}段`, 'success');
    toast('下一步: 🔁重跑总纲 (总编剧按稿切系列)', 'info');
  } catch (e) {
    toast('樊登全书稿失败: ' + e.message, 'error');
  } finally {
    _busy = false;
    await loadBook(BOOKS.cur.id);
  }
}

// ── 人设下拉 (复用 writing.js loadHosts + checkPersonaLock 模式, 2026-08-20) ──
async function loadProducePersonas(savedId) {
  const select = document.getElementById('produce-persona');
  if (!select) return;
  try {
    // personas 端点在 /api 前缀下 (其余 books 端点无前缀), 直接 fetch 完整路径
    const resp = await fetch('/api/personas');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const personas = await resp.json();
    select.innerHTML = '';
    if (!personas.length) {
      select.innerHTML = '<option value="">无人物（先去「人物」页创建）</option>';
      return;
    }
    let defaultSelected = false;
    // 预选: 本书记住的 persona_id → 静读书 (brand=静读书) → 首个有品牌
    const bookPersona = savedId;
    let jingshuBook = null;
    personas.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.brand_name ? `${p.name} · ${p.brand_name}` : `${p.name} · ${p.prompt_template}`;
      if (p.id === bookPersona) { opt.selected = true; defaultSelected = true; }
      if (p.brand_name === '静读书') jingshuBook = opt;
      select.appendChild(opt);
    });
    if (!defaultSelected && jingshuBook) { jingshuBook.selected = true; defaultSelected = true; }
    if (!defaultSelected && personas.length) select.selectedIndex = 0;
    // 选人设 → 显示锁定信息 (音色/形象), 复用 checkPersonaLock 展示逻辑
    select.addEventListener('change', showProducePersonaLock);
    showProducePersonaLock();
  } catch (e) {
    console.error('加载人物失败', e);
    select.innerHTML = '<option value="">人物加载失败</option>';
  }
  applyPersonaGating();  // 0908 双产线分流 (gating 不依赖下拉选择, 依赖书 persona_id)
}

// 展示所选人设的锁定信息 (音色/形象), 与 writing.checkPersonaLock 同款
let _producePersonasCache = [];
async function showProducePersonaLock() {
  const lock = document.getElementById('produce-persona-lock');
  const select = document.getElementById('produce-persona');
  if (!lock || !select) return;
  try {
    if (!_producePersonasCache.length) {
      const resp = await fetch('/api/personas');
      if (resp.ok) _producePersonasCache = await resp.json();
    }
    const p = _producePersonasCache.find(x => x.id === select.value);
    if (!p) { lock.style.display = 'none'; lock.textContent = ''; return; }
    const voiceName = p.voice ? p.voice.name : (p.voice_id ? '已绑定' : '未绑定');
    const roleName = p.role ? p.role.name : '未绑定';
    lock.textContent = `🔒 人物「${p.name}」— 音色: ${voiceName}（音频加工页锁定）| 形象: ${roleName}`;
    lock.style.display = '';
  } catch (e) {
    lock.style.display = 'none'; lock.textContent = '';
  }
}

// ── 双产线 gating (0908): 按书的 persona 显隐按钮 — 老谭线只留页单工坊 ──
async function applyPersonaGating() {
  const inp = (BOOKS.cur && BOOKS.cur.input_json) || {};
  try {
    if (!_producePersonasCache.length) {
      const r = await fetch('/api/personas');
      if (r.ok) _producePersonasCache = await r.json();
    }
    const p = _producePersonasCache.find(x => x.id === inp.persona_id);
    BOOK_LINE = (p && p.prompt_template || '').includes('laotan') ? 'laotan' : 'jingshu';
  } catch (e) { BOOK_LINE = null; }
  const laotan = BOOK_LINE === 'laotan';
  // 0910 用户令: 老谭线双线都显示 (页单工坊=主力全内产, 进产线=PPT 链备选);
  // 静读书线维持只显示进产线 (bs1 族为老谭专用)
  document.querySelectorAll('.ep-prod-btn').forEach(el => { el.style.display = ''; });
  document.querySelectorAll('.ep-bs1-btn').forEach(el => { el.style.display = laotan ? '' : 'none'; });
}

// ── 总纲内联编辑: 保存 → PUT /books/{id}/roadmap ──
async function saveRoadmap() {
  if (_busy) { toast('有操作进行中，请等待', 'info'); return; }
  const rows = [];
  const hadScript = (BOOKS.cur.episodes || []).some(e => e.script_text);
  document.querySelectorAll('#roadmap-table tr[data-row]').length;  // no-op guard
  const trs = document.querySelectorAll('#roadmap-table tr');
  for (let i = 1; i < trs.length; i++) {  // skip header
    const cells = trs[i].querySelectorAll('td');
    const ep = parseInt(cells[0].textContent.trim(), 10);
    const get = field => {
      const cell = trs[i].querySelector(`td[data-field="${field}"]`);
      return cell ? cell.textContent.trim() : '';
    };
    const concepts = get('概念').split(/[、;；\n]+/).map(s => s.trim()).filter(Boolean);
    rows.push({
      ep, 主题: get('主题'), 本集秘籍: get('本集秘籍'), 对应书中内容: get('对应书中内容'),
      核心任务: get('核心任务'), 卖点: get('卖点'), 承上: get('承上'), 启下: get('启下'), 概念: concepts,
    });
  }
  if (!rows.length) { toast('没有可保存的总纲', 'info'); return; }
  _busy = true;
  try {
    await api(`/books/${BOOKS.cur.id}/roadmap`, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ episodes: rows }),
    });
    await loadBook(BOOKS.cur.id);
    toast('总纲已保存', 'success');
    if (hadScript) toast('已改总纲，建议对受影响集级联重跑', 'info');
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  } finally { _busy = false; }
}

// ── 批量 ──
async function generateRemaining(btn) {
  // 0912 修: 只生成 pending/generating (原 status!=='confirmed' 会把 draft 已有稿静默覆盖重生成);
  // draft 集要重做走单集"生成(pro)"或"级联重跑"
  // 0912 用户悟 / 0914 结构参数化: 生成顺序 工具集 → ep1(大钩子) → 末集(行动册) — 头尾吃工具集的真实内容,
  // ep1 先生成只能对着 roadmap 凭空许诺
  const total = (BOOKS.cur.episodes || []).length;
  const pending = (BOOKS.cur.episodes || [])
    .filter(e => e.status === 'pending' || e.status === 'generating')
    .sort((a, b) => {
      // 工具集 (2..N-1) 按序 → ep1 大钩子 → 末集行动册 (头尾吃工具集实产全稿)
      const w = e => (e.ep >= 2 && e.ep < total) ? e.ep : (e.ep === 1 ? total + 0.5 : total + 1);
      return w(a) - w(b);
    });
  if (!pending.length) { toast('没有待生成的集', 'info'); return; }
  const epList = pending.map(e => e.ep).join('、');
  if (!confirm(`将依次生成第 ${epList} 集（共 ${pending.length} 集，每集约2-5分钟，请保持页面打开）`)) return;
  _busy = true;
  if (btn) { btn.disabled = true; btn.textContent = `⏳ 0/${pending.length}`; }
  let done = 0, curEp = 0;
  try {
    for (const e of pending) {
      curEp = e.ep;
      toast(`生成第 ${e.ep} 集… (${done+1}/${pending.length})`, 'info');
      await api(`/books/${BOOKS.cur.id}/episodes/${e.ep}/generate`, { method: 'POST' });
      done++;
      await loadBook(BOOKS.cur.id);
      const nb = document.querySelector('[onclick^="generateRemaining"]');
      if (nb) { nb.textContent = `⏳ ${done}/${pending.length}`; nb.disabled = true; }
    }
    toast(`全部生成完成 (${done} 集)，请逐集确认`, 'success');
  } catch (err) {
    toast(`第${curEp}集生成失败: ${err.message}`, 'error');
  } finally {
    _busy = false;
    await loadBook(BOOKS.cur.id);
  }
}

async function confirmRemaining(btn) {
  const drafts = (BOOKS.cur.episodes || []).filter(e => e.status === 'draft');
  if (!drafts.length) { toast('没有待确认的集', 'info'); return; }
  if (!confirm(`确认所有 ${drafts.length} 集草稿？确认后进入定稿，后续修改需重跑。`)) return;
  _busy = true;
  if (btn) btn.disabled = true;
  try {
    for (const e of drafts) await api(`/books/${BOOKS.cur.id}/episodes/${e.ep}/confirm`, { method: 'POST' });
    await loadBook(BOOKS.cur.id);
    toast(`已确认 ${drafts.length} 集`, 'success');
  } catch (e) { toast('确认失败: ' + e.message, 'error'); }
  finally { _busy = false; }
}

// ── 破坏性操作防护 ──
async function rerunEp(n) {
  const affected = (BOOKS.cur.episodes || []).filter(e => e.ep >= n).length;
  if (!confirm(`⚠️ 级联重跑第 ${n} 集起：将清空第 ${n}~${n+affected-1} 集（共${affected}集）的稿件并重新生成，不可恢复！\n\n确认继续？`)) return;
  await run(`episodes/${n}/rerun`);
}

// ── 修稿 modal ──
function openEdit(n) {
  EDIT_EP = n;
  const e = BOOKS.cur.episodes.find(x => x.ep === n);
  $('#e-script').value = e.script_text || '';
  $('#e-ep-title').value = e.title || '';
  $('#e-duration').value = e.target_duration_sec || 600;
  $('#e-title').textContent = `第${n}集 修稿`;
  $('#edit-modal').classList.add('active');
}

async function saveEdit() {
  const body = { script_text: $('#e-script').value };
  const t = $('#e-ep-title').value.trim(); if (t) body.title = t;
  const d = parseFloat($('#e-duration').value); if (d && d >= 60) body.target_duration_sec = d;
  await api(`/books/${BOOKS.cur.id}/episodes/${EDIT_EP}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  closeModal('edit-modal');
  await loadBook(BOOKS.cur.id);
  toast('已保存', 'success');
}

// ── 挂车清单 ──
async function showChecklist() {
  const r = await api(`/books/${BOOKS.cur.id}/cart-checklist`);
  $('#checklist').style.display = ''; $('#checklist').textContent = r.checklist;
}

// ── 进产线 + TTS + 导演 (audio 连通) ──
async function produceEp(n) {
  if (BOOK_LINE === 'laotan') {  // 0908 防御: 老谭线走 bs1 工坊, 不 produce
    location.href = `/web/bs1_story.html?book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${n}`;
    return;
  }
  const e = BOOKS.cur.episodes.find(x => x.ep === n);
  if (!confirm(`第${n}集 确认进产线? 生成 Script+解析段`)) return;
  const r = await api(`/books/${BOOKS.cur.id}/episodes/${n}/produce`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ persona_id: currentPersonaId() }),
  });
  await loadBook(BOOKS.cur.id);
  toast(`进产线成功: script_id=${r.script_id}`, 'success');
  // 进产线自动跳音频页 (audio 连通, 带 book/ep 供 PPT 系列皮肤对齐)
  setTimeout(() => location.href = `/web/audio.html?script_id=${r.script_id}&book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${n}`, 1200);
}

// ── TTS 先行 (0912 用户令 → 0917 改令×2): 稿→[人工勾选→手动生成]→人耳验收 → 确认3 → 进产线/页单工坊.
// 复用音频加工页人工听检 (与新闻线无异, 音色=persona 链自动落 2.5 说书).
// 0917 用户令: ① 不自动生成 — 勾选/生成全在音频加工页人工; ② 每次点击都按当前稿件
// 全新重建 (force: 删旧 Script 级联 segments/audio_jobs/audio_files — 旧勾选/旧时长/
// 重排空槽/回听记录全清, 磁盘 wav 留底) — "不管改没改稿都当全新任务, 勾选不沿用".
async function ttsPreview(n) {
  const e = BOOKS.cur.episodes.find(x => x.ep === n);
  if (!e || !e.script_text) { toast('先生成稿', 'info'); return; }
  const draft = e.status !== 'confirmed';
  if (!confirm(`第${n}集 全新准备音频工位？\n${draft ? '当前是草稿 — 按当前稿重建段落。' : '已确认稿。'}\n旧勾选/旧音频记录将清空，跳转后自行勾选段落并点击「生成音频」。`)) return;
  try {
    // produce(preview+force): 删旧 Script (级联 segments/audio_jobs/audio_files) + 按当前稿重建
    const r = await api(`/books/${BOOKS.cur.id}/episodes/${n}/produce`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ persona_id: currentPersonaId(), preview: true, force: true }),
    });
    const sid = r.script_id;
    setTimeout(() => location.href =
      `/web/audio.html?script_id=${sid}&book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${n}`, 400);
  } catch (err) { toast('进入音频加工页失败: ' + err.message, 'error'); }
}

// 重跑进产线 (2026-08-20): 删旧 Script/segments 重建 → 跳 audio 页 (可换 PPT 产线)
async function rerunProduce(n) {
  const e = BOOKS.cur.episodes.find(x => x.ep === n);
  if (!confirm(`⚠️ 重跑第 ${n} 集进产线？\n\n将删除该集已有的 Script/解析段 (旧 TTS/导演任务保留在磁盘)，重新生成新的进产线产物，然后跳转音频页。\n\n用于发现错误重做，或切换新产线 (如 PPT 出片)。`)) return;
  try {
    const r = await api(`/books/${BOOKS.cur.id}/episodes/${n}/produce`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ persona_id: currentPersonaId(), force: true }),
    });
    await loadBook(BOOKS.cur.id);
    toast(`重跑成功: 新 script_id=${r.script_id}`, 'success');
    // 跳 audio 页 — 可选 PPT 出片或走现有产线 (带 book/ep 供皮肤对齐)
    setTimeout(() => location.href = `/web/audio.html?script_id=${r.script_id}&book_id=${encodeURIComponent(BOOKS.cur.id)}&ep_index=${n}`, 1200);
  } catch (err) {
    toast('重跑失败: ' + err.message, 'error');
  }
}

// 修复 TTS bug: 路径 /api/audio/scripts/{sid}/generate-audio (audio 路由挂 /api/audio 前缀,
// 旧 '/audio/...' 一直 404) + 字段 r.id
async function ttsEp(sid) {
  const r = await api(`/api/audio/scripts/${sid}/generate-audio?selected_only=true`, { method: 'POST' });
  toast(`TTS 已派发: job=${r.id}，请到 音频页 查看`, 'success');
}

function directEp(sid) {
  window.open(`/web/director.html?script_id=${sid}`, '_blank');
}

document.addEventListener('DOMContentLoaded', async () => {
  const id = new URLSearchParams(location.search).get('book_id');
  if (id) await loadBook(id);
  else $('#page-content').innerHTML = '<div class="hint">未指定书，从 <a href="/web/books.html">书库</a> 选择</div>';
});
