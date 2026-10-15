    const API = '/api';

    // Preset definitions
    const PRESETS = {
      default:     { speed: 1.0, pitch: 0,   volume: 1.0, bass_gain: 0, presence_gain: 0, air_gain: 0, temperature: 0.3, top_p: 0.7, repetition_penalty: 1.5, seed: 42, label: '默认' },
      male:        { speed: 1.0, pitch: -3,  volume: 1.0, bass_gain: 4, presence_gain: 0, air_gain: -2, temperature: 0.3, top_p: 0.7, repetition_penalty: 1.5, seed: 42, label: '男声' },
      female:      { speed: 1.05,pitch: 5,   volume: 1.0, bass_gain: -3, presence_gain: 2, air_gain: 4, temperature: 0.3, top_p: 0.7, repetition_penalty: 1.5, seed: 42, label: '女声' },
      deep_male:   { speed: 0.9, pitch: -6,  volume: 1.1, bass_gain: 8, presence_gain: -2, air_gain: -4, temperature: 0.3, top_p: 0.7, repetition_penalty: 1.5, seed: 42, label: '深沉男声' },
      loli:        { speed: 1.15,pitch: 9,   volume: 0.9, bass_gain: -6, presence_gain: 3, air_gain: 6, temperature: 0.3, top_p: 0.7, repetition_penalty: 1.5, seed: 42, label: '萝莉' },
      fast:        { speed: 1.5, pitch: 0,   volume: 1.0, bass_gain: 0, presence_gain: 0, air_gain: 0, temperature: 0.3, top_p: 0.7, repetition_penalty: 1.5, seed: 42, label: '超快播报' },
    };

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------
    function status(msg, type) {
      var el = document.getElementById('global-status');
      el.textContent = msg;
      el.className = 'status ' + (type || 'info');
    }

    function modalStatus(msg, type) {
      var el = document.getElementById('modal-status');
      el.textContent = msg;
      el.style.display = 'block';
      el.className = 'status ' + (type || 'info');
    }

    function escapeHtml(text) {
      return (text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function updateParamLabel(spanId, val) {
      var span = document.getElementById(spanId);
      if (span) span.textContent = parseFloat(val).toFixed(2).replace(/\.?0+$/, '');
    }

    function updateInlineVal(voiceId, key, val) {
      var span = document.getElementById('tp-' + key + '-' + voiceId);
      if (span) {
        var n = parseFloat(val);
        if (key === 'speed' || key === 'top_p') span.textContent = n.toFixed(2);
        else span.textContent = n.toFixed(1).replace(/\.?0+$/, '');
      }
    }

    // Modal slider ↔ number input bidirectional sync
    function onModalSliderInput(labelId, slider, numId) {
      updateParamLabel(labelId, slider.value);
      var num = document.getElementById(numId);
      if (num) num.value = slider.value;
    }
    function onModalNumInput(sliderId, num, labelId) {
      var slider = document.getElementById(sliderId);
      if (!slider) return;
      var v = num.value;
      if (v === '' || isNaN(v)) return;
      var clamped = Math.min(parseFloat(slider.max), Math.max(parseFloat(slider.min), parseFloat(v)));
      num.value = clamped;
      slider.value = clamped;
      updateParamLabel(labelId, clamped);
    }

    async function api(method, path, body) {
      var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      var resp = await fetch(API + path, opts);
      if (!resp.ok) {
        var data = await resp.json().catch(function() { return {}; });
        var msg = data.detail;
        if (typeof msg !== 'string') {
          if (Array.isArray(msg)) msg = msg.map(function(e) { return e.msg || JSON.stringify(e); }).join('; ');
          else if (msg) msg = JSON.stringify(msg);
          else msg = 'HTTP ' + resp.status;
        }
        throw new Error(msg);
      }
      if (resp.status === 204) return null;
      var ct = resp.headers.get('content-type') || '';
      if (ct.startsWith('audio/')) return resp;
      return resp.json().catch(function() { return {}; });
    }

    // -----------------------------------------------------------------------
    // Read params from modal form fields
    // -----------------------------------------------------------------------
    function readModalParams() {
      var p = {};
      p.speed = parseFloat(document.getElementById('f-speed').value);
      p.pitch = parseFloat(document.getElementById('f-pitch').value);
      p.volume = parseFloat(document.getElementById('f-volume').value);

      var bg = parseFloat(document.getElementById('f-bass_gain').value);
      var pg = parseFloat(document.getElementById('f-presence_gain').value);
      var ag = parseFloat(document.getElementById('f-air_gain').value);
      if (bg !== 0) p.bass_gain = bg;
      if (pg !== 0) p.presence_gain = pg;
      if (ag !== 0) p.air_gain = ag;

      var t = parseFloat(document.getElementById('f-temperature').value);
      var tp = parseFloat(document.getElementById('f-top_p').value);
      var rp = parseFloat(document.getElementById('f-repetition_penalty').value);
      if (t !== 0.3) p.temperature = t;
      if (tp !== 0.7) p.top_p = tp;
      if (rp !== 1.5) p.repetition_penalty = rp;

      var sd = document.getElementById('f-seed').value;
      if (sd !== '') p.seed = parseInt(sd, 10);

      var preset = document.getElementById('f-preset').value;
      if (preset) p.preset = preset;

      return p;
    }

    // -----------------------------------------------------------------------
    // Fill modal param fields from a params object
    // -----------------------------------------------------------------------
    function applyParamsToModal(params) {
      if (!params) params = {};
      setSlider('f-speed', params.speed != null ? params.speed : 1.0);
      setSlider('f-pitch', params.pitch != null ? params.pitch : 0);
      setSlider('f-volume', params.volume != null ? params.volume : 1.0);
      setSlider('f-bass_gain', params.bass_gain != null ? params.bass_gain : 0);
      setSlider('f-presence_gain', params.presence_gain != null ? params.presence_gain : 0);
      setSlider('f-air_gain', params.air_gain != null ? params.air_gain : 0);
      setSlider('f-temperature', params.temperature != null ? params.temperature : 0.3);
      setSlider('f-top_p', params.top_p != null ? params.top_p : 0.7);
      setSlider('f-repetition_penalty', params.repetition_penalty != null ? params.repetition_penalty : 1.5);

      var seedEl = document.getElementById('f-seed');
      if (params.seed != null) {
        seedEl.value = params.seed;
      } else {
        seedEl.value = '';
      }

      var sel = document.getElementById('f-preset');
      if (params.preset && PRESETS[params.preset]) {
        sel.value = params.preset;
      } else {
        sel.value = '';
      }
    }

    function setSlider(id, val) {
      var el = document.getElementById(id);
      if (!el) return;
      el.value = val;
      var inputEvent = new Event('input', { bubbles: true });
      el.dispatchEvent(inputEvent);
      // Sync number input if it exists
      var numId = id.replace('f-', 'n-');
      var num = document.getElementById(numId);
      if (num) num.value = val;
    }

    function presetChanged() {
      var sel = document.getElementById('f-preset');
      var preset = sel.value;
      if (preset && PRESETS[preset]) {
        var p = PRESETS[preset];
        var modalAdv = document.getElementById('modal-adv');
        if (modalAdv) modalAdv.classList.add('show');
        setSlider('f-speed', p.speed);
        setSlider('f-pitch', p.pitch);
        setSlider('f-volume', p.volume);
        setSlider('f-bass_gain', p.bass_gain || 0);
        setSlider('f-presence_gain', p.presence_gain || 0);
        setSlider('f-air_gain', p.air_gain || 0);
        setSlider('f-temperature', p.temperature);
        setSlider('f-top_p', p.top_p);
        setSlider('f-repetition_penalty', p.repetition_penalty);
        var seedEl = document.getElementById('f-seed');
        if (seedEl) { seedEl.value = p.seed != null ? p.seed : ''; }
      }
    }

    function toggleAdvParams(id) {
      var el = document.getElementById(id);
      if (el) el.classList.toggle('show');
    }

    // -----------------------------------------------------------------------
    // Build param summary string
    // -----------------------------------------------------------------------
    function paramSummary(params) {
      if (!params) return '默认参数';
      var parts = [];
      if (params.speed != null && params.speed !== 1.0) parts.push('语速 ' + params.speed.toFixed(2) + 'x');
      if (params.pitch != null && params.pitch !== 0) parts.push('语调 ' + (params.pitch > 0 ? '+' : '') + params.pitch);
      if (params.volume != null && params.volume !== 1.0) parts.push('音量 ' + params.volume.toFixed(1) + 'x');
      if (params.bass_gain != null && params.bass_gain !== 0) parts.push('低沉' + (params.bass_gain > 0 ? '+' : '') + params.bass_gain + 'dB');
      if (params.presence_gain != null && params.presence_gain !== 0) parts.push('中气' + (params.presence_gain > 0 ? '+' : '') + params.presence_gain + 'dB');
      if (params.air_gain != null && params.air_gain !== 0) parts.push('明亮' + (params.air_gain > 0 ? '+' : '') + params.air_gain + 'dB');
      if (params.preset && PRESETS[params.preset]) parts.push('预设:' + PRESETS[params.preset].label);
      return parts.length ? parts.join(' | ') : '默认参数';
    }

    var voices = [];
    var hosts = [];
    var testStates = {};

    // -----------------------------------------------------------------------
    // Data loading
    // -----------------------------------------------------------------------
    async function loadVoices() {
      try {
        voices = await api('GET', '/voices');
        renderVoices();
      } catch (e) {
        status('加载音色失败: ' + e.message, 'error');
      }
    }

    async function loadHosts() {
      try {
        hosts = await api('GET', '/hosts');
        var sel = document.getElementById('f-host');
        sel.innerHTML = '<option value="">-- 不关联 --</option>';
        hosts.forEach(function(h) {
          var opt = document.createElement('option');
          opt.value = h.id;
          opt.textContent = h.name;
          sel.appendChild(opt);
        });
      } catch (e) {
        console.warn('加载主播列表失败', e);
      }
    }

    // -----------------------------------------------------------------------
    // Render voice list
    // -----------------------------------------------------------------------
    function renderVoices() {
      var container = document.getElementById('voice-list');
      if (!voices.length) {
        container.innerHTML = '<div class="empty">暂无音色，点击上方按钮添加</div>';
        return;
      }
      container.innerHTML = '';
      voices.forEach(function(v) {
        var hostName = '';
        for (var hi = 0; hi < hosts.length; hi++) { if (hosts[hi].id === v.host_id) hostName = hosts[hi].name; }
        var p = v.params || {};
        var div = document.createElement('div');
        div.className = 'voice-item';
        var h = '';

        // Info
        h += '<div class="info">';
        h += '  <div class="name">' + escapeHtml(v.name) + '</div>';
        h += '  <div class="meta">后端: ' + v.backend;
        if (hostName) h += ' | 主播: ' + escapeHtml(hostName);
        if (v.reference_audio_path) h += ' | <span style="color:#6ee7b7;">参考音锚点: ✓</span>';
        h += '</div>';
        h += '  <div class="params-summary">' + paramSummary(p) + '</div>';

        // Test area
        h += '  <div class="test-area" data-voice-id="' + v.id + '">';
        h += '    <div class="test-row">';
        h += '      <input type="text" class="test-input" placeholder="输入测试文本..." value="大家好，欢迎收看老陈聊财经。">';
        h += '      <button class="small primary" style="background:#d97706;" onclick="toggleLongText(\'' + v.id + '\', this)">📝 长文本</button>';
        h += '      <button class="small secondary test-btn" onclick="testVoice(\'' + v.id + '\', this)">试听</button>';
        h += '      <button class="small" style="background:#7c3aed;color:white;" onclick="carnivalVoice(\'' + v.id + '\', this)">🎲 抽卡</button>';
        h += '      <button class="small secondary" onclick="toggleTestParams(\'' + v.id + '\')">参数</button>';
        h += '    </div>';

        // Long text area for ~300 char batch prototype
        h += '    <div class="long-text-area" id="long-text-' + v.id + '" style="display:none;margin-top:0.4rem;">';
        h += '      <textarea placeholder="输入 200-400 字的长文本（约 30 秒音频），用于抽卡选出统一声音锚点..." style="min-height:60px;font-size:0.85rem;">在当今这个信息爆炸的时代，财经资讯每天如潮水般涌来。很多朋友问我，老陈，这么多消息，哪些才是真正值得我们关注的？今天我就来给大家梳理一下最近的重要经济动态。首先，我们要看的是宏观政策的走向。货币政策方面，央行近期释放了明确信号，要精准发力，支持实体经济发展。这意味着什么呢？简单来说，就是市场上不会缺钱，但也不会大水漫灌。对于普通投资者来说，这是一个重要的参考信号。</textarea>';
        h += '    </div>';

        // Test params panel
        h += '    <div class="test-params" id="test-params-' + v.id + '">';
        h += '      <div class="preset-row">';
        h += '        <button class="small" onclick="applyTestPreset(\'' + v.id + '\', \'default\')">默认</button>';
        h += '        <button class="small" onclick="applyTestPreset(\'' + v.id + '\', \'male\')">男声</button>';
        h += '        <button class="small" onclick="applyTestPreset(\'' + v.id + '\', \'female\')">女声</button>';
        h += '        <button class="small" onclick="applyTestPreset(\'' + v.id + '\', \'deep_male\')">深沉</button>';
        h += '        <button class="small" onclick="applyTestPreset(\'' + v.id + '\', \'loli\')">萝莉</button>';
        h += '        <button class="small" onclick="applyTestPreset(\'' + v.id + '\', \'fast\')">快播</button>';
        h += '      </div>';
        h += '      <div class="param-slider"><label>语速</label><span class="rl">变慢</span><div class="range-wrap"><input type="range" min="0.5" max="2.0" step="0.05" value="' + (p.speed || 1.0) + '" oninput="updateInlineVal(\'' + v.id + '\',\'speed\',this.value)"><span class="rr">变快</span></div><span class="val" id="tp-speed-' + v.id + '">' + (p.speed || 1.0).toFixed(2) + '</span></div>';
        h += '      <div class="param-slider"><label>语调</label><span class="rl">低沉</span><div class="range-wrap"><input type="range" min="-12" max="12" step="0.5" value="' + (p.pitch || 0) + '" oninput="updateInlineVal(\'' + v.id + '\',\'pitch\',this.value)"><span class="rr">尖锐</span></div><span class="val" id="tp-pitch-' + v.id + '">' + (p.pitch || 0) + '</span></div>';
        h += '      <div class="param-slider"><label>低沉</label><span class="rl">轻薄</span><div class="range-wrap"><input type="range" min="-24" max="24" step="1" value="' + (p.bass_gain || 0) + '" oninput="updateInlineVal(\'' + v.id + '\',\'bass_gain\',this.value)"><span class="rr">厚重</span></div><span class="val" id="tp-bass_gain-' + v.id + '">' + (p.bass_gain || 0) + '</span></div>';
        h += '      <div class="param-slider"><label>中气</label><span class="rl">柔和</span><div class="range-wrap"><input type="range" min="-24" max="24" step="1" value="' + (p.presence_gain || 0) + '" oninput="updateInlineVal(\'' + v.id + '\',\'presence_gain\',this.value)"><span class="rr">有力</span></div><span class="val" id="tp-presence_gain-' + v.id + '">' + (p.presence_gain || 0) + '</span></div>';
        h += '      <div class="param-slider"><label>明亮</label><span class="rl">哑暗</span><div class="range-wrap"><input type="range" min="-24" max="24" step="1" value="' + (p.air_gain || 0) + '" oninput="updateInlineVal(\'' + v.id + '\',\'air_gain\',this.value)"><span class="rr">清脆</span></div><span class="val" id="tp-air_gain-' + v.id + '">' + (p.air_gain || 0) + '</span></div>';
        h += '      <div style="margin-top:0.2rem;"><span class="adv-toggle" onclick="toggleTestAdvParams(\'' + v.id + '\')">▶ 高级</span></div>';
        h += '      <div class="adv-params" id="tp-adv-' + v.id + '">';
        h += '        <div class="param-slider"><label>发挥程度</label><input type="range" min="0.1" max="1.5" step="0.1" value="' + (p.temperature || 0.7) + '" oninput="updateInlineVal(\'' + v.id + '\',\'temperature\',this.value)"><span class="val" id="tp-temperature-' + v.id + '">' + (p.temperature || 0.7).toFixed(1) + '</span></div>';
        h += '        <div class="param-slider"><label>丰富度</label><input type="range" min="0.1" max="1.0" step="0.05" value="' + (p.top_p || 0.7) + '" oninput="updateInlineVal(\'' + v.id + '\',\'top_p\',this.value)"><span class="val" id="tp-top_p-' + v.id + '">' + (p.top_p || 0.7).toFixed(2) + '</span></div>';
        h += '        <div class="param-slider"><label>避口癖</label><input type="range" min="1.0" max="2.0" step="0.1" value="' + (p.repetition_penalty || 1.5) + '" oninput="updateInlineVal(\'' + v.id + '\',\'repetition_penalty\',this.value)"><span class="val" id="tp-repetition_penalty-' + v.id + '">' + (p.repetition_penalty || 1.5).toFixed(1) + '</span></div>';
        h += '      </div>';
        h += '      <div class="param-slider" style="margin-top:0.4rem;"><label>种子</label><input type="number" id="tp-seed-' + v.id + '" min="0" max="2147483647" step="1" placeholder="留空=随机" value="' + (p.seed != null ? p.seed : '') + '" style="flex:1;max-width:8rem;"></div>';
        h += '      <div style="margin-top:0.5rem; display:flex; gap:0.4rem;">';
        h += '        <button class="small primary" onclick="saveAsNewVoice(\'' + v.id + '\')">另存为新音色</button>';
        h += '        <button class="small secondary" onclick="updateThisVoice(\'' + v.id + '\')">更新此音色</button>';
        h += '      </div>';
        h += '    </div>';

        // Result
        h += '    <div class="test-result" id="test-result-' + v.id + '" style="display:none;"></div>';
        h += '  </div></div>';

        // Actions
        h += '<div class="actions">';
        h += '  <button class="small primary" onclick="openEditModal(\'' + v.id + '\')">编辑</button>';
        h += '  <button class="small danger" onclick="deleteVoice(\'' + v.id + '\')">删除</button>';
        h += '</div>';

        div.innerHTML = h;
        container.appendChild(div);
      });

      // Restore expanded test params
      for (var id in testStates) {
        if (testStates[id] && testStates[id].expanded) {
          var el = document.getElementById('test-params-' + id);
          if (el) el.classList.add('show');
        }
      }
    }

    // -----------------------------------------------------------------------
    // Inline test param management
    // -----------------------------------------------------------------------
    function getOrInitTestState(voiceId) {
      if (!testStates[voiceId]) testStates[voiceId] = { expanded: false };
      return testStates[voiceId];
    }

    function toggleTestParams(voiceId) {
      var el = document.getElementById('test-params-' + voiceId);
      if (!el) return;
      el.classList.toggle('show');
      var state = getOrInitTestState(voiceId);
      state.expanded = el.classList.contains('show');
    }

    function toggleTestAdvParams(voiceId) {
      var el = document.getElementById('tp-adv-' + voiceId);
      if (el) el.classList.toggle('show');
    }

    function toggleLongText(voiceId, btn) {
      var el = document.getElementById('long-text-' + voiceId);
      if (!el) return;
      var hidden = el.style.display === 'none';
      el.style.display = hidden ? 'block' : 'none';
      btn.textContent = hidden ? '📝 关闭长文本' : '📝 长文本';

      // Sync: when long text is opened, copy the test input value into the textarea
      // and vice versa to keep them in sync
      var area = btn.closest('.test-area');
      if (hidden) {
        var textarea = el.querySelector('textarea');
        if (textarea && textarea.value.trim()) {
          area.querySelector('.test-input').value = textarea.value;
        }
      }
    }

    function applyTestPreset(voiceId, presetName) {
      if (!PRESETS[presetName]) return;
      var p = PRESETS[presetName];
      var el = document.getElementById('test-params-' + voiceId);
      if (!el) return;
      var sliders = el.querySelectorAll('.param-slider input[type="range"]');
      var vals = [p.speed, p.pitch, p.bass_gain || 0, p.presence_gain || 0, p.air_gain || 0, p.temperature, p.top_p, p.repetition_penalty];
      for (var i = 0; i < Math.min(sliders.length, vals.length); i++) {
        sliders[i].value = vals[i];
      }
      // Update labels — find label ids dynamically
      var labelKeys = ['speed', 'pitch', 'bass_gain', 'presence_gain', 'air_gain', 'temperature', 'top_p', 'repetition_penalty'];
      for (var i = 0; i < labelKeys.length && i < vals.length; i++) {
        var span = document.getElementById('tp-' + labelKeys[i] + '-' + voiceId);
        if (span) {
          var v = vals[i];
          if (labelKeys[i] === 'speed' || labelKeys[i] === 'top_p') span.textContent = v.toFixed(2);
          else if (labelKeys[i] === 'volume') span.textContent = v.toFixed(1);
          else if (labelKeys[i] === 'temperature') span.textContent = v.toFixed(1);
          else if (labelKeys[i] === 'repetition_penalty') span.textContent = v.toFixed(1);
          else span.textContent = v.toFixed(1);
        }
      }
      // Highlight button
      var btns = el.querySelectorAll('.preset-row button');
      for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
      var keys = Object.keys(PRESETS);
      var idx = keys.indexOf(presetName);
      if (idx >= 0 && btns[idx]) btns[idx].classList.add('active');

      // Set seed
      var seedEl = document.getElementById('tp-seed-' + voiceId);
      if (seedEl && p.seed != null) {
        seedEl.value = p.seed;
      }
    }

    function readTestParams(voiceId) {
      var el = document.getElementById('test-params-' + voiceId);
      if (!el || !el.classList.contains('show')) return {};
      var sliders = el.querySelectorAll('.param-slider input[type="range"]');
      var p = {};
      if (sliders[0]) p.speed = parseFloat(sliders[0].value);
      if (sliders[1]) p.pitch = parseFloat(sliders[1].value);
      if (sliders[2]) p.bass_gain = parseFloat(sliders[2].value);
      if (sliders[3]) p.presence_gain = parseFloat(sliders[3].value);
      if (sliders[4]) p.air_gain = parseFloat(sliders[4].value);
      if (sliders[5]) p.temperature = parseFloat(sliders[5].value);
      if (sliders[6]) p.top_p = parseFloat(sliders[6].value);
      if (sliders[7]) p.repetition_penalty = parseFloat(sliders[7].value);
      // Read seed from number input
      var seedEl = document.getElementById('tp-seed-' + voiceId);
      if (seedEl && seedEl.value !== '') {
        p.seed = parseInt(seedEl.value, 10);
      }
      return p;
    }

    // -----------------------------------------------------------------------
    // Save current inline test params as new voice / update existing
    // -----------------------------------------------------------------------
    function saveAsNewVoice(voiceId) {
      var params = readTestParams(voiceId);
      if (!Object.keys(params).length) { status('请先展开参数面板进行调音', 'error'); return; }
      openAddModal();
      applyParamsToModal(params);
    }

    function updateThisVoice(voiceId) {
      var params = readTestParams(voiceId);
      if (!Object.keys(params).length) { status('请先展开参数面板进行调音', 'error'); return; }
      var v = null;
      for (var i = 0; i < voices.length; i++) { if (voices[i].id === voiceId) { v = voices[i]; break; } }
      if (!v) return;
      openEditModal(voiceId);
      // Merge saved params with current inline params (inline overrides)
      var merged = {};
      if (v.params) { for (var k in v.params) { if (v.params.hasOwnProperty(k)) merged[k] = v.params[k]; } }
      for (var k in params) { if (params.hasOwnProperty(k)) merged[k] = params[k]; }
      applyParamsToModal(merged);
    }

    // -----------------------------------------------------------------------
    // Test TTS
    // -----------------------------------------------------------------------
    async function testVoice(voiceId, btn) {
      var area = btn.closest('.test-area');
      var input = area.querySelector('.test-input');
      var result = document.getElementById('test-result-' + voiceId);
      var text = input.value.trim();
      if (!text) { result.textContent = '请输入测试文本'; result.style.display = 'block'; return; }

      btn.disabled = true;
      btn.textContent = '合成中...';
      result.style.display = 'none';

      var voice = null;
      for (var i = 0; i < voices.length; i++) { if (voices[i].id === voiceId) { voice = voices[i]; break; } }
      var body = { text: text };
      var testParams = readTestParams(voiceId);
      if (voice && (voice.params || testParams.speed != null || testParams.pitch != null || testParams.bass_gain != null || testParams.presence_gain != null || testParams.air_gain != null || testParams.temperature != null || testParams.top_p != null || testParams.repetition_penalty != null)) {
        var merged = {};
        if (voice.params) {
          for (var k in voice.params) { if (voice.params.hasOwnProperty(k)) merged[k] = voice.params[k]; }
        }
        if (testParams.speed != null) merged.speed = testParams.speed;
        if (testParams.pitch != null) merged.pitch = testParams.pitch;
        if (testParams.bass_gain != null) merged.bass_gain = testParams.bass_gain;
        if (testParams.presence_gain != null) merged.presence_gain = testParams.presence_gain;
        if (testParams.air_gain != null) merged.air_gain = testParams.air_gain;
        if (testParams.temperature != null) merged.temperature = testParams.temperature;
        if (testParams.top_p != null) merged.top_p = testParams.top_p;
        if (testParams.repetition_penalty != null) merged.repetition_penalty = testParams.repetition_penalty;
        if (testParams.seed != null) merged.seed = testParams.seed;
        body.params = merged;
      }

      try {
        var resp = await api('POST', '/voices/' + voiceId + '/test', body);
        if (!(resp instanceof Response)) throw new Error('Unexpected response');
        var blob = await resp.blob();
        var url = URL.createObjectURL(blob);
        result.innerHTML = '<audio controls src="' + url + '" style="width:100%;height:36px;"></audio>'
          + '<div style="margin-top:0.4rem;display:flex;gap:0.4rem;">'
          + '  <button class="small" style="background:#059669;color:white;" onclick="setAsAnchor(\'' + voiceId + '\', this)">🔗 设为参考音锚点</button>'
          + '</div>';
        result.style.display = 'block';
      } catch (e) {
        result.innerHTML = '<div class="status error">试听失败: ' + escapeHtml(e.message) + '</div>';
        result.style.display = 'block';
      } finally {
        btn.disabled = false;
        btn.textContent = '试听';
      }
    }

    // -----------------------------------------------------------------------
    // 疯狂抽卡 — 生成 N 个版本打包 ZIP 下载
    // -----------------------------------------------------------------------
    async function carnivalVoice(voiceId, btn) {
      var area = btn.closest('.test-area');
      var input = area.querySelector('.test-input');
      var text = input.value.trim();
      if (!text) { status('请输入测试文本用于抽卡', 'error'); return; }

      btn.disabled = true;
      btn.textContent = '启动抽卡...';

      var voice = null;
      for (var i = 0; i < voices.length; i++) { if (voices[i].id === voiceId) { voice = voices[i]; break; } }
      var body = { text: text, count: 10 };
      var testParams = readTestParams(voiceId);
      if (voice && (voice.params || Object.keys(testParams).length)) {
        var merged = {};
        if (voice.params) { for (var k in voice.params) { if (voice.params.hasOwnProperty(k)) merged[k] = voice.params[k]; } }
        for (var k in testParams) { if (testParams.hasOwnProperty(k)) merged[k] = testParams[k]; }
        body.params = merged;
      }

      try {
        // 1. POST start carnival job → get job_id
        var resp = await fetch(API + '/voices/' + voiceId + '/carnival', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!resp.ok) {
          var edata = await resp.json().catch(function(){ return {}; });
          throw new Error(edata.detail || 'HTTP ' + resp.status);
        }
        var job = await resp.json();
        var jobId = job.job_id;
        var total = job.total;

        btn.textContent = '🎲 抽卡 0/' + total;

        // 2. SSE progress
        var done = false;
        await new Promise(function(resolve, reject) {
          var es = new EventSource(API + '/jobs/' + jobId + '/events');
          es.onmessage = function(evt) {
            var data = JSON.parse(evt.data);
            if (data.type === 'carnival_progress') {
              btn.textContent = '🎲 抽卡 ' + data.current + '/' + data.total;
              status('🎲 抽卡中(' + data.current + '/' + data.total + ') seed=' + data.seed, 'info');
            } else if (data.type === 'carnival_done') {
              done = true;
              es.close();
              resolve();
            } else if (data.type === 'carnival_error') {
              es.close();
              reject(new Error(data.error || '抽卡失败'));
            }
          };
          es.onerror = function() {
            es.close();
            if (!done) reject(new Error('SSE 连接断开'));
          };
        });

        // 3. Download ZIP
        var resp2 = await fetch(API + '/voices/carnival/' + jobId + '/download');
        if (!resp2.ok) throw new Error('下载失败: HTTP ' + resp2.status);
        var blob = await resp2.blob();
        var downloadUrl = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = downloadUrl;
        a.download = voice ? 'carnival_' + voice.name + '.zip' : 'carnival.zip';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        status('抽卡完成 — 已下载 ZIP 包（' + total + ' 个版本）', 'success');
      } catch (e) {
        status('抽卡失败: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = '🎲 疯狂抽卡';
      }
    }

    // -----------------------------------------------------------------------
    // 设为参考音锚点 — 将试听音频设为固定参考音
    // -----------------------------------------------------------------------
    async function setAsAnchor(voiceId, btn) {
      var area = btn.closest('.test-area');
      var input = area.querySelector('.test-input');
      var text = input.value.trim();
      if (!text) { status('请先输入/确认文本再设为锚点', 'error'); return; }
      if (!confirm('确定将当前试听设为 "' + text.substring(0, 30) + '..." 的参考音锚点？\n\n后续所有该音色的 TTS 都将以此声音为固定参考。')) return;

      btn.disabled = true;
      btn.textContent = '设置中...';

      // We use test-and-anchor endpoint: synthesize + set reference in one call
      var voice = null;
      for (var i = 0; i < voices.length; i++) { if (voices[i].id === voiceId) { voice = voices[i]; break; } }
      var body = { text: text };
      var testParams = readTestParams(voiceId);
      if (voice && (voice.params || Object.keys(testParams).length)) {
        var merged = {};
        if (voice.params) { for (var k in voice.params) { if (voice.params.hasOwnProperty(k)) merged[k] = voice.params[k]; } }
        for (var k in testParams) { if (testParams.hasOwnProperty(k)) merged[k] = testParams[k]; }
        body.params = merged;
      }

      try {
        var resp = await api('POST', '/voices/' + voiceId + '/test-and-anchor', body);
        if (!(resp instanceof Response)) throw new Error('Unexpected response');
        var blob = await resp.blob();
        var url = URL.createObjectURL(blob);

        // Update the test result with the anchor audio
        var result = document.getElementById('test-result-' + voiceId);
        result.innerHTML = '<div class="status success">✅ 已设为参考音锚点！</div>'
          + '<audio controls src="' + url + '" style="width:100%;height:36px;"></audio>';
        result.style.display = 'block';

        status('参考音锚点已设置！', 'success');

        // Reload voices to reflect anchor status
        await loadVoices();
      } catch (e) {
        status('设置参考音失败: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = '🔗 设为参考音锚点';
      }
    }

    // -----------------------------------------------------------------------
    // Add / Edit modal
    // -----------------------------------------------------------------------
    function openAddModal() {
      document.getElementById('modal-title').textContent = '添加音色';
      document.getElementById('edit-id').value = '';
      document.getElementById('f-name').value = '';
      document.getElementById('f-backend').value = 'fish';
      document.getElementById('f-host').value = '';
      document.getElementById('f-ref-audio').value = '';
      document.getElementById('f-ref-text').value = '';
      document.getElementById('f-url-fish').value = 'http://127.0.0.1:7860';
      document.getElementById('f-url-f5').value = 'http://127.0.0.1:7861';
      document.getElementById('f-url-indextts').value = 'http://127.0.0.1:7862';
      document.getElementById('f-master-text').value = '';
      document.getElementById('f-master-style').value = 'calm';
      document.getElementById('master-path-display').textContent = '';
      document.getElementById('master-path-display').dataset.path = '';
      toggleIndexttsFields();
      document.getElementById('f-preset').value = '';
      document.getElementById('f-seed').value = '';
      document.getElementById('modal-adv').classList.remove('show');
      applyParamsToModal(null);
      document.getElementById('modal-status').style.display = 'none';
      document.getElementById('voice-modal').classList.add('show');
      document.getElementById('btn-save').textContent = '保存';
    }

    function openEditModal(voiceId) {
      var v = null;
      for (var i = 0; i < voices.length; i++) { if (voices[i].id === voiceId) { v = voices[i]; break; } }
      if (!v) return;
      document.getElementById('modal-title').textContent = '编辑音色';
      document.getElementById('edit-id').value = v.id;
      document.getElementById('f-name').value = v.name;
      document.getElementById('f-backend').value = v.backend;
      document.getElementById('f-host').value = v.host_id || '';
      document.getElementById('f-ref-audio').value = v.reference_audio_path || '';
      document.getElementById('f-ref-text').value = v.reference_text || '';
      document.getElementById('f-url-fish').value = v.base_url_fish || 'http://127.0.0.1:7860';
      document.getElementById('f-url-f5').value = v.base_url_f5 || 'http://127.0.0.1:7861';
      document.getElementById('f-url-indextts').value = v.base_url_indextts || 'http://127.0.0.1:7862';
      document.getElementById('f-master-text').value = v.master_text || '';
      document.getElementById('f-master-style').value = 'calm';
      if (v.params && v.params.master_style && ['calm','excited','relaxed'].indexOf(v.params.master_style) >= 0) {
        document.getElementById('f-master-style').value = v.params.master_style;
      }
      var disp = document.getElementById('master-path-display');
      if (v.master_audio_path) {
        disp.textContent = '✓ ' + v.master_audio_path;
        disp.dataset.path = v.master_audio_path;
      } else {
        disp.textContent = '';
        disp.dataset.path = '';
      }
      toggleIndexttsFields();
      applyParamsToModal(v.params || {});
      if (v.params && (v.params.temperature != null || v.params.top_p != null || v.params.repetition_penalty != null)) {
        document.getElementById('modal-adv').classList.add('show');
      } else {
        document.getElementById('modal-adv').classList.remove('show');
      }
      document.getElementById('modal-status').style.display = 'none';
      document.getElementById('voice-modal').classList.add('show');
      document.getElementById('btn-save').textContent = '更新';
    }

    function closeModal() {
      document.getElementById('voice-modal').classList.remove('show');
    }

    function toggleIndexttsFields() {
      var backend = document.getElementById('f-backend').value;
      var show = backend === 'indextts';
      var block = document.getElementById('indextts-fields');
      if (block) block.style.display = show ? 'block' : 'none';

      // 切到 indextts 时,若 master 还没填且 reference 有值,自动用 reference 兜底
      if (show) {
        var disp = document.getElementById('master-path-display');
        if (disp && !disp.dataset.path) {
          var refAudio = document.getElementById('f-ref-audio').value.trim();
          if (refAudio) {
            disp.textContent = '(从 reference_audio_path 自动带入) ' + refAudio;
            disp.dataset.path = refAudio;
          }
        }
        // master_text 同样兜底
        var mtEl = document.getElementById('f-master-text');
        if (mtEl && !mtEl.value.trim()) {
          var refText = document.getElementById('f-ref-text').value.trim();
          if (refText) mtEl.value = refText;
        }
      }
    }

    async function uploadMaster() {
      var voiceId = document.getElementById('edit-id').value;
      var file = document.getElementById('f-master-audio').files[0];
      if (!voiceId) {
        modalStatus('请先保存音色后再上传 master audio', 'error');
        return;
      }
      if (!file) {
        modalStatus('请选择音频文件', 'error');
        return;
      }
      var masterText = document.getElementById('f-master-text').value || '';
      var fd = new FormData();
      fd.append('audio', file);
      fd.append('master_text', masterText);
      try {
        var resp = await fetch('/api/voices/' + voiceId + '/upload-master', { method: 'POST', body: fd });
        if (!resp.ok) {
          var errText = await resp.text();
          modalStatus('上传失败: ' + resp.status + ' ' + errText, 'error');
          return;
        }
        var data = await resp.json();
        document.getElementById('master-path-display').dataset.path = data.master_audio_path;
        document.getElementById('master-path-display').textContent = '✓ ' + data.master_audio_path;
        modalStatus('master audio 已上传 (' + data.size_bytes + ' bytes)', 'success');
      } catch (e) {
        modalStatus('上传失败: ' + e.message, 'error');
      }
    }

    async function saveVoice() {
      var id = document.getElementById('edit-id').value;
      var masterPath = document.getElementById('master-path-display').dataset.path || null;
      var data = {
        name: document.getElementById('f-name').value.trim(),
        backend: document.getElementById('f-backend').value,
        host_id: document.getElementById('f-host').value || null,
        reference_audio_path: document.getElementById('f-ref-audio').value.trim() || null,
        reference_text: document.getElementById('f-ref-text').value.trim() || null,
        base_url_fish: document.getElementById('f-url-fish').value.trim() || null,
        base_url_f5: document.getElementById('f-url-f5').value.trim() || null,
        master_audio_path: masterPath,
        master_text: document.getElementById('f-master-text').value.trim() || null,
        base_url_indextts: document.getElementById('f-url-indextts').value.trim() || null,
      };
      if (!data.name) { modalStatus('音色名称不能为空', 'error'); return; }

      // master style 走 params 通道
      var params = readModalParams();
      var masterStyle = document.getElementById('f-master-style').value;
      if (document.getElementById('f-backend').value === 'indextts') {
        params = params || {};
        params.master_style = masterStyle;
      }
      if (Object.keys(params).length) {
        data.params = params;
      }

      try {
        if (id) {
          var updated = await api('PUT', '/voices/' + id, data);
          for (var i = 0; i < voices.length; i++) { if (voices[i].id === id) { voices[i] = updated; break; } }
          if (i >= voices.length) voices.push(updated);
        } else {
          var created = await api('POST', '/voices', data);
          voices.push(created);
        }
        renderVoices();
        closeModal();
        status(id ? '音色已更新' : '音色已添加', 'success');
      } catch (e) {
        modalStatus(e.message, 'error');
      }
    }

    // -----------------------------------------------------------------------
    // Delete
    // -----------------------------------------------------------------------
    async function deleteVoice(voiceId) {
      if (!confirm('确定删除该音色？')) return;
      try {
        await api('DELETE', '/voices/' + voiceId);
        var arr = [];
        for (var i = 0; i < voices.length; i++) { if (voices[i].id !== voiceId) arr.push(voices[i]); }
        voices = arr;
        delete testStates[voiceId];
        renderVoices();
        status('音色已删除', 'success');
      } catch (e) {
        status('删除失败: ' + e.message, 'error');
      }
    }

    // -----------------------------------------------------------------------
    // Init
    // -----------------------------------------------------------------------
    async function init() {
      await Promise.all([loadVoices(), loadHosts()]);
      status('已加载 ' + voices.length + ' 个音色', voices.length ? 'success' : 'info');
      // IndexTTS2 master tape 区域显隐切换 (backend 切换时)
      const backendSel = document.getElementById('f-backend');
      if (backendSel) {
        backendSel.addEventListener('change', toggleIndexttsFields);
        toggleIndexttsFields();  // 初始化: 确保默认值正确显示
      }
    }

    init();
