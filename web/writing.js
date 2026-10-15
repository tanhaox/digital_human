// writing.js (2026-08-15) — 文字加工中心: 洗稿/修正/爆改/保存/字数
// 依赖 app.js v7 共享层 (api/setStatus/toggle/fetchScript/loadScriptList)

// ── 洗稿 (v7: 挂素材包 material_package_id) ──
async function rewriteArticle() {
  if (!currentArticle) return;
  const model = document.getElementById('rewrite-model').value;
  const template = document.getElementById('rewrite-template').value || 'laochen_default';
  const videoFormat = document.getElementById('rewrite-format').value || 'portrait';
  const hostSelect = document.getElementById('rewrite-host');
  const personaId = (hostSelect && hostSelect.value) || null;
  toggle('btn-rewrite', false);
  setStatus('status-rewrite', currentPackageId ? '洗稿中（素材包已注入）…' : '洗稿中，请稍候...');
  try {
    const perspective = document.getElementById('perspective-1')?.value?.trim() || null;
    const { job_id } = await api('POST', `/articles/${currentArticle.id}/rewrite`, {
      model, prompt_template: template, video_format: videoFormat, perspective,
      persona_id: personaId,
      material_package_id: currentPackageId || null,  // 七层素材包注入
    });
    const source = new EventSource(`${API}/jobs/${job_id}/events`);
    const ta = document.getElementById('script-text');
    ta.value = '';
    source.onmessage = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }  // P2-4: 坏数据不崩
      if (data.type === 'rewrite_chunk') {
        ta.value += data.chunk;
        ta.scrollTop = ta.scrollHeight;
        updateCharCount();
      } else if (data.type === 'rewrite_done') {
        source.close();
        setStatus('status-rewrite', '洗稿完成', false, true);
        ta.value = '';
        fetchScript(data.script_id);
        toggle('btn-save-script', true);
        // 显示修正观点/爆品改造区 + 口播适配等工具 (2026-08-27 统一走 revealScriptToolbars)
        revealScriptToolbars();
      } else if (data.type === 'rewrite_error') {
        source.close();
        setStatus('status-rewrite', data.error, true);
        toggle('btn-rewrite', true);
      }
    };
    source.onerror = () => {
      source.close();
      setStatus('status-rewrite', 'SSE 连接错误', true);
      toggle('btn-rewrite', true);
    };
  } catch (e) {
    setStatus('status-rewrite', e.message, true);
    toggle('btn-rewrite', true);
  }
}

// ── 字数统计 (七层模板目标 1400~1550) ──
function updateCharCount() {
  const ta = document.getElementById('script-text');
  const el = document.getElementById('script-char-count');
  if (!ta || !el) return;
  const n = ta.value.length;
  let hint = '';
  if (n > 0 && n < 2000) hint = ' · ⚠ 距目标还差 ' + (2100 - n) + ' 字 — 情感档语速 6.9字/秒, 不足 5 分钟';
  else if (n > 2500) hint = ' · 略超目标';
  el.textContent = `${n} 字 · 目标 2100~2400（七层·情感语速实测校准）${hint}`;
}

// ── 修正观点 (从 app.js v6 迁入, 按钮改 btn-goto-audio) ──
async function correctScript() {
  if (!currentScript) return;
  const perspective = document.getElementById('perspective-2')?.value?.trim();
  if (!perspective) {
    setStatus('status-correct', '请输入修正观点', true);
    return;
  }
  const model = document.getElementById('rewrite-model').value;
  toggle('btn-correct', false);
  setStatus('status-correct', '修正中，请稍候...');
  try {
    const { job_id } = await api('POST', `/scripts/${currentScript.id}/correct`, { perspective, model });
    const source = new EventSource(`${API}/jobs/${job_id}/events`);
    source.onmessage = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      if (data.type === 'correct_chunk') {
        const ta = document.getElementById('script-text');
        ta.value += data.chunk;
        ta.scrollTop = ta.scrollHeight;
        updateCharCount();
      } else if (data.type === 'correct_done') {
        source.close();
        setStatus('status-correct', data.had_boost
          ? '修正完成 — 旧改造稿基于旧观点已作废, 编辑区现为修正后洗稿稿; 仍需爆款结构请重新点「爆品改造」'
          : '修正完成', false, true);
        document.getElementById('script-text').value = '';
        fetchScript(data.script_id);
        toggle('btn-correct', true);
        revealScriptToolbars();
      } else if (data.type === 'correct_error') {
        source.close();
        setStatus('status-correct', data.error, true);
        toggle('btn-correct', true);
      }
    };
    source.onerror = () => {
      source.close();
      setStatus('status-correct', 'SSE 连接错误', true);
      toggle('btn-correct', true);
    };
  } catch (e) {
    setStatus('status-correct', e.message, true);
    toggle('btn-correct', true);
  }
}

