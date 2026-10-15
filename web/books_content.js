// 内容库页 (2026-08-20) — 输入补全 + 评论层 + 素材
// 依赖 books.js 共享 (api/esc/run/loadBook/toast). 渲染入口: renderPage(cur).

function renderPage(b) {
  const inp = b.input_json || {};
  const field = k => (inp[k] || {}).value || '';
  const tier = k => (inp[k] || {}).tier || '-';
  const needs = inp.needs_supplement || [];
  const prog = b.progress || { done: [false,false,false,false,false], current: 1 };
  const s1_ok = prog.done[0];         // 输入补全完成
  // 1012: 「→ 进讲书」门控链接已被书级流程导航 (.flownav) 取代 — 直达总纲页

  $('#page-content').innerHTML = `
    <div id="l0-panel" class="panel" style="display:none"></div>
    <div class="panel">
      <h2>📚 书皮 <span class="badge distilled">开场素材</span>
        <button class="sm secondary" style="float:right" onclick="reextractCover(this)">🔄 重新提取</button></h2>
      ${(() => { const cov = b.cover || {};
        if (!cov.ok) return '<div class="kv red"><span class="k">未提取到</span>书库无 epub 或提取失败 — 补 epub 后点「重新提取」</div>';
        return `<div style="display:flex;gap:1.2rem;align-items:flex-start;margin-top:0.4rem">
          <img src="/books/${b.id}/cover?v=${Date.now()}" alt="书皮"
               style="width:120px;border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,.45);flex-shrink:0">
          <div>
            <div class="kv"><span class="k">状态</span><span class="badge distilled">已提取</span> 开场前 5 秒素材 (书墙+主书封)</div>
            <div class="hint" style="word-break:break-all;margin-top:0.3rem">落盘: ${esc(cov.path || '-')}</div>
            <div class="hint" style="margin-top:0.3rem">提错了可手工替换同名文件 (开场产线吃同一文件), 刷新页面即见。</div>
          </div>
        </div>`; })()}
      <div class="hint" style="margin-top:0.5rem">建书时自动从书库 epub 提取封面 (OPF 声明优先), 此处提前核对 — 不用等到视觉渲染才发现提错。</div>
    </div>

    <div class="panel">
      <h2>输入补全 <span class="badge distilled">蒸馏自动填充</span>
        <button class="sm secondary" style="float:right" onclick="syncL0()">🔄 同步蒸馏</button></h2>
      <div class="kv"><span class="k">类型</span>${esc(field('书籍类型') || '-')}</div>
      <div class="kv"><span class="k">核心主张</span>${esc(field('全书核心主张') || '-')}</div>
      <div class="kv"><span class="k">概念</span>${esc((field('关键概念清单') || []).join(' / '))}</div>
      <div class="kv"><span class="k">金句</span>${esc((field('核心金句') || []).slice(0,2).join(' / '))}</div>
      <div class="kv"><span class="k">目标读者</span>${esc(field('目标读者画像') || '-')} ${(inp['目标读者画像']||{}).tier === 'persona' ? '<span class="badge">来自人设·兜底</span>' : (field('目标读者画像') ? '<span class="badge distilled">来自书内容</span>' : '')}</div>
      ${(() => { const am = (inp['受众地图']||{}).value; if (!am || !am.L1) return '';
        const pool = arr => (arr || []).map(p =>
          `<span style="display:inline-block;margin-right:0.6rem"><b style="color:#c9a25e">${esc(p.group||'')}</b><span style="color:#8aa0b8"> — ${esc(p.bridge||'')}</span></span>`).join('')
          || '<span style="color:#64748b">书撑不起此轴</span>';
        return `
      <div class="kv"><span class="k">闲话池·上下游</span><span class="badge">价值链同构</span> ${pool(am['上下游'])}</div>
      <div class="kv"><span class="k">闲话池·上下层</span><span class="badge">组织同构</span> ${pool(am['上下层'])}</div>
      <div class="hint" style="margin:0.2rem 0 0.4rem">受众两层 (0914): 主线"你"=上方目标读者, 全篇不换人; 闲话层在迁移落地处按轴搭桥点名 (总纲从池中分配, 桥=机制同构)。</div>`; })()}
      ${needs.length ? `<div class="kv red"><span class="k">待补</span>${needs.join('、')}</div>` : ''}
      ${(() => { const r = (b.input_json || {}).risk_assessment; if (!r) return '';
        const icon = { green: '🟢', yellow: '🟡', red: '🔴' }[r.tier] || '❓';
        return `<div class="kv" style="margin-top:0.5rem">${icon} <b>${r.tier}</b>${r.abandon ? ' <span class="red">建议放弃</span>' : ''}
          ｜雷区: ${esc((r.risk_points || []).join(' / ')) || '-'}
          ｜安全操作: ${esc((r.min_safe_ops || []).slice(0, 3).join(' / ')) || '-'}</div>`; })()}
      <div class="hint" style="margin-top:0.5rem">以上由 L0 蒸馏 + facing 自动产出（内核/概念/金句/合规），无需手动补全/预评估。点【同步蒸馏】重跑 facing 后刷新。</div>
    </div>

    <div class="panel">
      <h2>评论层 + 素材</h2>
      <div class="kv"><span class="k">评论层</span>${(inp.comment_layer || []).length} 条读者反应 <span class="badge distilled">蒸馏自动</span></div>
      <div class="kv"><span class="k">素材</span>${(inp.materials || []).length} 条${(inp.materials || []).length ? '' : '（书外素材搜索待接入）'}</div>
      ${(inp.comment_layer || []).length ? `
        <details style="margin-top:0.7rem"><summary style="cursor:pointer;font-size:0.85rem;color:#93c5fd">查看评论层内容</summary>
        <div style="margin-top:0.5rem">${(inp.comment_layer || []).map(c =>
          `<div class="kv" style="font-size:0.82rem">[${esc(c.type || '')}${c.unit_id ? ' · ' + esc(c.unit_id) : ''}] ${esc(c.comment || '')}</div>`).join('')}</div></details>` : ''}
    </div>

    <div class="panel" id="source-panel">
      <h2>📖 源头书单 <span class="badge distilled">自动</span></h2>
      <div class="hint">本书引用的源头书/理论 —— 可作粉丝书单、内容关联（"作者还在《XX》里讲过…"，涨粉/复购）。</div>
      <div id="source-body" class="hint" style="margin-top:0.5rem">加载中…</div>
    </div>`;
}

