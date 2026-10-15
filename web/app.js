// app.js v7 (2026-08-15 三页拆分) — 新闻线索 / 文字加工 / 音频加工 三页共享工具.
// 单页专属函数在各页 JS (news.js / writing.js / audio.js); 本文件跨页调用
// 一律加 typeof 守卫 + toggle 的元素守卫, 杜绝 P1-1 类 null 崩溃.
const API = '/api';
let currentArticle = null;    // 新闻线索/文字加工页: 当前文章
let currentScript = null;     // 文字加工/音频加工页: 当前脚本
let currentPackageId = null;  // 新闻线索(聚合) → 文字加工(洗稿挂包) 传递

function setStatus(id, text, isError = false, isSuccess = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'status' + (isError ? ' error' : '') + (isSuccess ? ' success' : '');
}

function toggle(id, enabled) {
  const el = document.getElementById(id);
  if (el) el.disabled = !enabled;
}

// P1-1 修复: 安全设置「保存导演选择」按钮可用态 (元素缺失时静默跳过)。
function setSaveDirectorEnabled(enabled) {
  const el = document.getElementById('btn-save-director');
  if (el) el.disabled = !enabled;
}

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    // detail 可能是对象/数组(FastAPI 422 校验错误) — 字符串化防 [object Object]
    const d = data.detail;
    const msg = d == null ? `HTTP ${resp.status}`
      : (typeof d === 'string' ? d : JSON.stringify(d).slice(0, 300));
    throw new Error(msg);
  }
  return resp.json().catch(() => ({}));
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Toast (v7 从 index.js 迁入, 加守卫) ──
function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 4000);
}

// ── 启用的管线 (音频加工页 C/P/H 开关, localStorage 持久化) ──
function getPipelineFlags() {
  try {
    const raw = localStorage.getItem('dh_pipeline_flags');
    if (raw) return JSON.parse(raw);
  } catch (e) { /* 损坏则回退默认 */ }
  // 默认 C 线关: 数字人出镜默认不启用, 只跑 P/H 线
  return { c: false, p: true, h: true };
}

function savePipelineFlags() {
  localStorage.setItem('dh_pipeline_flags', JSON.stringify(getPipelineFlags()));
}

/** director.js 复用: 返回当前启用的管线数组 ['c','p','h'] 的子集 */
function getEnabledPipelines() {
  const flags = getPipelineFlags();
  const enabled = [];
  if (flags.c) enabled.push('c');
  if (flags.p) enabled.push('p');
  if (flags.h) enabled.push('h');
  return enabled;
}

/** 音频加工页开关 onchange: 写回 localStorage */
function onPipelineToggle() {
  const f = getPipelineFlags();
  f.c = document.getElementById('toggle-c')?.checked ?? f.c;
  f.p = document.getElementById('toggle-p')?.checked ?? f.p;
  f.h = document.getElementById('toggle-h')?.checked ?? f.h;
  savePipelineFlags();
  const n = (f.c ? 1 : 0) + (f.p ? 1 : 0) + (f.h ? 1 : 0);
  const hint = document.getElementById('pipeline-hint');
  if (hint) {
    hint.textContent = n === 3 ? '⚠ 三条管线全开: 将启用数字人出镜 (C线), LLM 可能规划 host slot。若只要 P/H 请关闭 C线'
      : n === 0 ? '⚠ 全部关闭，将只跑兜底(本地素材/黑场)' : '';
  }
}