async function loadLatest() {
  try {
    const list = await api('GET', '/articles?limit=1');
    if (!list.length) {
      setStatus('status-rewrite', '暂无稿件 — 请先到「新闻线索」页创建', true);
      return;
    }
    currentArticle = list[0];
    setStatus('status-rewrite', `已加载稿件: ${currentArticle.title || currentArticle.id}`, false, true);
    showBanner();
    toggle('btn-rewrite', true);
    // 2026-08-22: 直开本页同样自动调取该文章已有脚本 (与 article_id 跳入行为一致)
    try {
      const scripts = await api('GET', '/scripts?limit=50');
      const mine = scripts.filter(s => s.article_id === currentArticle.id);
      if (mine.length) {
        await fetchScript(mine[0].id);
        revealScriptToolbars();
      }
    } catch (_) { /* 静默 */ }
    // 顺带绑定该文章最新素材包 (等价于新闻线索页跳转, 2026-08-15)
    try {
      const pkgs = await api('GET', `/materials/packages?article_id=${currentArticle.id}`);
      const preferred = pkgs.find(p => p.status === 'audited') || pkgs[0];
      if (preferred) {
        currentPackageId = preferred.id;
        await renderMaterialBadge();
      }
    } catch (_) { /* 无包静默 */ }
  } catch (e) {
    setStatus('status-rewrite', e.message, true);
  }
}

// ── 人物/模板下拉 (从 app.js v6 迁入) ──
async function loadHosts() {
  const select = document.getElementById('rewrite-host');
  if (!select) return;
  try {
    // 人物即账号 (2026-08-08): 下拉读 /personas, 选项值 = persona.id
    const personas = await api('GET', '/personas');
    select.innerHTML = '';
    if (!personas.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '无人物（请先到「人物」页创建）';
      select.appendChild(opt);
      return;
    }
    let defaultSelected = false;
    personas.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.brand_name ? `${p.name} · ${p.brand_name}` : `${p.name} · ${p.prompt_template}`;
      select.appendChild(opt);
      if (!defaultSelected && p.brand_name) {
        opt.selected = true;
        defaultSelected = true;
      }
    });
    if (!defaultSelected && personas.length) select.selectedIndex = 0;
    // 模板联动 (2026-08-25): 后端"人物即账号"会用 persona.prompt_template 覆盖模板下拉 —
    // 选人物时同步把模板下拉切到该人物的模板, 所见即所得, 消除"选了被静默覆盖"。
    const _syncTemplate = () => {
      const pid = select.value;
      const p = personas.find(x => x.id === pid);
      const tplSel = document.getElementById('rewrite-template');
      if (p && p.prompt_template && tplSel) {
        tplSel.value = p.prompt_template;
        if (typeof checkPersonaLock === 'function') checkPersonaLock();
      }
    };
    select.addEventListener('change', _syncTemplate);
    _syncTemplate();  // 初始默认人物也同步
  } catch (e) {
    console.error('加载数字人失败', e);
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '人物加载失败';
    select.appendChild(opt);
  }
}

