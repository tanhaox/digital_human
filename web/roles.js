/* roles.js — 角色形象管理：多组多机位 */

const CAM_LABELS = ['正面半身', '左侧45度', '右侧45度', '正面特写'];

// ── Load & Render ──
async function loadRoles() {
  const grid = document.getElementById('grid');
  try {
    const r = await fetch('/api/roles');
    if (!r.ok) { grid.innerHTML = '<div class="empty-state">加载失败</div>'; return; }
    const roles = await r.json();
    if (!roles.length) { grid.innerHTML = '<div class="empty-state">尚无角色，点击"+ 新建角色"开始</div>'; return; }
    grid.innerHTML = roles.map(renderCard).join('');
  } catch (e) {
    grid.innerHTML = `<div class="empty-state">异常: ${e}</div>`;
  }
}

function renderCard(r) {
  const refUrl = `/api/roles/${r.id}/reference-image`;
  const groups = r.view_groups || [];
  const groupsHtml = groups.length
    ? groups.map((g, gi) => renderGroup(r.id, g, gi)).join('')
    : '<div style="color:var(--text-ghost);font-size:0.8rem;padding:0.5rem 0;">暂无形象组，点击下方按钮上传</div>';

  return `
    <div class="card">
      <div class="card-header">
        <img class="ref-thumb" src="${refUrl}" onerror="this.style.opacity=0.3">
        <div class="info">
          <div class="name">${esc(r.name)}</div>
          <div class="desc">${esc(r.description || '—')}</div>
        </div>
      </div>
      <div class="groups">${groupsHtml}</div>
      <div class="card-actions">
        <button class="btn btn-primary btn-sm" onclick="openGroupModal('${r.id}')">+ 上传形象组</button>
        <button class="btn btn-secondary btn-sm" onclick="openEditModal('${r.id}')">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="deleteRole('${r.id}')">删除</button>
      </div>
    </div>`;
}

function renderGroup(roleId, group, idx) {
  const cams = group.cameras || {};
  const camSlots = [1,2,3,4].map(i => {
    const path = cams[String(i)];
    if (path) {
      return `<div class="cam-slot"><img src="/api/roles/${roleId}/groups/${idx}/cameras/${i}" onerror="this.parentElement.classList.add('empty');this.remove();"><div class="cam-label">${CAM_LABELS[i-1]}</div></div>`;
    }
    return `<div class="cam-slot empty">${CAM_LABELS[i-1]}</div>`;
  }).join('');

  return `
    <div class="group-block">
      <div class="group-header">
        <span><span class="group-name">${esc(group.name)}</span><span class="group-idx">#${idx}</span></span>
        <button class="btn btn-danger btn-sm" onclick="deleteGroup('${roleId}', ${idx})" title="删除此组">删除</button>
      </div>
      <div class="cameras">${camSlots}</div>
    </div>`;
}

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ── Create ─
function openCreateModal() {
  document.getElementById('create-modal').classList.add('active');
  document.getElementById('f-name').value = '';
  document.getElementById('f-description').value = '';
  document.getElementById('f-reference').value = '';
  document.getElementById('create-status').textContent = '';
}
function closeCreateModal() { document.getElementById('create-modal').classList.remove('active'); }

async function submitCreate() {
  const status = document.getElementById('create-status');
  const name = document.getElementById('f-name').value.trim();
  const description = document.getElementById('f-description').value.trim();
  const file = document.getElementById('f-reference').files[0];
  if (!name) { status.textContent = '请填角色名'; status.className = 'status error'; return; }
  if (!file) { status.textContent = '请选参考图'; status.className = 'status error'; return; }
  const fd = new FormData();
  fd.append('name', name);
  fd.append('description', description);
  fd.append('reference', file);
  status.textContent = '上传中...'; status.className = 'status';
  try {
    const r = await fetch('/api/roles', { method: 'POST', body: fd });
    if (!r.ok) { const t = await r.text(); status.textContent = `失败: ${r.status} ${t}`; status.className = 'status error'; return; }
    status.textContent = '创建成功'; status.className = 'status success';
    setTimeout(() => { closeCreateModal(); loadRoles(); }, 500);
  } catch (e) { status.textContent = '异常: ' + e; status.className = 'status error'; }
}

