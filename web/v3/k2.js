// ⑤ K2 页 (V6 薄壳: v3_k2 相位 + 人检)
import { rail } from './rail.js';
import { createJobRunner } from './job.js';

const qs = new URLSearchParams(location.search);
const B = qs.get('book_id') || '', E = qs.get('ep_index') || '1';
const BASE = `/api/anim/v3/ep/${encodeURIComponent(B)}/${E}`;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

const ST_ZH = { planned: '待图', img_done: '图出', approved: '✓ 已过审', failed: '✗ 失败' };
let SHOTS = [], FROZEN = false;

/* 1108 修 (ep6 实锤「已选 7 镜 按钮失效」): job 完成 onFinished→boot() 会重建全部
   checkbox, 旧版 cart 条停在旧值而 cartSids() 读到空 → startRedo 静默 return。
   SEL 单源: 渲染勾选态/cartSids 都从 SEL 出, 跨重渲染存活; sessionStorage 兜刷新。 */
const SEL = new Set(JSON.parse(sessionStorage.getItem('k2sel') || '[]'));
function selSync() {
  const live = new Set(SHOTS.map(s => s.sid));
  [...SEL].forEach(x => { if (!live.has(x)) SEL.delete(x); });
  try { sessionStorage.setItem('k2sel', JSON.stringify([...SEL])); } catch (e) {}
}

const runner = createJobRunner({
  /* 1106 用户令: 逐镜增量刷新 — 每生成完一张, 立刻拉那张图替换占位 (不等全批);
     实现挂在 job.js 的轮询回调上, 面板进度条本身就是逐镜推进的数据源 */
  onPoll: job => {
    if (job?.status !== 'running' || !job.progress?.current) return;
    const m = String(job.progress.current).match(/^(s\d+)/);
    if (!m) return;
    const sid = m[1];
    const card = document.querySelector(`.kcard[data-sid="${sid}"]`);
    if (!card || card.dataset.loaded === '1') return;
    fetch(`${BASE}/k2`).then(r => r.json()).then(d => {
      const s = (d.shots || []).find(x => x.sid === sid);
      if (s?.image_url) {
        card.dataset.loaded = '1';
        const imgBox = card.querySelector('.kimg');
        if (imgBox) {
          imgBox.innerHTML = `<img src="${s.image_url}?v=${Date.now()}" loading="lazy" alt="${sid}">`;
        }
        const badge = card.querySelector('.kbadge');
        if (badge) { badge.className = 'kbadge img_done'; badge.textContent = '图出'; }
      }
    }).catch(() => {});
  },
  onFinished: job => {
    if (job.status === 'completed')
      toast(`✓ K2 完成: ${job.result?.ok ?? '?'} 成 / ${job.result?.fail ?? 0} 败` +
            (job.result?.suspect ? ` · ${job.result.suspect} 镜文字嫌疑` : ''));
    else if (job.status !== 'unknown') toast('K2 结束: ' + (job.status || ''));
    boot(); refreshCart();
  },
  onBusy: busy => { $('btn-run').disabled = busy; $('btn-all').disabled = busy; },
});

async function boot() {
  const [d, st] = await Promise.all([
    (await fetch(`${BASE}/k2`)).json(),
    (await fetch(`${BASE}/state`)).json(),
  ]);
  SHOTS = d.shots || []; FROZEN = !!d.frozen;
  $('sub').textContent = `${st.book_title} · 第${st.ep}集`;
  rail($('rail'), { line: st.line, gates: d.gates, active: 'k2' });
  if (st.running_job) runner.attach(st.running_job);
  renderCounts(); renderGrid();
}