async function loadPromptTemplates() {
  try {
    const templates = await api('GET', '/articles/prompt-templates');
    const select = document.getElementById('rewrite-template');
    select.innerHTML = '';
    if (!templates.length) {
      const opt = document.createElement('option');
      opt.value = 'laochen_default';
      opt.textContent = 'laochen_default';
      select.appendChild(opt);
      return;
    }
    templates.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.name;
      select.appendChild(opt);
    });
    // 默认选中老谭科技七层模板（不存在时保持第一个）
    select.value = 'laotan-tech_7layer_v2';
  } catch (e) {
    console.error('加载提示词模板失败', e);
    const select = document.getElementById('rewrite-template');
    select.innerHTML = '<option value="laochen_default">laochen_default</option>';
  }
}

// ── persona 提示 (拆页后音色在音频加工页, 这里只提示不定音色) ──
let _personaLockInfo = null;
async function checkPersonaLock() {
  const template = document.getElementById('rewrite-template').value;
  if (!template) return;
  try {
    const persona = await api('GET', `/personas/by-template/${encodeURIComponent(template)}`);
    if (persona) {
      _personaLockInfo = persona;
      const voiceName = persona.voice ? persona.voice.name : (persona.voice_id ? '已绑定' : '未绑定');
      const roleName = persona.role ? persona.role.name : '未绑定';
      const info = document.getElementById('material-context-info');
      if (info && !info.textContent) {
        info.textContent = `🔒 人物「${persona.name}」— 音色: ${voiceName}（音频加工页锁定）| 形象: ${roleName}`;
      }
    } else {
      _personaLockInfo = null;
    }
  } catch (e) { _personaLockInfo = null; }
}

// ── 项目目录渲染 (P1-2 修复版, 从 app.js v6 迁入) ──
function renderProjectDir(projectDir) {
  let el = document.getElementById('project-dir');
  if (!el) {
    el = document.createElement('div');
    el.id = 'project-dir';
    el.className = 'project-dir';
    const scriptText = document.getElementById('script-text');
    const anchor = scriptText ? scriptText.parentElement : document.querySelector('#step-2-card, .card');
    if (!anchor) return;
    anchor.insertBefore(el, scriptText);
  }
  if (projectDir) {
    el.innerHTML = `<strong>项目目录：</strong><span style="color:#94a3b8;word-break:break-all;">${projectDir}</span> <span style="font-size:0.75rem;color:#64748b;">(服务器本地路径，请直接在服务器查看)</span>`;
  } else {
    el.innerHTML = '';
  }
}

async function saveScript() {
  if (!currentScript) {
    // 2026-08-22: 原静默 return 导致"保存无反应 + 去生成音频灰按钮" 的困惑。
    // 显式提示下一步, 让用户知道该先洗稿/调取脚本。
    setStatus('status-rewrite', '尚无脚本可保存 — 请先「开始洗稿」或从上方下拉调取已有脚本', true);
    return;
  }
  const text = document.getElementById('script-text').value.trim();
  try {
    // 2026-08-12: 改造后编辑区显示改造稿, 保存写回 boosted_text (TTS 用改造稿)
    const hasBoosted = !!currentScript.boosted_text;
    const payload = hasBoosted ? { boosted_text: text } : { script_text: text };
    currentScript = await api('PUT', `/scripts/${currentScript.id}`, payload);
    setStatus('status-rewrite', hasBoosted ? '改造稿已保存并重新分段' : '脚本已保存并重新分段', false, true);
    toggle('btn-goto-audio', true);
  } catch (e) {
    setStatus('status-rewrite', e.message, true);
  }
}

