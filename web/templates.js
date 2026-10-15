    const API = '/api/articles';
    let selectedFile = null;

    // ── Toast ──
    function toast(msg, type = 'info') {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.className = `toast ${type} show`;
      setTimeout(() => el.classList.remove('show'), 4000);
    }

    // ─ API ──
    async function apiGet(path) {
      const r = await fetch(API + path);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.json();
    }
    async function apiDelete(path) {
      const r = await fetch(API + path, { method: 'DELETE' });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `${r.status}`);
      }
      return r.json();
    }

    // ── File Input ──
    const fileInput = document.getElementById('file-input');
    const uploadArea = document.getElementById('upload-area');
    const fileNameEl = document.getElementById('file-name');
    const btnUpload = document.getElementById('btn-upload');

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length) handleFile(e.target.files[0]);
    });

    // Drag & drop
    uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
    uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
    uploadArea.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadArea.classList.remove('dragover');
      if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });

    function handleFile(file) {
      if (!file.name.endsWith('.txt')) {
        toast('请选择 .txt 文件', 'error');
        return;
      }
      selectedFile = file;
      fileNameEl.textContent = file.name;
      fileNameEl.classList.remove('hidden');
      btnUpload.disabled = false;
      // Auto-fill name from filename
      const nameInput = document.getElementById('template-name');
      if (!nameInput.value) {
        nameInput.value = file.name.replace(/\.txt$/i, '').replace(/[^a-zA-Z0-9_\-]/g, '_');
      }
    }

    // ── Upload ──
    async function uploadTemplate() {
      const name = document.getElementById('template-name').value.trim();
      if (!name) { toast('请输入模板名称', 'error'); return; }
      if (!selectedFile) { toast('请选择文件', 'error'); return; }

      const spinner = document.getElementById('spinner-upload');
      btnUpload.disabled = true;
      spinner.classList.remove('hidden');

      const formData = new FormData();
      formData.append('name', name);
      formData.append('file', selectedFile);

      try {
        const r = await fetch(`${API}/prompt-templates/upload`, { method: 'POST', body: formData });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `${r.status}`);
        }
        const result = await r.json();
        toast(`模板 "${result.name}" 上传成功`, 'success');
        // Reset
        selectedFile = null;
        fileNameEl.classList.add('hidden');
        document.getElementById('template-name').value = '';
        fileInput.value = '';
        btnUpload.disabled = true;
        // Refresh list
        await loadTemplates();
      } catch (e) {
        toast('上传失败: ' + e.message, 'error');
        btnUpload.disabled = false;
      } finally {
        spinner.classList.add('hidden');
      }
    }

    // ── Load Templates ──
    async function loadTemplates() {
      const el = document.getElementById('template-list');
      try {
        const templates = await apiGet('/prompt-templates');
        if (!templates.length) {
          el.innerHTML = '<div class="empty"><div class="icon">📭</div><p>暂无模板</p></div>';
          return;
        }
        el.innerHTML = templates.map(t => {
          const sizeKB = (t.size / 1024).toFixed(1);
          const isBuiltin = t.id === 'laochen_default';
          return `
            <div class="template-item">
              <div class="t-icon">📄</div>
              <div class="t-info">
                <div class="t-name">${t.name}</div>
                <div class="t-meta">${sizeKB} KB${isBuiltin ? ' · 内置模板' : ''}</div>
              </div>
              <div class="t-actions">
                ${!isBuiltin ? `<button class="btn btn-warning btn-sm" onclick="showRename('${t.id}')">改名</button><button class="btn btn-danger btn-sm" onclick="deleteTemplate('${t.id}')">删除</button>` : ''}
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        el.innerHTML = `<div class="empty"><div class="icon">️</div><p>加载失败: ${e.message}</p></div>`;
      }
    }

    // ── Rename ──
    function showRename(id) {
      document.getElementById('rename-old').textContent = id;
      document.getElementById('rename-new').value = id;
      document.getElementById('rename-overlay').classList.add('show');
      setTimeout(() => document.getElementById('rename-new').focus(), 100);
    }
    function closeRename() {
      document.getElementById('rename-overlay').classList.remove('show');
    }
    async function doRename() {
      const oldId = document.getElementById('rename-old').textContent;
      const newName = document.getElementById('rename-new').value.trim();
      if (!newName) { toast('请输入新名称', 'error'); return; }
      try {
        const r = await fetch(`${API}/prompt-templates/${oldId}?new_name=${encodeURIComponent(newName)}`, { method: 'PUT' });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.detail || `${r.status}`);
        }
        toast(`模板已重命名为 "${newName}"`, 'success');
        closeRename();
        await loadTemplates();
      } catch (e) {
        toast('改名失败: ' + e.message, 'error');
      }
    }

    // ── Delete ──
    async function deleteTemplate(id) {
      if (!confirm(`确认删除模板 "${id}"？`)) return;
      try {
        await apiDelete(`/prompt-templates/${id}`);
        toast(`模板 "${id}" 已删除`, 'success');
        await loadTemplates();
      } catch (e) {
        toast('删除失败: ' + e.message, 'error');
      }
    }

    // ── Init ──
    loadTemplates();
