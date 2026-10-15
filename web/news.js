// news.js (2026-08-15) — 新闻线索页: 文章输入 + 素材包(多源聚合/七层审计/定向补搜)
// 依赖 app.js v7 共享层 (api/setStatus/toggle/toast/currentArticle/currentPackageId)

// ── 文章输入 (从 app.js v6 迁入) ──
// 证据图管线① (2026-09-04): 抓取时「📷 搜图」开关 → with_images 提取本页候选图,
// 暂存于此 → 建稿时存 articles.images_json (非空 = 搜图总闸开, 建包自动扫图)。
let fetchedImages = [];

// 搜图勾点掉即作废已暂存的候选图 (2026-09-06 修): 勾只在点「抓取」瞬间被读,
// 抓取后再点掉勾不清空 fetchedImages → 建稿照样带图落库 → 总闸形同虚设 (job 27ba3371 实锤)
document.addEventListener('DOMContentLoaded', () => {
  const imgToggle = document.getElementById('fetch-images');
  if (imgToggle) {
    imgToggle.addEventListener('change', () => {
      if (!imgToggle.checked && fetchedImages.length) {
        fetchedImages = [];
        setStatus('status-create', '已作废本次抓取的候选图（搜图已关闭）', false, true);
      }
    });
  }
});

async function fetchUrl() {
  const urlInput = document.getElementById('article-url');
  const url = urlInput.value.trim();
  if (!url) {
    setStatus('status-create', '请先输入 URL', true);
    return;
  }
  const btn = document.getElementById('btn-fetch-url');
  btn.disabled = true;
  setStatus('status-create', '正在抓取...');
  const imgToggle = document.getElementById('fetch-images');
  const withImages = imgToggle ? imgToggle.checked : false;
  try {
    const result = await api('POST', '/articles/fetch-url', { url, with_images: withImages });
    if (!result.ok) {
      setStatus('status-create', result.error || '抓取失败', true);
      return;
    }
    if (result.title) document.getElementById('article-title').value = result.title;
    if (result.raw_text) document.getElementById('article-text').value = result.raw_text;
    fetchedImages = withImages ? (result.images || []) : [];
    const imgNote = fetchedImages.length ? `（抓到 ${fetchedImages.length} 张图，建包时自动扫证据图）` : '';
    setStatus('status-create', `已自动填入标题和正文${imgNote}`, false, true);
  } catch (e) {
    setStatus('status-create', e.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function createArticle() {
  const title = document.getElementById('article-title').value.trim();
  const sourceUrl = document.getElementById('article-url').value.trim();
  const rawText = document.getElementById('article-text').value.trim();
  if (!rawText) {
    setStatus('status-create', '请输入原文内容', true);
    return;
  }
  // 赛道 (2026-08-16 用户方案): 建稿勾选 → 评论层解构 + 七层审计走对应分支
  const trackEl = document.querySelector('input[name="article-track"]:checked');
  const track = trackEl ? trackEl.value : 'tech';
  // 证据图管线①: 搜图开关抓到的候选图随稿落库 (非空 = 总闸开)。
  // 建稿再读一次勾状态 (2026-09-06 修): 以用户当下勾为准, 抓取时开着、建稿前点掉 → 不带图
  const body = { title, source_url: sourceUrl, raw_text: rawText, track };
  const imgToggleNow = document.getElementById('fetch-images');
  const useImages = imgToggleNow ? imgToggleNow.checked : false;
  if (useImages && fetchedImages.length) body.images = fetchedImages;
  else fetchedImages = [];
  try {
    currentArticle = await api('POST', '/articles', body);
    fetchedImages = [];  // 已落库, 防重复提交
    setStatus('status-create', `稿件已创建: ${currentArticle.id}（${track === 'geo' ? '地缘/国际' : '科技/商业'}赛道）`, false, true);
    enablePackageUI();
    renderComments(null);  // 新稿无评论层
    // 新稿件必须清空旧包状态 (2026-08-16 bug: currentPackageId 残留上一篇的包,
    // 导致"去洗稿"带旧 package_id → writing 页报"素材包不存在或不属于该稿件")
    currentPackageId = null;
    const sel = document.getElementById('package-select');
    if (sel) sel.innerHTML = '<option value="">— 新建素材包 —</option>';
    const auditBox = document.getElementById('audit-report');
    if (auditBox) auditBox.style.display = 'none';
    const itemsBox = document.getElementById('material-items');
    if (itemsBox) itemsBox.innerHTML = '';
    // 流程修正 (2026-08-16 用户口径): 原稿 → 观众解构(自动) → 七层素材分析。
    // 之前是素材分析先跑、解构靠手动 — 顺序反了。
    await deconstructArticle();
  } catch (e) {
    setStatus('status-create', e.message, true);
  }
}

function enablePackageUI() {
  toggle('btn-new-package', true);
  toggle('btn-material-create', true);
  toggle('btn-material-add', true);
  toggle('btn-deconstruct', true);
}

// ── 评论层 · 观众反应解构 (2026-08-15) ──
const PERSONA_LABELS = {
  tech_explainer: ['🧪 技术科普党', '#93c5fd'],
  national_supporter: ['🔥 国产支持党', '#fbbf24'],
  brand_fan: ['🛡 品牌红粉', '#4ade80'],
  brand_hater: ['⚔ 品牌黑粉', '#f87171'],
  source_tracker: ['🔗 源头党', '#94a3b8'],
};

async function deconstructArticle() {
  if (!currentArticle) {
    setStatus('status-comment', '请先创建稿件', true);
    return;
  }
  toggle('btn-deconstruct', false);
  setStatus('status-comment', '解构观众反应中…（约 20~40s）');
  try {
    const { job_id } = await api('POST', `/articles/${currentArticle.id}/deconstruct`);
    const source = new EventSource(`${API}/jobs/${job_id}/events`);
    source.onmessage = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      if (data.type === 'deconstruct_start') {
        setStatus('status-comment', data.msg || '解构中…');
      } else if (data.type === 'deconstruct_done') {
        source.close();
        setStatus('status-comment', data.msg || '解构完成，自动进入七层素材分析…', false, true);
        toggle('btn-deconstruct', true);
        refreshComments();
        // 流程链 (2026-08-16): 解构完成 → 七层素材分析 (还没包才自动建, 防重复)
        if (currentArticle && !currentPackageId) {
          loadPackages(currentArticle.id);
        }
      } else if (data.type === 'deconstruct_error') {
        source.close();
        setStatus('status-comment', data.error || '解构失败（可手动重试，或直接进行素材分析）', true);
        toggle('btn-deconstruct', true);
        // 解构失败不堵流程: 直接进素材分析
        if (currentArticle && !currentPackageId) {
          loadPackages(currentArticle.id);
        }
      }
    };
    source.onerror = () => {
      source.close();
      setStatus('status-comment', 'SSE 连接错误', true);
      toggle('btn-deconstruct', true);
    };
  } catch (e) {
    setStatus('status-comment', e.message, true);
    toggle('btn-deconstruct', true);
  }
}

async function refreshComments() {
  if (!currentArticle) return;
  try {
    currentArticle = await api('GET', `/articles/${currentArticle.id}`);
    renderComments(currentArticle.deconstruct_json);
  } catch (e) { /* 静默 */ }
}

function renderComments(decon) {
  const box = document.getElementById('comment-view');
  if (!box) return;
  if (!decon || !decon.reactions) {
    box.innerHTML = '<div style="font-size:0.78rem;color:var(--text-muted);">尚无解构数据 — 点上方按钮，或先洗一稿（洗稿自动生成）</div>';
    return;
  }
  let html = '';
  // 1) 用户反应
  if (decon.reactions?.length) {
    html += '<div class="cmt-sec">🧠 观众心里话</div>';
    decon.reactions.forEach(r => {
      html += `<div class="cmt-item"><div class="cmt-text">${escapeHtml(String(r))}</div></div>`;
    });
  }
  // 2) 评论区人设
  if (decon.comment_archetypes?.length) {
    html += '<div class="cmt-sec">🎭 评论区生态（五种典型人设）</div>';
    decon.comment_archetypes.forEach(c => {
      const [label, color] = PERSONA_LABELS[c.type] || [c.type, '#94a3b8'];
      html += `<div class="cmt-item"><span class="cmt-badge" style="color:${color};border-color:${color}44;">${label}</span>
        <div class="cmt-text">${escapeHtml(c.comment || '')}</div>
        ${c.audience_feeling ? `<div class="cmt-feel">观众感觉：${escapeHtml(c.audience_feeling)}</div>` : ''}</div>`;
    });
  }
  // 3) 叙事线
  if (decon.narrative?.length) {
    html += '<div class="cmt-sec">🪜 层层递进叙事线</div>';
    decon.narrative.forEach(n => {
      html += `<div class="cmt-item"><span class="cmt-badge" style="color:#a78bfa;border-color:#a78bfa44;">第${n.layer || '?'}层</span>
        <div class="cmt-text">${escapeHtml(n.content || '')}${n.answers ? ` <span style="color:var(--text-muted);">(解答: ${escapeHtml(n.answers)})</span>` : ''}</div></div>`;
    });
  }
  // 4) 资料清单
  if (decon.research?.length) {
    html += '<div class="cmt-sec">📚 要讲透需要的料</div>';
    html += '<div class="cmt-item"><div class="cmt-text">' + decon.research.map(r => '• ' + escapeHtml(String(r))).join('<br>') + '</div></div>';
  }
  box.innerHTML = html;
}

// ── 素材包 ──
// 七层层名双轨 (2026-08-25): 跟随建稿赛道 — tech=科技七层 / geo=地缘七层(L4/L5/L6 语义不同),
// 与后端 material_service._AUDIT_PROMPT / _AUDIT_PROMPT_GEO 的层定义保持一致。
const LAYER_NAMES_TECH = {
  L1: '极速钩子', L2: '身份+反差', L3: '背景纵深', L4: '硬实力三张牌',
  L5: '实测修罗场', L6: '商业降维打击', L7: '价值观收割',
};
const LAYER_NAMES_GEO = {
  L1: '极速钩子', L2: '身份+反差', L3: '背景纵深', L4: '硬事实牌',
  L5: '一线细节与人物', L6: '横向对照与花边', L7: '价值观收割',
};
function layerNames() {
  return (currentArticle && currentArticle.track === 'geo') ? LAYER_NAMES_GEO : LAYER_NAMES_TECH;
}

/**
 * AI 自动分析: 对当前主稿建包并跑七层覆盖审计 (无需任何人工素材).
 * 建稿后自动触发; 缺口补搜词由审计 LLM 生成.
 */
async function autoAnalyze() {
  if (!currentArticle) return;
  setStatus('status-material', 'AI 正在分析主稿的七层素材覆盖…');
  try {
    const r = await api('POST', '/materials/packages', { article_id: currentArticle.id, urls: [] });
    currentPackageId = r.id;
    subscribePackage(r.job_id);
  } catch (e) {
    setStatus('status-material', e.message, true);
  }
}

async function loadPackages(articleId) {
  const select = document.getElementById('package-select');
  if (!select || !articleId) return;
  try {
    const pkgs = await api('GET', `/materials/packages?article_id=${encodeURIComponent(articleId)}`);
    // 该稿件还没有素材包 → AI 直接自动分析主稿 (无需人工填任何素材)
    if (!pkgs.length) {
      select.innerHTML = '<option value="">— 新建素材包 —</option>';
      await autoAnalyze();
      return;
    }
    select.innerHTML = '<option value="">— 新建素材包 —</option>';
    pkgs.forEach((p, i) => {
      const opt = document.createElement('option');
      opt.value = p.id;
      const covered = (p.status === 'audited') ? '已审计' : p.status;
      opt.textContent = `素材包${pkgs.length - i} · ${p.item_count}条素材 · ${covered}`;
      select.appendChild(opt);
    });
    // 默认选中最新 audited 包 (直接可去洗稿), 否则最新一条
    const preferred = pkgs.find(p => p.status === 'audited') || pkgs[0];
    if (preferred) {
      select.value = preferred.id;
      currentPackageId = preferred.id;
      await refreshPackage();
    }
  } catch (e) {
    console.warn('loadPackages failed:', e.message);
  }
}

function selectPackage(sel) {
  currentPackageId = sel.value || null;
  if (currentPackageId) refreshPackage();
  else {
    document.getElementById('material-items').innerHTML = '';
    document.getElementById('audit-report').style.display = 'none';
    setStatus('status-material', '点「➕ 新建」让 AI 重新分析主稿，或在下方折叠区手动补素材');
  }
}

async function newPackage() {
  const select = document.getElementById('package-select');
  select.value = '';
  currentPackageId = null;
  document.getElementById('material-items').innerHTML = '';
  document.getElementById('audit-report').style.display = 'none';
  // 新建包 = AI 重新从主稿开始分析
  await autoAnalyze();
}

async function createPackage() {
  if (!currentArticle) {
    setStatus('status-material', '请先创建稿件', true);
    return;
  }
  const urls = document.getElementById('material-urls').value
    .split('\n').map(s => s.trim()).filter(Boolean).slice(0, 10);
  const manualTitle = document.getElementById('material-manual-title').value.trim() || null;
  const manualText = document.getElementById('material-manual-text').value.trim();
  toggle('btn-material-create', false);
  setStatus('status-material', '正在聚合素材…');
  try {
    const r = await api('POST', '/materials/packages', { article_id: currentArticle.id, urls });
    currentPackageId = r.id;
    if (manualText) {
      await api('POST', `/materials/packages/${r.id}/items`, { title: manualTitle, text: manualText });
      document.getElementById('material-manual-text').value = '';
      document.getElementById('material-manual-title').value = '';
    }
    subscribePackage(r.job_id);
  } catch (e) {
    setStatus('status-material', e.message, true);
    toggle('btn-material-create', true);
  }
}

function subscribePackage(jobId, scanOnly = false) {
  const source = new EventSource(`${API}/jobs/${jobId}/events`);
  source.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch (_) { return; }
    const t = data.type;
    if (t === 'material_fetch_progress') {
      setStatus('status-material', `抓取素材中… ${data.done}/${data.total}`);
    } else if (t === 'material_search_start') {
      setStatus('status-material', `智谱补搜中… (${(data.queries || []).length} 条查询词)`);
    } else if (t === 'material_audit_start') {
      setStatus('status-material', '七层覆盖审计中…');
    } else if (t === 'material_scan_start') {
      setStatus('status-material', `扫描证据图… 候选 ${data.total || 0} 张, 下载+视觉打标中`);
    } else if (t === 'material_scan_progress') {
      setStatus('status-material', `证据图打标中… ${data.done}/${data.total}`);
    } else if (t === 'material_scan_done') {
      // 自动扫 (scanOnly=false): 后面还有 material_done, 不关流;
      // 手动扫 (scanOnly=true): 这就是终点
      if (scanOnly) {
        source.close();
        setStatus('status-material',
          `扫图完成 — 下载 ${data.downloaded || 0} 张, 证据图 ${data.charts || 0} 张`, false, true);
        toggle('btn-scan-images', true);
        refreshPackage();
      }
    } else if (t === 'material_scan_error') {
      setStatus('status-material', `扫图失败: ${data.error || ''}（不影响素材包, 可重试）`, true);
      if (scanOnly) toggle('btn-scan-images', true);
    } else if (t === 'material_done') {
      source.close();
      setStatus('status-material', `审计完成 — 覆盖 ${data.covered}/7 层`, false, true);
      reloadPackageOptions();
      refreshPackage();
      toggle('btn-material-create', true);
    } else if (t === 'material_error') {
      source.close();
      setStatus('status-material', (data.error || '素材任务失败') + ' — 可点「🔄 重新分析」重试', true);
      // 刷新按钮态 (2026-08-16): 失败后 refreshPackage 让「重新分析」恢复可点,
      // 否则用户被灰按钮困死 (补搜后重审偶发 LLM 解析失败的恢复路径)
      reloadPackageOptions();
      refreshPackage();
    }
  };
  source.onerror = () => {
    source.close();
    setStatus('status-material', 'SSE 连接错误', true);
    toggle('btn-material-create', true);
    toggle('btn-scan-images', true);
  };
}

// 手动补扫证据图 (2026-09-04 管线①): 不受「搜图」总闸限制, 老包回填用
async function scanImages() {
  if (!currentPackageId) return;
  toggle('btn-scan-images', false);
  setStatus('status-material', '扫图任务启动…');
  try {
    const r = await api('POST', `/materials/packages/${currentPackageId}/scan-images`);
    subscribePackage(r.job_id, true);
  } catch (e) {
    setStatus('status-material', e.message, true);
    toggle('btn-scan-images', true);
  }
}

async function refreshPackage() {
  if (!currentPackageId) return;
  try {
    const p = await api('GET', `/materials/packages/${currentPackageId}`);
    renderItems(p.items || []);
    renderAudit(p);
    const hasGapQueries = (p.gap_queries || []).length > 0;
    toggle('btn-material-search', hasGapQueries && p.status !== 'collecting');
    toggle('btn-goto-writing', p.status === 'audited');
    toggle('btn-reaudit', p.status !== 'collecting');
    toggle('btn-scan-images', p.status !== 'collecting');
    toggle('btn-material-create', true);
    if (p.status === 'failed' && p.error_message) {
      setStatus('status-material', p.error_message, true);
    }
  } catch (e) {
    console.warn('refreshPackage failed:', e.message);
  }
}

/** 只刷新下拉选项并保持当前选中 (任务完成后新包回填), 不触发自动分析 */
async function reloadPackageOptions() {
  if (!currentArticle || !currentPackageId) return;
  try {
    const pkgs = await api('GET', `/materials/packages?article_id=${encodeURIComponent(currentArticle.id)}`);
    const select = document.getElementById('package-select');
    select.innerHTML = '<option value="">— 新建素材包 —</option>';
    pkgs.forEach((p, i) => {
      const opt = document.createElement('option');
      opt.value = p.id;
      const covered = (p.status === 'audited') ? '已审计' : p.status;
      opt.textContent = `素材包${pkgs.length - i} · ${p.item_count}条素材 · ${covered}`;
      select.appendChild(opt);
    });
    select.value = currentPackageId;
  } catch (e) { /* 静默 */ }
}

function renderItems(items) {
  const container = document.getElementById('material-items');
  container.innerHTML = '';
  if (!items.length) {
    container.innerHTML = '<div style="font-size:0.78rem;color:var(--text-muted);padding:0.5rem 0;">暂无素材 — 贴 URL 或手动粘贴后聚合</div>';
    return;
  }
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'material-item' + (it.fetch_ok ? '' : ' failed');
    const typeBadge = it.source_type === 'search' ? '🔍补搜' : (it.source_type === 'manual' ? '✍️手动' : '🌐抓取');
    const src = it.media || (it.source_url || '').replace(/^https?:\/\//, '').split('/')[0];
    // 层归属小图标: 审计标注这条素材支撑哪些层 (title 悬浮显示层名)
    const tagChips = (it.layer_tags || [])
      .filter(t => layerNames()[t])
      .map(t => `<span class="layer-mini" title="${layerNames()[t]}">${t}</span>`)
      .join('');
    // 证据图缩略图行 (2026-09-04 管线①): ≤4 张, is_chart 优先, 点击开原图
    const imgs = (it.images_json || []).filter(e => e && e.url);
    const sorted = [...imgs].sort((a, b) => {
      const av = (a.vlm && a.vlm.is_chart) ? 1 : 0, bv = (b.vlm && b.vlm.is_chart) ? 1 : 0;
      return bv - av;
    }).slice(0, 4);
    const thumbs = sorted.length ? `
      <div style="display:flex;gap:0.4rem;margin-top:0.4rem;flex-wrap:wrap;">
        ${sorted.map(e => {
          const chartMark = (e.vlm && e.vlm.is_chart) ? '📊' : '🖼';
          const tip = (e.vlm && e.vlm.desc_zh) ? e.vlm.desc_zh : (e.alt || '证据图');
          return `<a href="${escapeHtml(e.url)}" target="_blank" rel="noopener"
            title="${escapeHtml(tip)}" style="flex:0 0 auto;position:relative;display:inline-block;">
            <img src="${escapeHtml(e.url)}" alt="" loading="lazy"
              onerror="this.parentElement.style.display='none'"
              style="height:52px;border-radius:4px;border:1px solid var(--border);object-fit:cover;max-width:120px;display:block;">
            <span style="position:absolute;left:2px;top:1px;font-size:12px;text-shadow:0 1px 3px #000;">${chartMark}</span>
          </a>`;
        }).join('')}
      </div>` : '';
    div.innerHTML = `
      <span class="mtype">${typeBadge}</span>
      ${tagChips}
      <div class="mmain">
        <div class="mtitle">${escapeHtml(it.title || src || '未命名素材')}${it.fetch_ok ? '' : ' <b style="color:#f87171;">抓取失败</b>'}</div>
        <div class="mmeta">${src ? escapeHtml(src) + ' · ' : ''}${it.char_count}字${it.search_query ? ' · 搜「' + escapeHtml(it.search_query.slice(0, 24)) + '」' : ''}</div>
        ${thumbs}
      </div>
      <button class="btn btn-secondary btn-sm" onclick="deleteMaterialItem('${it.id}')">删除</button>
    `;
    container.appendChild(div);
  });
}