function renderCounts() {
  const n = SHOTS.length;
  const by = k => SHOTS.filter(s => (s.status || 'planned') === k).length;
  const sus = SHOTS.filter(s => s.suspect).length;
  $('counts').innerHTML =
    `<span class="badge">${n} 镜</span>` +
    `<span class="badge">${by('img_done')} 图出</span>` +
    `<span class="badge ${by('approved') === n && n ? 'ok' : ''}">${by('approved')} 已过审</span>` +
    (sus ? `<span class="badge bad">${sus} 文字嫌疑</span>` : '') +
    (by('failed') ? `<span class="badge bad">${by('failed')} 失败</span>` : '');
  const img = n - by('planned');
  $('stat').textContent = FROZEN
    ? `图 ${img}/${n} · 过审 ${by('approved')}/${n} — 全过审后可进 ⑦ 盖章`
    : '🔒 ④ 未放行 — 先在导演页确认放行 (英文化批) 才能生图';
}

/* 灯箱预览 (1106 用户令: 看图效果对齐 style_bible + 连续翻页审核)
   事件委托在 #grid 上 — 增量刷新换 innerHTML 也不用重绑
   1108 R 标: 点开看过 = 本地摘徽章 + POST reviewed (v2 语义, 独立文件不吃集锁) */
$('grid').addEventListener('click', ev => {
  const img = ev.target.closest('.kimg img');
  if (!img || !img.src) return;
  const card = img.closest('.kcard');
  const sid = card?.dataset.sid;
  const rbd = card?.querySelector('.kbadge.bd-r');
  if (rbd) {
    rbd.remove();
    fetch(`${BASE}/reviewed/${encodeURIComponent(sid)}`, { method: 'POST' }).catch(() => {});
  }
  const gallery = SHOTS.filter(x => x.image_url).map(x => ({
    src: x.image_url, cap: `${x.sid} · ${x.size || ''} ${x.cam || ''} · ~${x.est_s}s`}));
  if (typeof lbOpen === 'function')
    // 用原始属性 (相对路径) 比对画廊 — img.src 是解析后的绝对 URL,
    // 与画廊相对路径永不相等 → findIndex 落空 → 永远从第一张开 (实锤)
    lbOpen(img.getAttribute('src').split('?')[0],
           (() => { const s = SHOTS.find(x => x.sid === sid); 
                    return s ? `${s.sid} · ${s.size || ''} ${s.cam || ''} · ~${s.est_s}s` : sid; })(),
           gallery);
});