// 2026-08-22: 蒸馏自动填充 — 触发 /auto-fill (幂等), 同步 facing → input_json/评论层/总纲
async function syncL0() {
  const btn = document.querySelector('#l0-panel [onclick*="syncL0"]') || document.createElement('span');
  try {
    const r = await api('/books/' + BOOKS.cur.id + '/auto-fill', { method: 'POST' });
    toast((r.auto_filled || []).join('、') || '已同步（无新增）', 'success');
    await loadBook(BOOKS.cur.id);
    loadL0(BOOKS.cur.id);
  } catch (e) { toast('同步失败: ' + e.message, 'error'); }
}

// 重提书皮 (0925): 删缓存 00_本书_*.jpg 重跑 — epub 晚到/提错重试
async function reextractCover(btn) {
  btn.disabled = true;
  try {
    const r = await api('/books/' + BOOKS.cur.id + '/cover/reextract', { method: 'POST' });
    toast(r.ok ? '书皮已重新提取' : '仍未提取到 (书库无 epub?)', r.ok ? 'success' : 'error');
    await loadBook(BOOKS.cur.id);
  } catch (e) { toast('重提失败: ' + e.message, 'error'); }
  finally { btn.disabled = false; }
}

// 源头书单 (2026-08-23): 本书引用的源头书/理论 → 粉丝书单/内容关联
async function loadSources(id) {
  const el = $('#source-panel');
  if (!el) return;
  let data;
  try { data = await api('/books/' + id + '/source-list'); } catch (_) { return; }
  const srcs = data.sources || [];
  if (!srcs.length) { el.style.display = 'none'; return; }
  el.style.display = '';
  $('#source-body').innerHTML = srcs.map(s => {
    const okAuthor = s.author && !['未知', '未提及', '无', '不详'].includes(s.author);
    return `<div class="kv" style="font-size:0.82rem"><span class="k">${s.type === 'book' ? '📕' : '🔹'}</span>《${esc(s.name)}》` +
      (okAuthor ? ' · ' + esc(s.author) : '') +
      ` <span class="hint">第${(s.chapters || []).join('、')}章</span></div>`;
  }).join('');
}