// ── 爆品改造 (从 app.js v6 迁入) ──
async function boostScript() {
  if (!currentScript) return;
  toggle('btn-boost', false);
  setStatus('status-boost', '爆品改造中（P-L反问目录→P4精修→P6拼音审计）…情绪标注在生成音频时自动跑');
  try {
    const { job_id } = await api('POST', `/scripts/${currentScript.id}/boost`);
    const source = new EventSource(`${API}/jobs/${job_id}/events`);
    source.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.type && data.type.startsWith('boost_') && data.msg) {
        setStatus('status-boost', data.msg);
        return;
      }
      if (data.type === 'boost_done') {
        source.close();
        if (data.boosted) {
          // 拼音纠音 (2026-08-25): P6 审计结果随完成态展示, 易错词由词表在 TTS 自动标注
          const fixes = data.pinyin_fixes?.length
            ? ` · 🔊 拼音纠音 ${data.pinyin_fixes.length} 处 (${data.pinyin_fixes.join('、')})` : '';
          // P7 流量评审 (2026-08-25): 五维评级常驻, 短板提醒
          const audit = data.flow_overall
            ? ` · 📊 流量评审 ${data.flow_overall} (评论率${data.flow_comment || '?'}${data.flow_weaknesses?.length ? `, 短板: ${data.flow_weaknesses[0]}` : ''})` : '';
          setStatus('status-boost', `✅ 爆品改造完成${fixes}${audit}`, false, true);
          fetchScript(currentScript.id);
        } else {
          setStatus('status-boost', '改造未执行', true);
        }
        toggle('btn-boost', true);
      } else if (data.type === 'boost_error') {
        source.close();
        setStatus('status-boost', '爆品改造失败: ' + (data.error || ''), true);
        toggle('btn-boost', true);
      }
    };
    source.onerror = () => {
      source.close();
      setStatus('status-boost', 'SSE 连接错误', true);
      toggle('btn-boost', true);
    };
  } catch (e) {
    setStatus('status-boost', e.message, true);
    toggle('btn-boost', true);
  }
}

// ── 口播适配 (2026-08-27): 全文数字/百分比/连字符 → 中文读法, 其余 1:1 ──
// 手改稿专用: 洗稿数字禁令只覆盖首产, 手改引入的阿拉伯数字靠此补转。
// 结果只回填编辑区不落库 — 目检后点「保存编辑」才写库+重分段。
async function ttsAdaptScript() {
  if (!currentScript) return;
  const ta = document.getElementById('script-text');
  const text = ta.value.trim();
  if (!text) {
    setStatus('status-rewrite', '编辑区无内容可适配', true);
    return;
  }
  toggle('btn-tts-adapt', false);
  setStatus('status-rewrite', '口播适配中（数字/百分比/连字符/星号 → 中文读法，其余一字不动）…');
  try {
    const data = await api('POST', `/scripts/${currentScript.id}/tts-adapt`, { text });
    ta.value = data.adapted_text;
    updateCharCount();
    setStatus('status-rewrite', data.changed_lines > 0
      ? `✅ 口播适配完成: ${data.changed_lines} 行已转读法/清符号 — 请目检后点「保存编辑」落库`
      : '✅ 全文无数字/百分号/连字符/星号, 无需适配', false, true);
  } catch (e) {
    setStatus('status-rewrite', '口播适配失败: ' + e.message, true);
  } finally {
    toggle('btn-tts-adapt', true);
  }
}

// ── 脚本就绪后的工具区显现 (2026-08-27 bug 修复) ──
// 爆品改造/修正观点区原先只在 洗稿完成/手动调取 两路显示; 带 article_id
// 进入的自动调取路径 (从音频页后退回来的主路径) 漏了 → 按钮凭空消失。
function revealScriptToolbars() {
  const p2 = document.getElementById('perspective-2-section');
  if (p2) p2.style.display = 'block';
  const boost = document.getElementById('boost-section');
  if (boost) boost.style.display = 'block';
  toggle('btn-save-script', true);
  toggle('btn-boost', true);
  toggle('btn-tts-adapt', true);
  toggle('btn-goto-audio', true);
}

// ── 调取已有脚本 (从 app.js v6 迁入) ──
async function selectExistingScript(select) {
  const scriptId = select.value;
  if (!scriptId) return;
  try {
    await fetchScript(scriptId);
    revealScriptToolbars();
    // 调取脚本后回填来源文章 banner
    if (currentScript.article) {
      currentArticle = currentScript.article;
      currentPackageId = currentScript.material_package_id || null;
      showBanner();
    }
    setStatus('status-rewrite', '已调取脚本，可编辑/改观点/爆品改造', false, true);
  } catch (e) {
    setStatus('status-rewrite', '调取脚本失败: ' + e.message, true);
  }
}