// ── 调取脚本 (文字加工 + 音频加工共用) ──
// v7: DOM 访问全部守卫; 页面函数 (renderProjectDir / renderSegments /
// restoreAudioJobs / updateCharCount / applyPersonaLockByTemplate) typeof 判定。
async function fetchScript(scriptId) {
  currentScript = await api('GET', `/scripts/${scriptId}`);
  const ta = document.getElementById('script-text');
  if (ta) {
    // 2026-08-12: 改造后编辑区显示改造稿(boosted_text) 并保存到 boosted_text
    const hasBoosted = !!currentScript.boosted_text;
    ta.value = hasBoosted ? currentScript.boosted_text : (currentScript.script_text || '');
    const badge = document.getElementById('editor-mode-badge');
    if (badge) {
      badge.textContent = hasBoosted ? '改造稿' : '洗稿稿';
      badge.style.background = hasBoosted ? 'rgba(139,92,246,0.2)' : 'var(--border)';
      badge.style.color = hasBoosted ? '#a78bfa' : 'var(--text-secondary)';
    }
    if (typeof updateCharCount === 'function') updateCharCount();
  }
  if (typeof renderProjectDir === 'function') renderProjectDir(currentScript.project_dir);
  if (typeof renderSegments === 'function') renderSegments(currentScript.segments);
  await loadVoices();
  // 音频加工页: 按脚本模板锁 persona 音色 (2026-08-15 拆页后模板/音色分居两页)
  if (typeof applyPersonaLockByTemplate === 'function' && currentScript.prompt_template) {
    applyPersonaLockByTemplate(currentScript.prompt_template);
  }
  // 2026-08-14: 调取已有脚本后启用生成音频按钮
  toggle('btn-audio', true);
  toggle('btn-goto-audio', true);
  // 2026-08-22: 载入脚本即可保存编辑 (auto-load 已有脚本路径之前漏启用 btn-save-script)
  toggle('btn-save-script', true);
  // ID-019: 刷新/跳页返回后恢复音频任务状态 (仅音频加工页)
  if (typeof restoreAudioJobs === 'function') restoreAudioJobs();
}

async function loadVoices() {
  const select = document.getElementById('voice-select');
  if (!select) return; // 本页无音色下拉 (新闻线索/文字加工页)
  const previous = select.value;
  try {
    const voices = await api('GET', '/voices');
    select.innerHTML = '';
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = voices.length ? '默认音色（跟随主持人）' : '默认音色（暂无已保存音色）';
    select.appendChild(defaultOpt);
    voices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.id;
      opt.textContent = `${v.name} (${v.backend})`;
      select.appendChild(opt);
    });
    // Preserve user's selection across reloads (e.g. fetchScript re-triggers).
    if (previous && [...select.options].some(o => o.value === previous)) {
      select.value = previous;
    }
  } catch (e) {
    console.error('加载音色失败', e);
    select.innerHTML = '<option value="">默认音色（音色列表加载失败）</option>';
  }
}

// ── 脚本选择下拉 (文字加工/音频加工页共用, #script-select 各页一个) ──
function fmtScriptTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch { return String(ts).slice(0, 16); }
}

async function loadScriptList() {
  try {
    const scripts = await api('GET', '/scripts?limit=30');
    const select = document.getElementById('script-select');
    if (!select) return;
    select.innerHTML = '<option value="">— 选择已有脚本（可选）—</option>';
    scripts.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      const title = (s.article && s.article.title) ? s.article.title.slice(0, 20) : s.id.slice(0, 8);
      const boosted = s.boosted_text ? '✅改造' : '📝洗稿';
      const time = s.updated_at ? ' ' + fmtScriptTime(s.updated_at) : '';
      opt.textContent = `${title} (${boosted})${time}`;
      select.appendChild(opt);
    });
  } catch (e) {
    console.warn('loadScriptList failed:', e.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  // 恢复音频加工页管线开关状态 (localStorage 持久化)
  try {
    const flags = getPipelineFlags();
    const tc = document.getElementById('toggle-c');
    const tp = document.getElementById('toggle-p');
    const th = document.getElementById('toggle-h');
    if (tc) tc.checked = flags.c;
    if (tp) tp.checked = flags.p;
    if (th) th.checked = flags.h;
  } catch (e) { /* 无开关 DOM 时忽略 */ }
  // 文字加工页: 模板切换 → persona 锁提示 (writing.js 定义)
  const tplSelect = document.getElementById('rewrite-template');
  if (tplSelect && typeof checkPersonaLock === 'function') {
    tplSelect.addEventListener('change', checkPersonaLock);
  }
  // 观点1 字数统计
  const p1 = document.getElementById('perspective-1');
  if (p1) p1.addEventListener('input', () => {
    const cnt = document.getElementById('perspective-1-count');
    if (cnt) cnt.textContent = p1.value.length;
  });
  // 观点2 字数统计
  const p2 = document.getElementById('perspective-2');
  if (p2) p2.addEventListener('input', () => {
    const cnt = document.getElementById('perspective-2-count');
    if (cnt) cnt.textContent = p2.value.length;
  });
});

// 页面加载时填充脚本列表 (自带 #script-select 守卫, 两页各填各的)
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', loadScriptList);
} else {
  loadScriptList();
}