// ── Edit ──
async function openEditModal(id) {
  const r = await fetch(`/api/roles/${id}`);
  if (!r.ok) { alert('加载失败'); return; }
  const role = await r.json();
  document.getElementById('edit-id').value = role.id;
  document.getElementById('edit-name').value = role.name;
  document.getElementById('edit-description').value = role.description || '';
  document.getElementById('edit-status').textContent = '';
  document.getElementById('edit-modal').classList.add('active');
}
function closeEditModal() { document.getElementById('edit-modal').classList.remove('active'); }

async function submitEdit() {
  const status = document.getElementById('edit-status');
  const id = document.getElementById('edit-id').value;
  const data = {
    name: document.getElementById('edit-name').value.trim(),
    description: document.getElementById('edit-description').value.trim(),
  };
  try {
    const r = await fetch(`/api/roles/${id}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
    if (!r.ok) { status.textContent = `失败: ${r.status}`; status.className = 'status error'; return; }
    status.textContent = '已保存'; status.className = 'status success';
    setTimeout(() => { closeEditModal(); loadRoles(); }, 400);
  } catch (e) { status.textContent = '异常: ' + e; status.className = 'status error'; }
}

// ── Group Upload ──
function openGroupModal(roleId) {
  document.getElementById('group-role-id').value = roleId;
  document.getElementById('group-name').value = '';
  [1,2,3,4].forEach(i => {
    document.getElementById(`cam-${i}`).value = '';
    const prev = document.getElementById(`preview-${i}`);
    prev.style.display = 'none'; prev.src = '';
  });
  document.getElementById('group-status').textContent = '';
  document.getElementById('group-modal').classList.add('active');
}
function closeGroupModal() { document.getElementById('group-modal').classList.remove('active'); }

function previewCam(input, idx) {
  const prev = document.getElementById(`preview-${idx}`);
  if (input.files[0]) {
    prev.src = URL.createObjectURL(input.files[0]);
    prev.style.display = 'block';
  } else {
    prev.style.display = 'none';
  }
}

async function submitGroup() {
  const status = document.getElementById('group-status');
  const roleId = document.getElementById('group-role-id').value;
  const name = document.getElementById('group-name').value.trim();
  if (!name) { status.textContent = '请填组名'; status.className = 'status error'; return; }

  const files = [1,2,3,4].map(i => document.getElementById(`cam-${i}`).files[0]);
  if (files.some(f => !f)) { status.textContent = '请选择全部 4 张机位图'; status.className = 'status error'; return; }

  const totalMB = (files.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(1);
  const fd = new FormData();
  fd.append('name', name);
  fd.append('cam1', files[0]);
  fd.append('cam2', files[1]);
  fd.append('cam3', files[2]);
  fd.append('cam4', files[3]);

  status.textContent = `上传中… (共 ${totalMB} MB，大文件可能需要 10-30 秒)`; status.className = 'status';
  document.getElementById('btn-upload-group').disabled = true;
  try {
    const r = await fetch(`/api/roles/${roleId}/groups`, { method: 'POST', body: fd });
    if (!r.ok) { const t = await r.text(); status.textContent = `失败: ${r.status} ${t.slice(0,200)}`; status.className = 'status error'; return; }
    status.textContent = '上传成功'; status.className = 'status success';
    setTimeout(() => { closeGroupModal(); loadRoles(); }, 500);
  } catch (e) {
    status.textContent = `网络异常: ${e.message}。如果文件过大(>50MB)，请先压缩图片再上传。`;
    status.className = 'status error';
  }
  finally { document.getElementById('btn-upload-group').disabled = false; }
}

async function deleteGroup(roleId, idx) {
  if (!confirm(`确认删除第 ${idx} 组形象？`)) return;
  const r = await fetch(`/api/roles/${roleId}/groups/${idx}`, { method: 'DELETE' });
  if (!r.ok) { alert(`删除失败: ${r.status}`); return; }
  loadRoles();
}

// ── Delete Role ──
async function deleteRole(id) {
  if (!confirm('确认删除角色？(磁盘文件保留)')) return;
  const r = await fetch(`/api/roles/${id}`, { method: 'DELETE' });
  if (!r.ok) { alert(`删除失败: ${r.status}`); return; }
  loadRoles();
}

// ── Init ──
loadRoles();