function renderAudit(p) {
  const report = document.getElementById('audit-report');
  const audit = p.audit_json;
  if (!audit || !audit.layers) {
    report.style.display = 'none';
    return;
  }
  report.style.display = 'block';
  const layers = Object.values(audit.layers);
  const covered = layers.filter(l => l.covered).length;
  const naCount = layers.filter(l => l.applicable === false).length;
  document.getElementById('audit-summary').textContent =
    `已覆盖 ${covered}/${7 - naCount}${naCount ? ` · ${naCount} 层不适用` : ''} · ${audit.summary || ''}`;
  // 七层红绿 (灰 = 不适用该内容类型)
  const layersBox = document.getElementById('audit-layers');
  layersBox.innerHTML = '';
  Object.keys(layerNames()).forEach(lid => {
    const l = audit.layers[lid] || { applicable: true, covered: false, evidence: '', gaps: '', search_queries: [] };
    const na = l.applicable === false;
    const chipClass = na ? 'na' : (l.covered ? 'ok' : 'gap');
    const chipText = na ? `◌ ${lid}` : (l.covered ? `✓ ${lid}` : `✗ ${lid}`);
    const row = document.createElement('div');
    row.className = 'layer-row';
    row.innerHTML = `
      <span class="layer-chip ${chipClass}">${chipText}</span>
      <div class="layer-body">
        <div class="layer-name">${layerNames()[lid]}${na ? ' <span style="font-size:0.7rem;color:var(--text-muted);">(不适用本篇)</span>' : ''}</div>
        ${l.evidence ? `<div class="layer-evidence">证据: ${escapeHtml(l.evidence)}</div>` : ''}
        ${l.gaps ? `<div class="layer-gaps">${na ? '说明' : '缺口'}: ${escapeHtml(l.gaps)}</div>` : ''}
      </div>
    `;
    layersBox.appendChild(row);
  });
  // 缺口搜索词 checkbox (默认勾选)
  const qbox = document.getElementById('audit-queries');
  qbox.innerHTML = '';
  const queries = p.gap_queries || [];
  if (queries.length) {
    const label = document.createElement('div');
    label.className = 'aq-label';
    label.textContent = '缺口补搜词（勾选后执行智谱搜索）:';
    qbox.appendChild(label);
    queries.forEach(q => {
      const lab = document.createElement('label');
      lab.className = 'aq-item';
      lab.innerHTML = `<input type="checkbox" value="${escapeHtml(q)}" checked> ${escapeHtml(q)}`;
      qbox.appendChild(lab);
    });
  } else {
    qbox.innerHTML = '<div style="font-size:0.75rem;color:var(--text-muted);">无缺口搜索词</div>';
  }
}

