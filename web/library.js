/* library.js — 视频库管理页(多维标签/编号/批量操作版) */
const API = '/api/library';
let currentTab = 'assets';
let editingId = null;
let _searchTimer = null;
let _dimensionOptions = {};
let _currentAssets = [];
let _selectedIds = new Set();
// 分页: 每页 PAGE_SIZE 条, 数字翻页 (减少一次性渲染大量 <video> 的加载开销)
const PAGE_SIZE = 10;
let _assetPage = 0;
let _outputPage = 0;
let _assetTotal = 0;
let _outputTotal = 0;
let _lastAssetQueryKey = null;
let _lastOutputQueryKey = null;
let _taggingJobId = null;
let _taggingSource = null;
let _taggingPollTimer = null;
const TAGGING_API = '/api/library/tagging';

const DIM_LABELS = {
  orientation: { portrait: '竖屏', landscape: '横屏' },
  source_type: { footage: '实拍', creative: '创意' },
  location: { domestic: '国内', foreign: '国外' },
  people: { people: '有人', none: '无人' },
  preference: { like: '喜欢', neutral: '中性', dislike: '厌恶' },
};

// ── Toast ──
function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 3500);
}

// ── Tab Switch ──
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.getElementById('grid-assets').classList.toggle('hidden', tab !== 'assets');
  document.getElementById('grid-outputs').classList.toggle('hidden', tab !== 'outputs');
  const ing = document.getElementById('tab-ingest');
  if (ing) ing.classList.toggle('hidden', tab !== 'ingest');
  document.getElementById('asset-filters').classList.toggle('hidden', tab !== 'assets');
  document.getElementById('batch-bar').classList.toggle('hidden', tab !== 'assets');
  _assetPage = 0;
  _outputPage = 0;
  if (tab === 'ingest') { ingestLoadEntities(); ingestPollJobs(); }
  loadCurrentTab();
}

function loadCurrentTab() {
  if (currentTab === 'assets') loadAssets();
  else loadOutputs();
}

// ── Search Debounce ──
function debounceSearch() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => loadCurrentTab(), 400);
}

function _multiSelectValues(id) {
  const opts = Array.from(document.getElementById(id).selectedOptions);
  return opts.map(o => o.value).filter(v => v);
}

