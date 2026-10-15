// ── 1007 P4: 法层拍摄脚本面板 (道→法→术 架构的"法"展示层) ──
// 1008 P7: 校验徽章 (ss.validation) + 详情列表 — 分镜/组装完成后自动刷新
import { $, esc } from './util.js';
import { fileUrl } from './api.js';

let collapsed = false;

const ANGLE_ZH = { birds_eye: '鸟瞰', high_angle: '高角', slight_high: '微俯',
  eye_level: '平视', slight_low: '微仰', low_angle: '仰视', dutch: '斜角' };

export function shootingView(state) {
  const st = state.st;
  const panel = $('#shooting-panel');
  if (!panel) return;
  // 1008 P7: 折叠只藏 body 不藏面板 — 校验徽章常驻标题栏 (分镜续跑完成后即见)
  panel.hidden = !st?.planned;
  $('#shooting-body').hidden = collapsed;
  if (panel.hidden) return;
  renderShooting(st);
  renderCollapse();
}

function renderCollapse() {
  const btn = $('#shooting-collapse-btn');
  btn.textContent = collapsed ? '▸ 展开' : '▾ 收起';
  btn.onclick = () => {
    collapsed = !collapsed;
    $('#shooting-body').hidden = collapsed;
    renderCollapse();
  };
}

/** P7 校验徽章: ✓通过 / ⚠N处 (点击展开面板看详情) */
function validationBadge(v) {
  if (!v) return '';
  const warn = v.warns || 0;
  return `<span class="badge ${warn ? 'bd-amber' : 'bd-green'}" id="shooting-val-badge"
    title="${esc((v.items || []).map(i => i.name + ': ' + i.detail).join('\n'))}">
    ${warn ? '⚠ 法校验 ' + warn + ' 处' : '✓ 法校验通过'}</span>`;
}

function validationBlock(v) {
  if (!v) return '';
  const rows = (v.items || []).map(i =>
    `<div class="val-row ${i.status === 'warn' ? 'val-warn' : ''}">
      <span>${i.status === 'warn' ? '⚠' : '✓'} ${esc(i.name)}</span>
      <span class="hint">${esc(i.detail)}</span>
    </div>`).join('');
  return `<div class="val-panel"><b>📋 法校验结果</b>${rows}
    <span class="hint">校验于 ${esc(v.checked_at || '')} · 生成/组装时自动跑 · 零 LLM</span></div>`;
}

async function renderShooting(st) {
  const body = $('#shooting-body');
  if (!body) return;

  // 尝试读 shooting_script.json
  const ss = await fetchShooting(st);
  if (!ss) {
    body.innerHTML = '<span class="hint">📋 拍摄脚本未生成 — 规划(立意闸)时自动产出</span>';
    return;
  }

  const scenes = ss.scenes || [];
  if (!scenes.length) {
    body.innerHTML = '<span class="hint">法层无场次数据</span>';
    return;
  }

  // P7: 徽章进面板标题栏 (面板在顶栏区 = 顶栏徽章)
  const bar = $('#shooting-bar-badge');
  if (bar) bar.innerHTML = validationBadge(ss.validation);

  let html = validationBlock(ss.validation);

  for (const scene of scenes) {
    const shots = scene.shots || [];
    const light = scene.lighting || {};
    const cont = scene.continuity || {};

    // 镜头序 chips
    const chips = shots.map((sh, i) => {
      const cls = i === 0 ? ' first' : i === shots.length - 1 ? ' last' : '';
      const arrow = i < shots.length - 1
        ? `<span class="shot-arrow">${esc(sh.transition_to_next === 'match_cut' ? '⇄' : sh.transition_to_next === 'dissolve' ? '⇢' : '→')}</span>` : '';
      return `<span class="shot-chip${cls}" title="${esc(sh.creative_hint || '')}">
        <b>${esc(sh.size || '?')}</b>·${esc(ANGLE_ZH[sh.angle] || sh.angle || '?')}
      </span>${arrow}`;
    }).join('');

    // 光影 badge (1009: key_source 退役→key_direction 光效语言; 旧档兼容)
    const lightBadge = `<span class="lighting-badge">
      <b>${esc(light.mood || '?')}</b> ${esc(light.key_direction || light.key_source || '')} ${esc(light.temp || '')} ${esc(light.fill_ratio || '')}
    </span>`;

    // 连续性 tags
    const eyelines = (cont.eyeline_chain || []).map(e =>
      `<span class="cont-tag">👁 ${esc(e)}</span>`).join(' ');
    const propStates = cont.prop_states
      ? `<span class="cont-tag">🔧 ${esc(cont.prop_states)}</span>` : '';

    html += `<div class="shooting-scene">
      <h4>${esc(scene.arc_id || '?')} <small>${shots.length}镜</small></h4>
      <div class="shot-seq">${chips}</div>
      <div>${lightBadge}</div>
      ${(eyelines || propStates) ? `<div class="continuity-tags">${eyelines} ${propStates}</div>` : ''}
    </div>`;
  }

  body.innerHTML = html;
}

async function fetchShooting(st) {
  // 从 ep 目录读 shooting_script.json
  try {
    const r = await fetch(fileUrl('shooting_script.json'));
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}
