// ══ 素材摄入产线 (2026-08-30): VPN 徽章 + 实体chips + 任务表 ═════════════
let _ingestTimer = null;
let _vpnTimer = null;

async function ingestVpnBadge() {
  const el = document.getElementById('vpn-badge');
  const hint = document.getElementById('vpn-hint');
  if (!el) return;
  try {
    const v = await api('GET', '/materials/ingest/vpn');
    if (v.state === 'on') {
      el.textContent = '🌐 VPN 已连通 (' + v.latency_ms + 'ms)';
      el.style.background = 'rgba(34,197,94,0.15)'; el.style.color = '#22c55e';
    } else {
      el.textContent = '🔒 VPN 未连通 (直连)';
      el.style.background = 'rgba(239,68,68,0.15)'; el.style.color = '#ef4444';
    }
    const jobs = await api('GET', '/materials/ingest/jobs?limit=50');
    const waiting = jobs.filter(j => j.stage.startsWith('waiting'));
    if (waiting.length) {
      const want = waiting[0].stage.endsWith('_on') ? '开启' : '退出';
      hint.textContent = `⏳ ${waiting.length} 个任务等待 VPN ${want}… (每5s检测, 30分钟超时)`;
    } else hint.textContent = '';
  } catch (e) { el.textContent = '🌐 检测失败'; }
}

async function ingestLoadEntities() {
  const box = document.getElementById('ingest-entities');
  if (!box) return;
  try {
    const ents = await api('GET', '/materials/ingest/entities');
    box.innerHTML = '';
    ents.forEach(e => {
      const b = document.createElement('button');
      b.className = 'btn btn-sm';
      b.style.cssText = e.need_refill
        ? 'border-color:#ef4444;color:#ef4444;' : 'border-color:var(--border);';
      b.title = `搜索: ${e.search} | 库存 ${e.stock}/${e.min_stock}`;
      b.innerHTML = `${e.need_refill ? '🔴 ' : ''}${e.name} <span style="opacity:.55;font-size:.7em">${e.stock}</span>`;
      b.onclick = () => ingestStartEntity(e.name);
      box.appendChild(b);
    });
  } catch (e) { box.innerHTML = '<span style="color:var(--text-muted)">实体字典加载失败</span>'; }
}

async function ingestStartUrl() {
  const url = document.getElementById('ingest-url').value.trim();
  if (!url) { toast('请粘贴 YouTube 链接', 'error'); return; }
  const entity = document.getElementById('ingest-entity-name').value.trim() || null;
  await api('POST', '/materials/ingest', { mode: 'url', url, entity });
  toast('摄取任务已创建');
  document.getElementById('ingest-url').value = '';
  ingestPollJobs();
}

async function ingestStartEntity(name) {
  await api('POST', '/materials/ingest', { mode: 'entity', entity: name });
  toast(`实体批「${name}」已启动`);
  ingestPollJobs();
}

async function ingestStartCustom() {
  const name = document.getElementById('ingest-custom-entity').value.trim();
  const search = document.getElementById('ingest-custom-search').value.trim();
  if (!name || !search) { toast('临时实体需要名字+搜索词', 'error'); return; }
  await api('POST', '/materials/ingest', { mode: 'entity', entity: name, custom_search: search });
  toast(`临时实体批「${name}」已启动`);
  ingestPollJobs();
}

const STAGE_LABEL = {
  queued: '排队', downloading: '⬇ 下载', probing: '🎞 抽帧',
  ocr: '🔍 OCR时间轴', splitting: '✂ 净窗切片', tagging: '🧠 LLM打标',
  registering: '📥 入库', done: '✅ 完成', failed: '❌ 失败',
  waiting_vpn_on: '⏳ 等VPN开', waiting_vpn_off: '⏳ 等VPN关',
  paused_vpn_on: '⏸ 暂停(需VPN开)', paused_vpn_off: '⏸ 暂停(需VPN关)',
};

async function ingestPollJobs() {
  const box = document.getElementById('ingest-jobs');
  if (!box) return;
  try {
    const jobs = await api('GET', '/materials/ingest/jobs?limit=50');
    if (!jobs.length) { box.innerHTML = '<span style="color:var(--text-muted)">暂无任务 — 贴链接或点实体开始</span>'; return; }
    box.innerHTML = jobs.map(j => {
      const st = STAGE_LABEL[j.stage] || j.stage;
      const isWait = j.stage.startsWith('waiting') || j.stage.startsWith('paused');
      const canResume = j.stage.startsWith('paused') || j.stage === 'failed';
      const s = j.stats || {};
      const stats = s.registered ? `入库 ${s.registered}` :
        s.tagged ? `打标 ${s.tagged}` : s.clips ? `切片 ${s.clips}` :
        (s.clean_windows !== undefined) ? `净窗 ${s.clean_windows} (${s.clean_sec||0}s)` :
        (s.cuts !== undefined) ? `切点 ${s.cuts}` : '';
      return `<div style="display:flex;gap:0.6rem;align-items:center;padding:0.35rem 0;border-bottom:1px solid var(--border);">
        <span style="flex:0 0 90px;font-weight:600;">${j.entity || j.mode}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-secondary);">${j.title || j.source_url || ''}</span>
        <span style="flex:0 0 110px;${isWait ? 'color:#f59e0b;font-weight:600;' : ''}">${st}</span>
        <span style="flex:0 0 120px;color:var(--text-muted);">${stats}</span>
        ${canResume ? `<button class="btn btn-sm" onclick="ingestResume('${j.id}')">▶ 续跑</button>` : ''}
      </div>`;
    }).join('');
  } catch (e) { box.innerHTML = '加载失败: ' + e.message; }
}

async function ingestResume(id) {
  await api('POST', `/materials/ingest/${id}/resume`);
  toast('已续跑');
  ingestPollJobs();
}

const _origSwitchTab = switchTab;
switchTab = function(tab) {
  _origSwitchTab(tab);
  clearInterval(_ingestTimer); clearInterval(_vpnTimer);
  if (tab === 'ingest') {
    ingestVpnBadge();
    _vpnTimer = setInterval(ingestVpnBadge, 5000);
    _ingestTimer = setInterval(ingestPollJobs, 4000);
  }
};