// ── banner (来源稿件 + 素材包状态) ──
function showBanner() {
  const banner = document.getElementById('article-banner');
  if (!banner || !currentArticle) return;
  banner.style.display = 'block';
  document.getElementById('banner-article-title').textContent =
    currentArticle.title || currentArticle.id;
  renderMaterialBadge();
}

async function renderMaterialBadge() {
  const el = document.getElementById('material-context-info');
  if (!el) return;
  if (!currentPackageId) {
    el.textContent = '';
    return;
  }
  try {
    const p = await api('GET', `/materials/packages/${currentPackageId}`);
    const covered = p.audit_json
      ? Object.values(p.audit_json.layers || {}).filter(l => l.covered).length : null;
    el.textContent = covered !== null
      ? `🧺 素材包已挂载: ${p.items.length} 条素材 · 覆盖 ${covered}/7 层`
      : `🧺 素材包已挂载: ${p.items.length} 条素材（未审计）`;
  } catch (e) { el.textContent = ''; }
}

function gotoAudio() {
  if (!currentScript) return;
  window.location.href = '/web/audio.html?script_id=' + encodeURIComponent(currentScript.id);
}

// ── 初始化: URL 参数 (article_id/package_id 从新闻线索页跳入) ──
document.addEventListener('DOMContentLoaded', async () => {
  loadPromptTemplates();
  loadHosts();
  // 字数实时统计
  const ta = document.getElementById('script-text');
  if (ta) ta.addEventListener('input', updateCharCount);

  const params = new URLSearchParams(location.search);
  const articleId = params.get('article_id');
  const packageId = params.get('package_id');
  if (articleId) {
    try {
      currentArticle = await api('GET', `/articles/${articleId}`);
      if (packageId) {
        // 包归属校验 (2026-08-16 bug 防再犯): 旧稿件的包被带入新稿件时,
        // 后端洗稿会报"素材包不存在或不属于该稿件" — 这里提前发现并降级, 不堵流程
        try {
          const pkg = await api('GET', `/materials/packages/${packageId}`);
          if (pkg && pkg.article_id === articleId) {
            currentPackageId = packageId;
          } else {
            toast('⚠ 素材包不属于该稿件，已按无包模式继续（可回新闻线索重新分析）', 'info');
          }
        } catch (_) {
          toast('⚠ 素材包不存在，已按无包模式继续（可回新闻线索重新分析）', 'info');
        }
      }
      showBanner();
      toggle('btn-rewrite', true);
      // 2026-08-22: 带 article_id 进入时自动调取该文章已有脚本 — 否则 currentScript=null,
      // "保存编辑"静默失效 + "去生成音频"一直禁用 (用户反馈灰按钮的根因)。
      // 新闻线索页跳转只传 article_id/package_id, 不带 script_id。
      let autoLoaded = false;
      try {
        const scripts = await api('GET', '/scripts?limit=50');
        const mine = scripts.filter(s => s.article_id === articleId);
        if (mine.length) {
          await fetchScript(mine[0].id);
          revealScriptToolbars();
          autoLoaded = true;
        }
      } catch (_) { /* 无脚本或列表失败: 静默, 用户走「开始洗稿」 */ }
      setStatus('status-rewrite', autoLoaded
        ? (currentPackageId ? '已调取该文章已有脚本（洗稿将重新生成，含素材包）' : '已调取该文章已有脚本，可编辑/保存/生成音频')
        : (currentPackageId ? '已就绪 — 洗稿将注入素材包' : '已就绪 — 可直接洗稿（无素材包）'));
    } catch (e) {
      setStatus('status-rewrite', '加载稿件失败: ' + e.message, true);
    }
  } else {
    // 直开本页 (无 URL 参数): 自动绑定最新稿件, 免点「加载最新稿件」(2026-08-15)
    await loadLatest();
  }
});
