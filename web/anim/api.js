// ── API 客户端: 全部后端交互的唯一出口 (原实现 4 处手写 fetch, 错误处理各自为政) ──
import { BASE } from './context.js';

/** 带 status 的业务错误, 便于调用方按状态码分流 */
export class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

/**
 * 统一请求封装 (BASE 相对路径)。
 * @param {string} path
 * @param {object|null} body 传了即默认 POST
 * @param {string} [method]
 * @returns {Promise<any>}
 */
export async function api(path, body, method) {
  const r = await fetch(BASE + path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));   // 非 JSON 错误响应也不至于抛 SyntaxError
  if (!r.ok) throw new ApiError(data.detail || r.statusText, r.status);
  return data;
}

/**
 * 产物文件 URL (图/视频/立意.md/qc_sheet)。
 * @param {string} rel 集内相对路径
 * @param {number|string} ver 媒体版本号 (见 context.media — 空 falsy 时不拼参数)
 */
export function fileUrl(rel, ver) {
  if (!rel) return '';
  // 前端硬拒绝协议 URL / 绝对路径 — 防误传被后端当"友好路径"解析 (越权读任意文件)
  if (/^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(rel) || rel.startsWith('/')) return '';
  if (rel.includes('..')) {
    // 合法越级用例 (../../_资产): 走查询参数, 由后端防穿越白名单把关
    const v = ver ? `&v=${ver}` : '';
    return `${BASE}/file?path=${encodeURIComponent(rel)}${v}`;
  }
  const q = ver ? `?v=${ver}` : '';
  return `${BASE}/file/` + rel.split('/').map(encodeURIComponent).join('/') + q;
}