// ── Load Assets ──
async function loadAssets() {
  const params = new URLSearchParams();
  const q = document.getElementById('search-input').value.trim();
  const orient = document.getElementById('filter-orientation').value;
  const sourceType = document.getElementById('filter-source_type').value;
  const location = document.getElementById('filter-location').value;
  const people = document.getElementById('filter-people').value;
  const preference = document.getElementById('filter-preference').value;
  const scenes = _multiSelectValues('filter-scenes').join(',');
  const shotTypes = _multiSelectValues('filter-shot_types').join(',');

  if (q) params.set('q', q);
  if (orient) params.set('orientation', orient);
  if (sourceType) params.set('source_type', sourceType);
  if (location) params.set('location', location);
  if (people) params.set('people', people);
  if (preference) params.set('preference', preference);
  if (scenes) params.set('scenes', scenes);
  if (shotTypes) params.set('shot_types', shotTypes);

  const key = params.toString();
  if (key !== _lastAssetQueryKey) { _assetPage = 0; _lastAssetQueryKey = key; }

  params.set('limit', String(PAGE_SIZE));
  params.set('offset', String(_assetPage * PAGE_SIZE));

  try {
    const r = await fetch(`${API}/assets?${params}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    _currentAssets = data.items || [];
    _assetTotal = data.total || 0;
    // 当前页被删空 (本页最后一条被删) → 回退一页
    if (_assetPage > 0 && _currentAssets.length === 0 && _assetTotal > 0) {
      _assetPage -= 1;
      loadAssets();
      return;
    }
    renderAssets(_currentAssets);
    renderPager('assets', _assetPage, _assetTotal);
    document.getElementById('result-count').textContent = `共 ${_assetTotal} 条`;
  } catch (e) { toast('加载失败: ' + e.message, 'error'); }
}

function renderAssets(items) {
  const grid = document.getElementById('grid-assets');
  const empty = document.getElementById('empty-state');
  if (!items.length) { grid.innerHTML = ''; empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');
  grid.innerHTML = items.map(a => {
    const orientLabel = DIM_LABELS.orientation[a.orientation] || a.orientation;
    const prefClass = a.preference === 'like' ? 'pref-like' : a.preference === 'dislike' ? 'pref-dislike' : '';
    const sourceLabel = DIM_LABELS.source_type[a.source_type] || a.source_type;
    const locationLabel = DIM_LABELS.location[a.location] || a.location;
    const peopleLabel = DIM_LABELS.people[a.people] || a.people;
    const desc = a.description_zh || a.description_en || a.raw_query || '—';
    const sceneTags = (a.scenes || []).map(t => `<span class="scene">${escHtml(t)}</span>`).join('');
    const shotTags = (a.shot_types || []).map(t => `<span class="shot">${escHtml(t)}</span>`).join('');
    const oldTags = (a.tags || []).slice(0, 4).map(t => `<span>${escHtml(t)}</span>`).join('');
    const selected = _selectedIds.has(a.id) ? 'checked' : '';
    return `
      <div class="video-card ${prefClass}" data-id="${a.id}">
        <label class="card-check"><input type="checkbox" ${selected} onchange="toggleSelect('${a.id}')"></label>
        <div class="thumb">
          <video src="${API}/assets/${a.id}/file" controls playsinline preload="metadata"></video>
          <span class="asset-no">${escHtml(a.asset_no)}</span>
          <span class="duration">${a.duration_sec?.toFixed(1) || '?'}s</span>
          <span class="orient-badge">${orientLabel}</span>
        </div>
        <div class="info">
          <div class="desc">${escHtml(desc)}</div>
          <div class="badges">
            <span class="badge source-${a.source_type}">${sourceLabel}</span>
            <span class="badge loc-${a.location}">${locationLabel}</span>
            <span class="badge people-${a.people}">${peopleLabel}</span>
            ${a.ai_tagged_at ? `<span class="badge ai-badge" title="AI 已打标: ${escHtml(a.ai_tag_model || '—')}">🤖 AI</span>` : ''}
          </div>
          <div class="meta">
            <span>${a.width}×${a.height}</span>
            ${a.photographer ? `<span>📷 ${escHtml(a.photographer)}</span>` : ''}
            <span>使用${a.used_count}次</span>
          </div>
          ${sceneTags || shotTags ? `<div class="tags dim-tags">${sceneTags}${shotTags}</div>` : ''}
          ${oldTags ? `<div class="tags">${oldTags}</div>` : ''}
        </div>
        <div class="actions">
          <button class="${a.preference === 'like' ? 'liked' : ''}" onclick="setPreference('${a.id}', 'like')">😍</button>
          <button class="${a.preference === 'neutral' ? 'active' : ''}" onclick="setPreference('${a.id}', 'neutral')">😐</button>
          <button class="${a.preference === 'dislike' ? 'disliked' : ''}" onclick="setPreference('${a.id}', 'dislike')">🚫</button>
          <button onclick="copyAssetNo('${a.id}', '${escHtml(a.asset_no)}')" title="复制编号 ${escHtml(a.asset_no)}">📋 复制编号</button>
          <button onclick="openEdit('${a.id}')">✏ 编辑</button>
          <button class="del" onclick="deleteAsset('${a.id}')">🗑</button>
        </div>
      </div>`;
  }).join('');
  updateBatchUI();
}

// ── Load Outputs ──
async function loadOutputs() {
  const params = new URLSearchParams();
  const q = document.getElementById('search-input').value.trim();
  const orient = document.getElementById('filter-orientation').value;
  if (q) params.set('q', q);
  if (orient) params.set('orientation', orient);

  const key = params.toString();
  if (key !== _lastOutputQueryKey) { _outputPage = 0; _lastOutputQueryKey = key; }

  params.set('limit', String(PAGE_SIZE));
  params.set('offset', String(_outputPage * PAGE_SIZE));

  try {
    const r = await fetch(`${API}/outputs?${params}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    _outputTotal = data.total || 0;
    // 当前页被删空 → 回退一页
    if (_outputPage > 0 && (data.items || []).length === 0 && _outputTotal > 0) {
      _outputPage -= 1;
      loadOutputs();
      return;
    }
    document.getElementById('result-count').textContent = `共 ${_outputTotal} 条`;
    renderOutputs(data.items);
    renderPager('outputs', _outputPage, _outputTotal);
  } catch (e) { toast('加载失败: ' + e.message, 'error'); }
}

function renderOutputs(items) {
  const grid = document.getElementById('grid-outputs');
  const empty = document.getElementById('empty-state');
  if (!items.length) { grid.innerHTML = ''; empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');
  grid.innerHTML = items.map(o => {
    const orientLabel = {portrait:'竖屏',landscape:'横屏',square:'方形'}[o.orientation] || o.orientation;
    return `
      <div class="video-card">
        <div class="thumb">
          <video src="${API}/outputs/${o.id}/file" controls playsinline preload="metadata"></video>
          <span class="duration">${o.duration_sec?.toFixed(1) || '?'}s</span>
          <span class="orient-badge">${orientLabel}</span>
        </div>
        <div class="info">
          <div class="desc">${escHtml(o.title)}</div>
          <div class="meta">
            <span>${formatDate(o.created_at)}</span>
            ${o.video_format ? `<span>${o.video_format}</span>` : ''}
          </div>
        </div>
        <div class="actions">
          <button class="del" onclick="deleteOutput('${o.id}')">🗑 删除</button>
        </div>
      </div>`;
  }).join('');
}

// ── Pagination ──
function renderPager(kind, page, total) {
  const el = document.getElementById(kind === 'assets' ? 'pager-assets' : 'pager-outputs');
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, totalPages - 1);
  if (totalPages <= 1) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  el.classList.remove('hidden');

  // 数字窗口: 最多 7 个页码, 当前页居中
  const WINDOW = 7;
  let start = Math.max(0, current - Math.floor(WINDOW / 2));
  let end = Math.min(totalPages - 1, start + WINDOW - 1);
  start = Math.max(0, end - WINDOW + 1);

  const nums = [];
  for (let i = start; i <= end; i++) {
    nums.push(i === current
      ? `<span class="page-num active">${i + 1}</span>`
      : `<a class="page-num" href="javascript:void(0)" onclick="goPage('${kind}', ${i})">${i + 1}</a>`);
  }
  el.innerHTML = `
    <a class="page-btn ${current === 0 ? 'disabled' : ''}" href="javascript:void(0)" onclick="goPage('${kind}', ${current - 1})">‹ 上一页</a>
    ${nums.join('')}
    <a class="page-btn ${current === totalPages - 1 ? 'disabled' : ''}" href="javascript:void(0)" onclick="goPage('${kind}', ${current + 1})">下一页 ›</a>
    <span class="page-info">
      <input class="page-jump" type="number" min="1" max="${totalPages}" value="${current + 1}"
             title="输入页码, 回车跳转"
             onkeydown="if(event.key==='Enter')goPage('${kind}', parseInt(this.value,10)-1)">
      / ${totalPages}
    </span>`;
}

function goPage(kind, page) {
  const total = kind === 'assets' ? _assetTotal : _outputTotal;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  page = Math.max(0, Math.min(page, totalPages - 1));  // 页码框手输越界钳回边界
  if (kind === 'assets') { _assetPage = page; loadAssets(); }
  else { _outputPage = page; loadOutputs(); }
  const gridId = kind === 'assets' ? 'grid-assets' : 'grid-outputs';
  document.getElementById(gridId).scrollIntoView({ behavior: 'smooth', block: 'start' });
}
document.addEventListener('play', (e) => {
  const v = e.target;
  if (!v || v.tagName !== 'VIDEO') return;
  document.querySelectorAll('.video-card video').forEach(o => {
    if (o !== v && !o.paused) o.pause();
  });
  v.closest('.video-card')?.classList.add('playing');
}, true);
document.addEventListener('pause', (e) => {
  if (e.target && e.target.tagName === 'VIDEO') {
    e.target.closest('.video-card')?.classList.remove('playing');
  }
}, true);

// ── Actions ──
async function setPreference(id, pref) {
  try {
    const r = await fetch(`${API}/assets/${id}/preference`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preference: pref }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    loadAssets();
  } catch (e) { toast('操作失败', 'error'); }
}

async function deleteAsset(id) {
  if (!confirm('确认删除此素材？文件也会移入回收站。')) return;
  try {
    const r = await fetch(`${API}/assets/${id}?remove_file=true`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    toast('已删除', 'success');
    loadAssets();
  } catch (e) { toast('删除失败', 'error'); }
}

async function deleteOutput(id) {
  if (!confirm('确认删除此成品？文件也会移入回收站。')) return;
  try {
    const r = await fetch(`${API}/outputs/${id}?remove_file=true`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    toast('已删除', 'success');
    loadOutputs();
  } catch (e) { toast('删除失败', 'error'); }
}

// ── Batch Selection ──
function toggleSelect(id) {
  if (_selectedIds.has(id)) _selectedIds.delete(id);
  else _selectedIds.add(id);
  updateBatchUI();
}

function toggleSelectAll() {
  const checked = document.getElementById('select-all').checked;
  if (checked) _currentAssets.forEach(a => _selectedIds.add(a.id));
  else _currentAssets.forEach(a => _selectedIds.delete(a.id));
  renderAssets(_currentAssets);
}

function updateBatchUI() {
  document.getElementById('batch-count').textContent = `已选 ${_selectedIds.size}`;
  const allSelected = _currentAssets.length > 0 && _currentAssets.every(a => _selectedIds.has(a.id));
  document.getElementById('select-all').checked = allSelected;
}

// ── 复制素材编号 (2026-08-12) ──
function copyAssetNo(assetId, assetNo) {
  const done = () => toast(`已复制编号 ${assetNo}`, 'success');
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = assetNo;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); }
    catch { toast('复制失败，请手动复制', 'error'); }
    document.body.removeChild(ta);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(assetNo).then(done).catch(fallback);
  } else {
    fallback();
  }
}

