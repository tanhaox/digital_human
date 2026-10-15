// ── 页面上下文: URL 参数 / API 基址 / 媒体资源版本 (集中一处, 便于测试与替换) ──
// typeof location 守卫: 让本模块在 node --test 里也能 import (无 window 环境)
const qs = new URLSearchParams(typeof location === 'undefined' ? '' : location.search);

/** @type {string} 书 ID (URL ?book_id=) */
export const BOOK = qs.get('book_id') || '';
/** @type {number} 集号 (URL ?ep_index=, 默认 1) */
export const EP = parseInt(qs.get('ep_index') || '1', 10) || 1;
/** API 基址: 本集所有端点都在这棵子树下 */
export const BASE = `/api/anim/ep/${encodeURIComponent(BOOK)}/${EP}`;

/**
 * 媒体资源版本: 初始空串 (URL 不拼 ?v=, 干净); 数据刷新时 bump。
 * 为什么: 原实现 fileUrl 每次拼接 ?t=Date.now() — 同一份数据的任何重渲染
 * 都会全量重下所有图/视频 (60 镜剧集 = 几十 MB); 版本号在"数据未变"期间
 * 保持稳定, 浏览器缓存可用, 数据更新后才换 URL 防旧图。
 */
export const media = {
  ver: '',
  bump() { this.ver = Date.now(); },
};