function renderGrid() {
  if (!SHOTS.length) { $('grid').innerHTML = '<div class="note">未切镜 — 先跑 ③</div>'; return; }
  $('grid').innerHTML = SHOTS.map(s => {
    const st = s.status || 'planned';
    const img = s.image_url
      ? `<img src="${esc(s.image_url)}" loading="lazy"
              alt="${esc(s.sid)}" title="${esc(s.sid)}${s.brand ? ' · 品牌位' : ' · seed ' + (s.seed ?? '?')}">`
      : (s.muted ? '🔇 已屏蔽 — 音频屏弃, 本槽不生图不生视频'
                 : (s.brand ? '🏔 品牌卡直通 (待生成)' : '— 待生成 —'));
    return `<div class="kcard ${st === 'approved' ? 'approved' : (s.suspect ? 'suspect' : '')}${s.muted ? ' kmut' : ''}" data-sid="${esc(s.sid)}">
      <div class="kimg">${img}</div>
      <div class="kmeta">
        ${s.brand ? '' : `<input type="checkbox" class="ksel" data-sid="${esc(s.sid)}"${SEL.has(s.sid) ? ' checked' : ''} title="加入购物篮 (列队重做)">`}
        <span class="ksid">${esc(s.sid)}</span>
        <span class="ksize">${esc(s.size || '')}</span>
        <span class="kcam">${esc(s.cam || '')}${s.cam && s.cam !== '固定' ? ' ↦' : ''}</span>
        ${s.runs > 1 ? `<span class="ktry" title="此镜累计生图 ${s.runs} 次 (含重做/重掷)">${s.runs} 试</span>` : ''}
        <span class="flex1"></span>
        ${s.muted ? '<span class="kbadge planned" title="音频已屏弃 — 盖章/H3 跳过">🔇屏弃</span>' : ''}
        ${s.redo_review ? '<span class="kbadge bd-r" title="重做待复查: 此镜刚被列队重做 — 点开看过 / ✅过审后消除">R</span>' : ''}
        ${s.suspect ? '<span class="kbadge failed" title="OCR 检字命中 — 看图确认是否乱字">⚠文字</span>' : ''}
        <span class="kbadge ${st}">${ST_ZH[st] || st}</span>
      </div>
      ${s.error ? `<div class="kerr">${esc(s.error)}</div>` : ''}
      <div class="kbtns">
        <button class="iconbtn" data-act="approve" data-sid="${esc(s.sid)}"
                ${st !== 'img_done' ? ' disabled' : ''}>✅ 过审</button>
        <span class="flex1"></span>
        ${s.brand ? '<span class="kbadge planned" title="品牌位: 冻结资产直通, 不可重掷">🏔 品牌</span>'
                   : `<button class="iconbtn" data-act="reroll" data-sid="${esc(s.sid)}"
                title="单镜重掷 (换 seed)">${st === 'planned' ? '▶ 生成' : '🔁 重掷'}</button>`}
      </div>
    </div>`;
  }).join('');
  selSync(); refreshCart();
  $('grid').querySelectorAll('.ksel').forEach(cb => {
    cb.onchange = () => {
      cb.checked ? SEL.add(cb.dataset.sid) : SEL.delete(cb.dataset.sid);
      selSync(); refreshCart();
    };
  });
  $('grid').querySelectorAll('button[data-act]').forEach(b => {
    b.onclick = async () => {
      const sid = b.dataset.sid;
      b.disabled = true;
      try {
        if (b.dataset.act === 'approve') {
          const r = await (await fetch(`${BASE}/k2/approve`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sids: [sid] }) })).json();
          toast(`✓ ${sid} 已过审 (${r.approved})`);
          boot();
        } else {
          const r = await fetch(`${BASE}/v3_k2`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ only: [sid] }) });
          if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); b.disabled = false; return; }
          runner.attach(await r.json());
        }
      } catch (e) { toast('操作失败: ' + e.message); b.disabled = false; }
    };
  });
}

function cartSids() {
  return SHOTS.filter(s => SEL.has(s.sid)).map(s => s.sid);
}
function refreshCart() {
  const n = cartSids().length;
  $('cart').hidden = !n;
  $('cart-count').textContent = n;
}
async function startRedo() {
  const sids = cartSids();
  if (!sids.length) { toast('未选镜 — 勾选镜卡片上的选择框'); return; }
  if (!confirm(`🎨 重做到生图 ${sids.length} 镜?
LLM重设计画面句 → 重译英文 → 生图后停在人检 (视频归 ⑧ 页)`)) return;
  const note = prompt('重做原因 (可空, 会带进重设计提示; 留空用系统默认)', '') ?? '';
  const r = await fetch(`${BASE}/v3_redo`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ only: sids, note }) });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  SEL.clear(); selSync(); refreshCart();
  runner.attach(await r.json());
}
$('btn-redo-img').onclick = startRedo;
$('btn-cart-clear').onclick = () => {
  SEL.clear(); selSync();
  document.querySelectorAll('.ksel').forEach(c => { c.checked = false; });
  refreshCart();
};

$('btn-run').onclick = async () => {
  const r = await fetch(`${BASE}/v3_k2`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!r.ok) { toast('启动失败: ' + (await r.json()).detail); return; }
  runner.attach(await r.json());
};
$('btn-all').onclick = async () => {
  const r = await (await fetch(`${BASE}/k2/approve`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{"all":true}' })).json();
  toast(`✓ 过审 ${r.approved} 镜`);
  boot();
};

function toast(m) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = m;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}
boot();
