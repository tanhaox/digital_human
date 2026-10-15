    // ── state ──
    const state = {
      audioItems: [],
      selectedAudioPaths: new Set(),
      pendingStoryboardFiles: [],
      uploadedStoryboardPaths: [],
      videoId: null,
    };

    function setStatus(id, msg, level) {
      const el = document.getElementById(id);
      el.textContent = msg;
      el.className = 'status' + (level ? ' ' + level : '');
    }

    // ── 步骤 1: 加载候选音频 ──
    async function loadEligibleAudio() {
      setStatus('status-step1', '加载中...');
      try {
        const r = await fetch('/api/dhv/eligible-audio?limit=50');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        state.audioItems = data.items;
        document.getElementById('badge-audio-count').textContent = `${data.items.length} 项`;
        document.getElementById('badge-audio-total').textContent = `${data.total_duration_sec.toFixed(1)}s 总时长`;
        renderAudioPickList();
        setStatus('status-step1', `已加载 ${data.items.length} 项候选`, 'success');
      } catch (exc) {
        setStatus('status-step1', '加载失败: ' + exc.message, 'error');
      }
    }

    function renderAudioPickList() {
      const root = document.getElementById('audio-pick-list');
      if (!state.audioItems.length) {
        root.innerHTML = '<div class="empty-state">没有候选 wav。请先到 <a href="/web/index.html" style="color:#60a5fa;">主页面</a> 生成音频。</div>';
        return;
      }
      root.innerHTML = '';
      state.audioItems.forEach((item, idx) => {
        const div = document.createElement('div');
        div.className = 'audio-pick-item' + (state.selectedAudioPaths.has(item.path) ? ' selected' : '');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = state.selectedAudioPaths.has(item.path);
        cb.onchange = () => {
          if (cb.checked) state.selectedAudioPaths.add(item.path);
          else state.selectedAudioPaths.delete(item.path);
          renderAudioPickList();
          updateStep1Summary();
        };
        const label = document.createElement('div');
        label.className = 'label';
        label.textContent = item.label;
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = `${item.duration_sec ? item.duration_sec.toFixed(1) + 's' : '?'}`;
        div.appendChild(cb); div.appendChild(label); div.appendChild(meta);
        root.appendChild(div);
      });
      updateStep1Summary();
    }

    function updateStep1Summary() {
      const total = state.audioItems
        .filter(a => state.selectedAudioPaths.has(a.path))
        .reduce((s, a) => s + (a.duration_sec || 0), 0);
      document.getElementById('selected-audio-summary').textContent =
        `${state.selectedAudioPaths.size} 项 / ${total.toFixed(1)}s`;
      document.getElementById('btn-step2').disabled = total < 5.0;
      if (total < 5.0 && state.selectedAudioPaths.size > 0) {
        document.getElementById('selected-audio-summary').textContent += ' (低于 5s 阈值)';
      }
    }

    function goStep2() {
      document.getElementById('f-storyboard').scrollIntoView({ behavior: 'smooth' });
    }

    // ── 步骤 2: 创建任务 + 上传分镜 ──
    document.getElementById('f-storyboard').addEventListener('change', (e) => {
      state.pendingStoryboardFiles = Array.from(e.target.files);
      renderStoryboardPreview();
    });

    function renderStoryboardPreview() {
      const root = document.getElementById('storyboard-preview');
      if (!state.pendingStoryboardFiles.length) {
        root.innerHTML = '<div class="empty-state">尚未上传</div>';
        return;
      }
      root.innerHTML = '';
      state.pendingStoryboardFiles.forEach((f, i) => {
        const card = document.createElement('div');
        card.className = 'storyboard-card';
        const img = document.createElement('img');
        img.src = URL.createObjectURL(f);
        const fn = document.createElement('div');
        fn.className = 'filename';
        fn.textContent = f.name;
        card.appendChild(img); card.appendChild(fn);
        root.appendChild(card);
      });
    }

    async function createAndUpload() {
      if (!state.selectedAudioPaths.size) {
        setStatus('status-step2', '请先选择音频', 'error'); return;
      }
      if (!state.pendingStoryboardFiles.length) {
        setStatus('status-step2', '请先上传分镜图', 'error'); return;
      }
      setStatus('status-step2', '创建任务中...');

      // 1. POST /api/dhv/videos
      const body = {
        audio_source_paths: Array.from(state.selectedAudioPaths),
        storyboard_prompts: [],
        target_duration_sec: parseFloat(document.getElementById('f-duration').value),
        fps: parseInt(document.getElementById('f-fps').value),
        width: parseInt(document.getElementById('f-width').value),
        height: parseInt(document.getElementById('f-height').value),
        seed: document.getElementById('f-seed').value ? parseInt(document.getElementById('f-seed').value) : null,
      };
      let resp = await fetch('/api/dhv/videos', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) { setStatus('status-step2', '创建失败: ' + resp.status, 'error'); return; }
      const v = await resp.json();
      state.videoId = v.id;
      document.getElementById('video-id-display').textContent = 'id=' + v.id.slice(0, 8);
      document.getElementById('video-id-display').className = 'badge online';

      // 2. upload-storyboard
      const fd = new FormData();
      state.pendingStoryboardFiles.forEach(f => fd.append('files', f));
      resp = await fetch(`/api/dhv/videos/${v.id}/upload-storyboard`, {
        method: 'POST', body: fd,
      });
      if (!resp.ok) {
        setStatus('status-step2', '上传分镜失败: ' + resp.status, 'error');
        return;
      }
      const up = await resp.json();
      state.uploadedStoryboardPaths = up.uploaded_paths;
      setStatus('status-step2',
        `已创建任务, 上传 ${up.uploaded_paths.length} 张分镜图, 准备生成`,
        'success');
      document.getElementById('btn-generate').disabled = false;
    }

    // ── 步骤 3: 生成 ──
    async function generateVideo() {
      if (!state.videoId) { setStatus('status-step3', '请先创建任务', 'error'); return; }
      setStatus('status-step3', '提交中... (聚合音频 → ComfyUI /prompt → 轮询 → ffprobe 校验)');
      document.getElementById('btn-generate').disabled = true;

      const resp = await fetch(`/api/dhv/videos/${state.videoId}/generate`, { method: 'POST' });
      if (!resp.ok) {
        setStatus('status-step3', `提交失败: ${resp.status}`, 'error');
        document.getElementById('btn-generate').disabled = false;
        return;
      }
      const data = await resp.json();
      document.getElementById('badge-status').textContent = data.status;
      document.getElementById('badge-status').className = 'badge ' + data.status;

      if (data.status === 'completed') {
        setStatus('status-step3',
          `✓ 完成 ${data.elapsed_sec.toFixed(1)}s — duration=${data.duration_actual}s, fps=${data.fps_actual}, frame_count=${data.frame_count}, audio=${data.has_audio_stream}`,
          'success');
        renderResult(data);
        loadHistory();
      } else {
        setStatus('status-step3', `✗ ${data.status}: ${data.error || ''}`, 'error');
        renderResult(data);
      }
      document.getElementById('btn-generate').disabled = false;
    }

    async function pollStatus() {
      if (!state.videoId) return;
      try {
        const r = await fetch(`/api/dhv/videos/${state.videoId}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const v = await r.json();
        document.getElementById('badge-status').textContent = v.status;
        document.getElementById('badge-status').className = 'badge ' + v.status;
        setStatus('status-step3', `status=${v.status}, prompt_id=${v.prompt_id || '-'}`);
      } catch (exc) {
        setStatus('status-step3', '轮询失败: ' + exc.message, 'error');
      }
    }

    function renderResult(data) {
      const root = document.getElementById('result-display');
      root.innerHTML = '';
      if (data.validation_issues && data.validation_issues.length) {
        const div = document.createElement('div');
        div.className = 'issues';
        div.innerHTML = '<strong>校验失败:</strong><ul>' +
          data.validation_issues.map(i => `<li>${i}</li>`).join('') + '</ul>';
        root.appendChild(div);
      }
      if (data.output_video_path) {
        const wrap = document.createElement('div');
        wrap.style.marginTop = '0.5rem';
        const a = document.createElement('a');
        a.href = `/api/dhv/videos/${state.videoId}/download`;
        a.target = '_blank';
        a.textContent = '⬇ 下载 mp4';
        a.style.cssText = 'color:#60a5fa;text-decoration:none;';
        wrap.appendChild(a);
        root.appendChild(wrap);
      }
    }

    // ── 步骤 4: 历史 ──
    async function loadHistory() {
      let list = [];
      try {
        const r = await fetch('/api/dhv/videos?limit=20');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        list = await r.json();
      } catch (exc) {
        setStatus('status-step4', '历史加载失败: ' + exc.message, 'error');
        return;
      }
      const root = document.getElementById('video-history');
      if (!list.length) {
        root.innerHTML = '<div class="empty-state">暂无历史任务</div>';
        return;
      }
      root.innerHTML = '';
      list.forEach(v => {
        const card = document.createElement('div');
        card.className = 'video-card';
        const info = document.createElement('div');
        info.className = 'info';
        info.innerHTML = `
          <div><span class="id">${v.id.slice(0, 8)}</span>
            <span class="badge ${v.status}">${v.status}</span>
            ${v.has_audio_stream ? '<span class="badge completed">🎵 audio</span>' : ''}
          </div>
          <div style="color:#94a3b8;font-size:0.78rem;margin-top:0.3rem;">
            ${v.target_duration_sec}s @ ${v.fps}fps ${v.width}×${v.height} |
            agg=${v.aggregated_duration_sec ? v.aggregated_duration_sec.toFixed(1) + 's' : '-'} |
            actual=${v.duration_actual ? v.duration_actual.toFixed(1) + 's' : '-'} |
            frames=${v.frame_count ?? '-'} |
            seed=${v.seed ?? 'rand'}
          </div>
          ${v.error_message ? `<div style="color:#fca5a5;font-size:0.75rem;margin-top:0.2rem;">${v.error_message}</div>` : ''}
        `;
        card.appendChild(info);
        const actions = document.createElement('div');
        actions.style.cssText = 'display:flex;gap:0.4rem;align-items:center;';
        if (v.output_video_path && v.status === 'completed') {
          const dl = document.createElement('a');
          dl.href = `/api/dhv/videos/${v.id}/download`;
          dl.target = '_blank';
          dl.textContent = '⬇ mp4';
          dl.style.cssText = 'color:#60a5fa;font-size:0.85rem;text-decoration:none;';
          actions.appendChild(dl);
        }
        const del = document.createElement('button');
        del.className = 'danger';
        del.style.cssText = 'padding:0.3rem 0.6rem;font-size:0.75rem;';
        del.textContent = '删除';
        del.onclick = async () => {
          if (!confirm('删除任务 ' + v.id.slice(0, 8) + '?')) return;
          try {
            const r = await fetch(`/api/dhv/videos/${v.id}`, { method: 'DELETE' });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            loadHistory();
          } catch (exc) { setStatus('status-step4', '删除失败: ' + exc.message, 'error'); }
        };
        actions.appendChild(del);
        card.appendChild(actions);
        root.appendChild(card);
      });
    }

    // ── init ──
    loadEligibleAudio();
    loadHistory();