async function addManualItem() {
  if (!currentPackageId) {
    setStatus('status-material', '请先创建素材包', true);
    return;
  }
  const title = document.getElementById('material-manual-title').value.trim() || null;
  const text = document.getElementById('material-manual-text').value.trim();
  const url = document.getElementById('material-manual-url').value.trim() || null;
  if (!text && !url) {
    setStatus('status-material', '请粘贴素材正文或填 URL', true);
    return;
  }
  toggle('btn-material-add', false);
  try {
    await api('POST', `/materials/packages/${currentPackageId}/items`,
      url ? { url, title } : { title, text });
    setStatus('status-material', '素材已添加，自动重新审计…');
    reauditPackage();
  } catch (e) {
    setStatus('status-material', e.message, true);
  } finally {
    toggle('btn-material-add', true);
  }
}

async function deleteMaterialItem(itemId) {
  if (!currentPackageId) return;
  try {
    await api('DELETE', `/materials/packages/${currentPackageId}/items/${itemId}`);
    refreshPackage();
    setStatus('status-material', '已删除（点「重新审计」更新覆盖报告）');
  } catch (e) {
    setStatus('status-material', e.message, true);
  }
}

async function reauditPackage() {
  if (!currentPackageId) return;
  toggle('btn-reaudit', false);
  setStatus('status-material', '重新审计中…');
  try {
    const r = await api('POST', `/materials/packages/${currentPackageId}/audit`);
    subscribePackage(r.job_id);
  } catch (e) {
    setStatus('status-material', e.message, true);
    toggle('btn-reaudit', true);
  }
}