// ── L0 蒸馏 + 第二层 facing 面板 (2026-08-22 UI 融合) ─────────────
async function loadL0(id) {
  const el = document.getElementById('l0-panel');
  if (!el) return;
  let data;
  try { data = await api('/books/' + id + '/l0'); }
  catch (_) { return; }
  if (!data.distilled) {
    el.style.display = '';
    el.innerHTML = '<h2>🧠 L0 分析 <span class="badge need">未蒸馏</span></h2>' +
      '<div class="hint">该书尚未跑 L0 蒸馏（元信息/排版强调/概念/金句/引用源头提取）。' +
      '命令行：<code>python -m app.services.book_service.l0 &lt;书路径&gt;</code></div>';
    return;
  }
  const f = data.facing || {};
  const tierIcon = { green: '🟢', yellow: '🟡', red: '🔴' };
  const m = data.meta || {};
  let h = `<h2>🧠 L0 分析 <span class="badge distilled">已蒸馏</span></h2>`;
  h += `<div class="kv" style="font-size:0.82rem"><span class="k">来源</span>${esc(m.author || '-')} · ${esc(m.publisher || '-')} · ${esc(m.pub_date || '-')} · ${data.chapters} 章</div>`;
  if (f.kernel) {
    h += `<div class="kv"><span class="k">内核</span>${esc(f.kernel.one_liner || '-')}</div>`;
  }
  if (f.units && f.units.units) {
    h += `<div class="kv"><span class="k">建议集数</span><b>${f.units.units.length} 集</b>（按内容自适应）· 单元：${f.units.units.map(u => esc(u.title)).join(' / ').slice(0, 80)}</div>`;
  }
  if (f.quotes && f.quotes.quotes) {
    const g = f.quotes.quotes.filter(q => q.type === '格言型').length;
    const s = f.quotes.quotes.length - g;
    h += `<div class="kv"><span class="k">金句</span>${f.quotes.quotes.length} 条（格言 ${g} / 情境 ${s}）</div>`;
  }
  if (f.compliance) {
    h += `<div class="kv"><span class="k">合规</span>${tierIcon[f.compliance.book_tier] || '❓'} ${esc(f.compliance.book_tier || '-')}` +
      (f.compliance.safe_angles && f.compliance.safe_angles.length ? ` ｜安全角度：${esc(f.compliance.safe_angles.slice(0, 2).join(' / '))}` : '') + `</div>`;
  }
  if (f.readers && f.readers.readers) {
    h += `<div class="kv"><span class="k">读者痛点</span>${f.readers.readers.length} 条</div>`;
  }
  const missing = Object.keys({ kernel: 1, units: 1, quotes: 1, compliance: 1, readers: 1 })
    .filter(k => !f[k]);
  if (missing.length) h += `<div class="hint">待跑面向：${missing.join(' / ')}（python -m app.services.book_service.facing）</div>`;
  el.style.display = '';
  el.innerHTML = h;
}

// 建书后台准备轮询 (2026-08-23): L0/facing 未就绪时后台跑, 这里轮询到 ready 后刷新
async function pollPrep(id) {
  let st;
  try { st = await api('/books/' + id + '/prep'); } catch (_) { return; }
  if (!st || st.status !== 'preparing') return;
  toast('蒸馏准备中: ' + (st.note || st.step || 'L0/facing…'), 'info');
  const t = setInterval(async () => {
    let s;
    try { s = await api('/books/' + id + '/prep'); } catch (_) { return; }
    if (s.status === 'ready') {
      clearInterval(t);
      toast('蒸馏完成 ✓', 'success');
      await loadBook(id); loadL0(id); loadSources(id);
    } else if (s.status === 'error') {
      clearInterval(t);
      toast('蒸馏失败: ' + (s.note || ''), 'error');
    }
  }, 5000);
}

document.addEventListener('DOMContentLoaded', async () => {
  const id = new URLSearchParams(location.search).get('book_id');
  if (id) { await loadBook(id); loadL0(id); loadSources(id); pollPrep(id); }
  else $('#page-content').innerHTML = '<div class="hint">未指定书，从 <a href="/web/books.html">书库</a> 选择</div>';
});