async function batchUpdate(body, successMsg) {
  const ids = Array.from(_selectedIds);
  if (!ids.length) { toast('请先勾选素材', 'info'); return false; }
  try {
    await Promise.all(ids.map(id =>
      fetch(`${API}/assets/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(r => { if (!r.ok) throw new Error(id); })
    ));
    toast(successMsg, 'success');
    _selectedIds.clear();
    loadAssets();
    return true;
  } catch (e) { toast('批量操作失败', 'error'); return false; }
}

async function batchSetPreference(pref) {
  await batchUpdate({ preference: pref }, `已批量设为${DIM_LABELS.preference[pref] || pref}`);
}

async function batchSetPeople(val) {
  await batchUpdate({ people: val }, `已批量设为${DIM_LABELS.people[val] || val}`);
}

async function batchAddScene() {
  const opts = (_dimensionOptions.scenes || []).map(s => `<option value="${escHtml(s)}">${escHtml(s)}</option>`).join('');
  openBatchModal('批量添加场景', `<select id="batch-value" size="8">${opts}</select>`, async () => {
    const v = document.getElementById('batch-value').value;
    if (!v) return;
    const ids = Array.from(_selectedIds);
    const updates = await Promise.all(ids.map(id => fetch(`${API}/assets/${id}`).then(r => r.json())));
    await Promise.all(updates.map(a => {
      const scenes = Array.from(new Set([...(a.scenes || []), v]));
      return fetch(`${API}/assets/${a.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenes }),
      });
    }));
    toast('已批量添加场景', 'success');
    closeBatchModal();
    _selectedIds.clear();
    loadAssets();
  });
}

