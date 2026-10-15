/* 拆书线统一灯箱 (0923 用户令: 一律按 publish_card 效果升级) — 单一事实源.
 * 用法: 任意 <img onclick="lbOpen(this.src, '说明文字')"> 或传画廊数组:
 *   lbOpen(src, cap, [{src,cap},...])  ← → 切换 / Esc / ✕ / 点击图外关闭.
 * 各页只需 <script src="/web/lightbox.js"></script>, 零依赖. */
(function () {
  let lb = null, items = [], idx = 0;

  function ensure() {
    if (lb) return;
    const st = document.createElement('style');
    st.textContent = [
      '#lb-shared{position:fixed;inset:0;background:rgba(10,8,6,.94);z-index:9999;display:flex;',
      'align-items:center;justify-content:center;flex-direction:column;gap:.6rem;cursor:zoom-out}',
      '#lb-shared[hidden]{display:none}',  // 1005 修: display:flex 压过 UA [hidden] → 关闭失效全页黑屏死锁
      '#lb-shared img{max-width:96vw;max-height:88vh;border:1px solid #4a4234;border-radius:4px;',
      'box-shadow:0 12px 80px rgba(0,0,0,.7);cursor:default}',
      '#lb-shared .lb-cap{color:#a2988a;font-size:.82rem}',
      '#lb-shared .lb-nav{color:#6f665a;font-size:.72rem}',
      '#lb-shared .lb-x{position:absolute;top:1rem;right:1.4rem;color:#a2988a;font-size:1.6rem;cursor:pointer}'
    ].join('');
    document.head.appendChild(st);
    lb = document.createElement('div');
    lb.id = 'lb-shared';
    lb.hidden = true;
    lb.innerHTML = '<span class="lb-x">✕</span><img alt="放大预览">'
      + '<div class="lb-cap"></div><div class="lb-nav"></div>';
    document.body.appendChild(lb);
    lb.addEventListener('click', e => {
      if (e.target === lb || e.target.classList.contains('lb-x')) hide();
    });
    document.addEventListener('keydown', e => {
      if (lb.hidden || !lb.isConnected) return;
      if (e.key === 'Escape') hide();
      if (e.key === 'ArrowRight') show(idx + 1);
      if (e.key === 'ArrowLeft') show(idx - 1);
    });
  }

  function show(i) {
    if (!items.length) return;
    idx = (i + items.length) % items.length;
    const it = items[idx];
    lb.querySelector('img').src = it.src;
    lb.querySelector('.lb-cap').textContent =
      (it.cap || '') + (items.length > 1 ? ` (${idx + 1}/${items.length})` : '');
    lb.querySelector('.lb-nav').textContent =
      items.length > 1 ? '← → 切换 · Esc 关闭 · 点击图外关闭' : 'Esc 关闭 · 点击图外关闭';
  }

  function hide() { lb.hidden = true; lb.querySelector('img').src = ''; }

  window.lbOpen = function (src, cap, gallery) {
    ensure();
    items = Array.isArray(gallery) && gallery.length ? gallery.slice() : [{ src: src, cap: cap }];
    const j = items.findIndex(x => x.src === src);
    lb.hidden = false;
    show(j >= 0 ? j : 0);
  };
})();