async function supplementSearch() {
  if (!currentPackageId) return;
  const checked = Array.from(document.querySelectorAll('#audit-queries input[type="checkbox"]:checked'))
    .map(i => i.value);
  toggle('btn-material-search', false);
  try {
    const r = await api('POST', `/materials/packages/${currentPackageId}/supplement-search`,
      { queries: checked });
    setStatus('status-material', `补搜中… ${r.queries.length} 条查询词`);
    subscribePackage(r.job_id);
  } catch (e) {
    setStatus('status-material', e.message, true);
    toggle('btn-material-search', true);
  }
}

function gotoWriting() {
  if (!currentArticle) return;
  const params = new URLSearchParams({ article_id: currentArticle.id });
  if (currentPackageId) params.set('package_id', currentPackageId);
  window.location.href = '/web/writing.html?' + params.toString();
}

// ── 初始化: 调取最近文章 (回访用户不必重贴) ──
document.addEventListener('DOMContentLoaded', async () => {
  try {
    const list = await api('GET', '/articles?limit=1');
    if (list.length) {
      currentArticle = list[0];
      document.getElementById('article-title').value = currentArticle.title || '';
      document.getElementById('article-url').value = currentArticle.source_url || '';
      document.getElementById('article-text').value = currentArticle.raw_text || '';
      setStatus('status-create', `已调取最近稿件: ${currentArticle.title || currentArticle.id}`, false, true);
      enablePackageUI();
      renderComments(currentArticle.deconstruct_json);
      await loadPackages(currentArticle.id);
    }
  } catch (e) { /* 首访无文章, 静默 */ }
});