async function batchAddShotType() {
  const opts = (_dimensionOptions.shot_types || []).map(s => `<option value="${escHtml(s)}">${escHtml(s)}</option>`).join('');
  openBatchModal('批量添加镜头', `<select id="batch-value" size="8">${opts}</select>`, async () => {
    const v = document.getElementById('batch-value').value;
    if (!v) return;
    const ids = Array.from(_selectedIds);
    const updates = await Promise.all(ids.map(id => fetch(`${API}/assets/${id}`).then(r => r.json())));
    await Promise.all(updates.map(a => {
      const shot_types = Array.from(new Set([...(a.shot_types || []), v]));
      return fetch(`${API}/assets/${a.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shot_types }),
      });
    }));
    toast('已批量添加镜头', 'success');
    closeBatchModal();
    _selectedIds.clear();
    loadAssets();
  });
}

async function batchDelete() {
  const ids = Array.from(_selectedIds);
  if (!ids.length) { toast('请先勾选素材', 'info'); return; }
  if (!confirm(`确认删除选中的 ${ids.length} 个素材？文件会移入回收站。`)) return;
  try {
    await Promise.all(ids.map(id => fetch(`${API}/assets/${id}?remove_file=true`, { method: 'DELETE' })));
    toast('已批量删除', 'success');
    _selectedIds.clear();
    loadAssets();
  } catch (e) { toast('批量删除失败', 'error'); }
}

// ── Scan Import ──
async function scanImport() {
  if (!confirm('扫描 materials 目录，导入未入库的视频文件？')) return;
  const btn = document.getElementById('scan-import-btn');
  btn.disabled = true;
  btn.textContent = '⏳ 扫描中...';
  try {
    const r = await fetch(`${API}/scan`, { method: 'POST' });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    toast(`扫描完成: 发现 ${data.scanned} 个视频, 导入 ${data.imported} 个, 跳过 ${data.skipped} 个`, 'success');
    loadAssets();
    loadTaggingCount();
  } catch (e) {
    toast('扫描失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '📥 扫描导入';
  }
}

// ── Import Folder + Tag ──
function _parseCsv(value) {
  return (value || '').split(/[,，]/).map(t => t.trim()).filter(Boolean);
}

async function loadImportFolders() {
  try {
    const r = await fetch(`${API}/folders`);
    if (!r.ok) return;
    const data = await r.json();
    const sel = document.getElementById('import-folder-select');
    if (!sel) return;
    sel.innerHTML = (data.folders || []).length
      ? (data.folders || []).map(f => `<option value="${escHtml(f)}">${escHtml(f)}</option>`).join('')
      : '<option value="">（materials 下暂无子文件夹）</option>';
  } catch (_) {}
}

function openImportFolderModal() {
  loadImportFolders();
  document.getElementById('import-modal').classList.remove('hidden');
  document.getElementById('import-scenes').value = '';
  document.getElementById('import-shot_types').value = '';
}

function closeImportModal() { document.getElementById('import-modal').classList.add('hidden'); }

async function importFolder() {
  const folder = document.getElementById('import-folder-select').value;
  if (!folder) { toast('请选择素材文件夹', 'info'); return; }
  const scenes = _parseCsv(document.getElementById('import-scenes').value);
  const shot_types = _parseCsv(document.getElementById('import-shot_types').value);
  if (!scenes.length && !shot_types.length) {
    toast('请至少填写一个标签（场景或镜头）', 'info');
    return;
  }
  const btn = document.getElementById('import-modal-ok');
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/import-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, scenes, shot_types }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    closeImportModal();
    toast(`导入完成: 发现 ${data.scanned} 个视频, 新导入 ${data.imported} 个, 跳过 ${data.skipped} 个, 打标 ${data.labeled} 个`, 'success');
    loadAssets();
    loadTaggingCount();
  } catch (e) {
    toast('导入失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

// ── Edit Modal ──
function openEdit(id) {
  editingId = id;
  fetch(`${API}/assets/${id}`).then(r => {
    if (!r.ok) { toast('加载素材失败', 'error'); return null; }
    return r.json();
  }).then(a => {
    if (!a) return;
    document.getElementById('edit-desc-zh').value = a.description_zh || '';
    document.getElementById('edit-source_type').value = a.source_type || 'footage';
    document.getElementById('edit-location').value = a.location || 'foreign';
    document.getElementById('edit-people').value = a.people || 'none';
    document.getElementById('edit-scenes').value = (a.scenes || []).join(', ');
    document.getElementById('edit-shot_types').value = (a.shot_types || []).join(', ');
    document.getElementById('edit-tags').value = (a.tags || []).join(', ');
    document.getElementById('edit-modal').classList.remove('hidden');
  }).catch(() => toast('加载素材失败', 'error'));
}
function closeModal() { document.getElementById('edit-modal').classList.add('hidden'); editingId = null; }

async function saveEdit() {
  if (!editingId) return;
  const body = {
    description_zh: document.getElementById('edit-desc-zh').value || null,
    source_type: document.getElementById('edit-source_type').value,
    location: document.getElementById('edit-location').value,
    people: document.getElementById('edit-people').value,
    scenes: document.getElementById('edit-scenes').value.split(',').map(t => t.trim()).filter(Boolean),
    shot_types: document.getElementById('edit-shot_types').value.split(',').map(t => t.trim()).filter(Boolean),
    tags: document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean),
  };
  try {
    const r = await fetch(`${API}/assets/${editingId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    toast('已保存', 'success');
    closeModal();
    loadAssets();
  } catch (e) { toast('保存失败', 'error'); }
}

// ── Batch Modal ──
function openBatchModal(title, html, onOk) {
  document.getElementById('batch-modal-title').textContent = title;
  document.getElementById('batch-modal-body').innerHTML = html;
  document.getElementById('batch-modal-ok').onclick = onOk;
  document.getElementById('batch-modal').classList.remove('hidden');
}
function closeBatchModal() { document.getElementById('batch-modal').classList.add('hidden'); }

// ── Dimensions / Categories ──
async function loadDimensions() {
  try {
    const r = await fetch(`${API}/dimensions`);
    if (!r.ok) return;
    _dimensionOptions = await r.json();
    fillSelect('filter-scenes', _dimensionOptions.scenes);
    fillSelect('filter-shot_types', _dimensionOptions.shot_types);
  } catch (_) {}
}

function fillSelect(id, items) {
  const sel = document.getElementById(id);
  sel.innerHTML = `<option value="">${id.includes('scenes') ? '场景' : '镜头'}</option>`;
  (items || []).forEach(c => {
    const o = document.createElement('option');
    o.value = c; o.textContent = c;
    sel.appendChild(o);
  });
}

// ── Utils ──
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function formatDate(ts) { if (!ts) return ''; try { return new Date(ts).toLocaleDateString('zh-CN'); } catch { return ts; } }

// ── AI Tagging ──
function setTaggingButtonStop() {
  const btn = document.getElementById('tagging-start-btn');
  btn.textContent = '⏹ 停止打标';
  btn.onclick = cancelTagging;
  btn.disabled = false;
  btn.title = '停止当前 AI 打标任务';
}

function resetTaggingButton() {
  const btn = document.getElementById('tagging-start-btn');
  btn.textContent = '🤖 AI 打标';
  btn.onclick = startTagging;
  btn.disabled = false;
  btn.title = '使用本地 AI 模型分析视频画面，自动生成多维标签';
}

async function startTagging() {
  const ids = Array.from(_selectedIds);
  const count = ids.length ? ids.length : '全部';
  const msg = ids.length
    ? `对选中的 ${ids.length} 个素材进行 AI 打标？`
    : `对所有未打标素材进行 AI 打标？(约需数分钟)`;

  if (!confirm(msg)) return;

  // 显示进度条
  const prog = document.getElementById('tagging-progress');
  prog.classList.remove('hidden');
  document.getElementById('tagging-status-text').textContent = '正在连接 AI 服务...';
  document.getElementById('tagging-progress-count').textContent = '—';
  document.getElementById('progress-fill').style.width = '0%';
  document.getElementById('tagging-current').textContent = '';
  document.getElementById('tagging-start-btn').disabled = true;

  try {
    const body = ids.length ? { asset_ids: ids } : {};
    const r = await fetch(`${TAGGING_API}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    _taggingJobId = data.job_id;
    renderTaggingProgress(data);
    setTaggingButtonStop();
    // 启动 SSE 监听
    connectTaggingSSE(data.job_id);
  } catch (e) {
    toast('AI 打标启动失败: ' + e.message, 'error');
    hideTaggingProgress();
    resetTaggingButton();
  }
}

function connectTaggingSSE(jobId) {
  if (_taggingSource) { _taggingSource.close(); }
  _taggingSource = new EventSource(`${TAGGING_API}/status/${jobId}/stream`);

  _taggingSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === 'heartbeat') return;
      renderTaggingProgress(data);
      if (data.type === 'complete') {
        toast(`AI 打标完成: ${data.done} 成功${data.failed ? `, ${data.failed} 失败` : ''}`, data.failed ? 'error' : 'success');
        finishTagging();
      } else if (data.type === 'failed') {
        toast('AI 打标任务失败: ' + (data.error || '未知错误'), 'error');
        finishTagging();
      } else if (data.type === 'cancelled') {
        toast(`AI 打标已取消 — 本次已处理 ${(data.done || 0) + (data.failed || 0)}/${data.total || 0}`, 'info');
        finishTagging();
      }
    } catch (_) { /* ignore parse errors */ }
  };

  _taggingSource.onerror = () => {
    // SSE 断连，回退到轮询
    if (_taggingSource) { _taggingSource.close(); _taggingSource = null; }
    pollTaggingStatus(jobId);
  };
}

async function pollTaggingStatus(jobId) {
  try {
    const r = await fetch(`${TAGGING_API}/status/${jobId}`);
    if (!r.ok) { finishTagging(); return; }
    const data = await r.json();
    renderTaggingProgress(data);
    if (['completed', 'failed', 'cancelled'].includes(data.status)) {
      if (data.status === 'completed') {
        toast(`AI 打标完成: ${data.done} 成功${data.failed ? `, ${data.failed} 失败` : ''}`, data.failed ? 'error' : 'success');
      } else if (data.status === 'cancelled') {
        toast(`AI 打标已取消 — 本次已处理 ${(data.done || 0) + (data.failed || 0)}/${data.total || 0}`, 'info');
      }
      finishTagging();
      return;
    }
    // 继续轮询
    _taggingSource = null; // clear SSE ref
    _taggingPollTimer = setTimeout(() => pollTaggingStatus(jobId), 2000);
  } catch (e) {
    finishTagging();
  }
}

function renderTaggingProgress(data) {
  document.getElementById('tagging-progress').classList.remove('hidden');

  // publish 事件用 "type", get_job_status 用 "status" — 归一化
  const state = data.status || data.type || '';
  const total = data.total || 0;
  const done = (data.done || 0) + (data.failed || 0);
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  document.getElementById('tagging-progress-count').textContent = `${done}/${total}`;
  document.getElementById('progress-fill').style.width = `${pct}%`;

  const statusText = document.getElementById('tagging-status-text');
  const currentEl = document.getElementById('tagging-current');

  if (state === 'completed' || state === 'complete') {
    statusText.textContent = `✅ 打标完成 — ${data.done || 0} 成功, ${data.failed || 0} 失败`;
    currentEl.textContent = '';
  } else if (state === 'failed') {
    statusText.textContent = `❌ 打标失败: ${data.error || ''}`;
    currentEl.textContent = '';
  } else if (state === 'cancelled') {
    statusText.textContent = `⏹ 已取消 — 本次已处理 ${done}/${total}`;
    currentEl.textContent = '';
  } else if (state === 'channel') {
    // 通道级进度: 当前素材第 N/4 通道
    const ch = data.channel || 0;
    const chTotal = data.channel_total || 4;
    const assetNo = data.current_asset_no || '';
    statusText.textContent = `🤖 AI 打标进行中 — ${assetNo} (第 ${ch}/${chTotal} 通道: ${data.channel_name || ''})`;
    currentEl.textContent = data.msg ? `当前: ${data.msg}` : '';
  } else {
    statusText.textContent = '🤖 AI 打标进行中...';
    currentEl.textContent = data.current_asset_no ? `当前: ${data.current_asset_no}` : '';
  }
}

function hideTaggingProgress() {
  document.getElementById('tagging-progress').classList.add('hidden');
  resetTaggingButton();
}

function finishTagging() {
  _taggingJobId = null;
  if (_taggingSource) { _taggingSource.close(); _taggingSource = null; }
  if (_taggingPollTimer) { clearTimeout(_taggingPollTimer); _taggingPollTimer = null; }
  resetTaggingButton();
  loadTaggingCount();
  loadAssets();
  // 3 秒后自动隐藏进度条
  setTimeout(() => {
    if (!_taggingJobId) hideTaggingProgress();
  }, 5000);
}

async function cancelTagging() {
  if (!_taggingJobId) return;
  if (!confirm('确认取消 AI 打标任务？')) return;
  const jobId = _taggingJobId;
  // 不立即隐藏进度条 — 保留本次已处理数量, 等 SSE cancelled 事件 (带 done/failed) 到达后再收起
  document.getElementById('tagging-status-text').textContent = '⏹ 正在取消...';
  document.getElementById('tagging-current').textContent = '';
  try {
    await fetch(`${TAGGING_API}/cancel/${jobId}`, { method: 'POST' });
  } catch (_) {}
  // SSE 已断开时兜底轮询终态; SSE 正常时等 cancelled 事件即可
  if (!_taggingSource) {
    _taggingPollTimer = setTimeout(() => pollTaggingStatus(jobId), 8000);
  }
}

async function loadTaggingCount() {
  try {
    const r = await fetch(`${TAGGING_API}/count`);
    if (!r.ok) return;
    const data = await r.json();
    document.getElementById('tagging-count').textContent =
      `AI: ${data.ai_tagged}/${data.total}`;
    document.getElementById('tagging-count').title =
      `AI 已打标 ${data.ai_tagged} / 共 ${data.total} 个素材`;
  } catch (_) {}
}

// ── Init ──
loadDimensions();
loadTaggingCount();
loadAssets();

// ── P线在线搜索 (2026-08-25): 与本地搜索独立的在线入口, 预览 → 勾选入库 ──
let _pexelsItems = [];
const _pexelsSelected = new Set();

// 外部 API 数据进 HTML 前转义 (Pexels 摄影师名/url 等)
function _pEsc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function togglePexelsPanel(force) {
  const p = document.getElementById('pexels-panel');
  const show = force !== undefined ? force : p.classList.contains('hidden');
  p.classList.toggle('hidden', !show);
  if (show) document.getElementById('pexels-query').focus();
}

let _pexelsPage = 1;
const _PEXELS_PER_PAGE = 40;

async function pexelsSearch(page) {
  const query = document.getElementById('pexels-query').value.trim();
  if (!query) { toast('请输入关键词', 'error'); return; }
  _pexelsPage = Math.max(1, page || 1);
  const orientation = document.getElementById('pexels-orientation').value || 'any';
  const status = document.getElementById('pexels-status');
  status.textContent = _pexelsPage > 1 ? `搜索中 (第 ${_pexelsPage} 页)…` : '搜索中…';
  try {
    const r = await fetch(`${API}/pexels/search`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, orientation, per_page: _PEXELS_PER_PAGE, page: _pexelsPage }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${r.status}`); }
    const data = await r.json();
    _pexelsItems = data.items || [];
    _pexelsSelected.clear();
    renderPexelsResults();
    const hasMore = _pexelsItems.length >= _PEXELS_PER_PAGE;  // 满页 = 大概率还有下一页
    status.textContent = `Pexels 返回 ${_pexelsItems.length} 条 · 第 ${_pexelsPage} 页`;
    renderPexelsPager(hasMore);
    document.getElementById('pexels-results').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) {
    status.textContent = '';
    toast('在线搜索失败: ' + e.message, 'error');
  }
}

function renderPexelsPager(hasMore) {
  let pager = document.getElementById('pexels-pager');
  if (!pager) {
    pager = document.createElement('div');
    pager.id = 'pexels-pager';
    pager.style.cssText = 'display:flex;gap:0.5rem;align-items:center;justify-content:center;padding:0.4rem 0 0.2rem;';
    document.getElementById('pexels-results').after(pager);
  }
  pager.innerHTML = `
    <button class="tagging-btn" onclick="pexelsSearch(${_pexelsPage - 1})" ${_pexelsPage <= 1 ? 'disabled' : ''}>← 上一页</button>
    <span style="font-size:0.75rem;color:var(--text-muted,#8892a6);">第 ${_pexelsPage} 页</span>
    <button class="tagging-btn" onclick="pexelsSearch(${_pexelsPage + 1})" ${hasMore ? '' : 'disabled'}>下一页 →</button>`;
}

// 从 Pexels video_files 挑预览直链: 最小宽度的 mp4 (流量友好), 无则 null 回退静态图
function _pPreviewSrc(video) {
  const mp4s = ((video && video.video_files) || [])
    .filter(f => (f.file_type || '').includes('mp4') && f.link)
    .sort((a, b) => (a.width || 99999) - (b.width || 99999));
  return mp4s.length ? mp4s[0].link : null;
}
function _pPlay(card) { const v = card.querySelector('video'); if (v) v.play().catch(() => {}); }
function _pStop(card) {
  const v = card.querySelector('video');
  if (v) { v.pause(); try { v.currentTime = 0; } catch (_) {} }
}

function renderPexelsResults() {
  const grid = document.getElementById('pexels-results');
  if (!_pexelsItems.length) {
    grid.innerHTML = '<div class="pexels-empty">无结果 — 换个英文关键词试试</div>';
    updatePexelsImportBtn();
    return;
  }
  grid.innerHTML = _pexelsItems.map(it => {
    const blocked = it.in_library || it.disliked;
    const badge = it.in_library ? ' · 已入库' : (it.disliked ? ' · 已拉黑' : '');
    // 悬停播放预览 (2026-08-25): 有 mp4 直链 → <video> hover 播放; 否则回退静态图
    const src = _pPreviewSrc(it.video);
    const media = src
      ? `<video muted loop playsinline preload="none" disablepictureinpicture controlslist="nodownload noremoteplayback" poster="${_pEsc(it.image)}" src="${_pEsc(src)}"></video>`
      : `<a href="${_pEsc(it.url)}" target="_blank" rel="noopener" title="在 Pexels 打开"><img src="${_pEsc(it.image)}" loading="lazy" alt=""></a>`;
    return `
    <div class="pexels-card ${blocked ? 'pexels-dim' : ''}" onmouseenter="_pPlay(this)" onmouseleave="_pStop(this)" title="悬停播放预览${src ? '' : '（此素材无直链, 点击跳 Pexels）'}">
      <label class="pexels-check"><input type="checkbox" onchange="togglePexelsSelect(${it.pexels_id}, this.checked)" ${_pexelsSelected.has(it.pexels_id) ? 'checked' : ''} ${blocked ? 'disabled' : ''}></label>
      ${media}
      <a class="pexels-ext" href="${_pEsc(it.url)}" target="_blank" rel="noopener" title="在 Pexels 打开">↗</a>
      <div class="pexels-meta">
        <span>${it.duration || 0}s · ${it.width || '?'}×${it.height || '?'}${badge}</span>
        <span class="pexels-author">${_pEsc(it.photographer)}</span>
      </div>
    </div>`;
  }).join('');
  updatePexelsImportBtn();
}

function togglePexelsSelect(pid, on) {
  if (on) _pexelsSelected.add(pid); else _pexelsSelected.delete(pid);
  updatePexelsImportBtn();
}

function updatePexelsImportBtn() {
  const btn = document.getElementById('pexels-import-btn');
  btn.textContent = `📥 入库所选 (${_pexelsSelected.size})`;
  btn.disabled = _pexelsSelected.size === 0;
}

async function pexelsImport() {
  if (!_pexelsSelected.size) return;
  const items = _pexelsItems
    .filter(it => _pexelsSelected.has(it.pexels_id))
    .map(it => ({ video: it.video }));
  const btn = document.getElementById('pexels-import-btn');
  btn.disabled = true; btn.textContent = '⏳ 入库中…';
  try {
    const r = await fetch(`${API}/pexels/import`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: document.getElementById('pexels-query').value.trim(), items }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${r.status}`); }
    const data = await r.json();
    const reasons = [...new Set((data.skipped || []).map(s => s.reason))].join('; ');
    toast(`入库 ${data.imported.length} 条${data.skipped.length ? `，跳过 ${data.skipped.length}（${reasons}）` : ''} · 剩余配额 ${data.quota_remaining}`, 'success');
    await pexelsSearch(_pexelsPage);   // 刷新当前页预览 (已入库条目置灰)
    _assetPage = 0;
    await loadAssets();            // 本地素材列表同步
  } catch (e) {
    toast('入库失败: ' + e.message, 'error');
    updatePexelsImportBtn();
  }
}
