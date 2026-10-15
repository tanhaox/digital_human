/* 书级流程导航 (1012 用户令 "四页只能回退或绕一圈"): 六页互达单源。
   用法: 页面放 <div class="flownav" data-page="…"></div> 并引本文件;
   data-page ∈ content|story|board|bible|chars|anim, 当前步高亮不可点,
   其余步带当前 book_id 直达, 尾部挂书库出口。 */
(function () {
  'use strict';
  var STEPS = [
    ['content', '/web/books_content.html', '大纲'],
    ['story', '/web/books_story.html', '总纲/逐集'],
    ['board', '/web/style_board.html', '🎨 风格选型'],
    ['bible', '/web/style_bible.html', '📖 艺术圣经'],
    ['chars', '/web/style_characters.html', '👥 人物造型'],
    ['anim', '/web/anim.html', '🎬 动画工坊'],
  ];
  var bid = new URLSearchParams(location.search).get('book_id') || '';
  document.querySelectorAll('.flownav[data-page]').forEach(function (el) {
    var cur = el.dataset.page;
    el.innerHTML = STEPS.map(function (s) {
      if (s[0] === cur) return '<span class="cur">' + s[2] + '</span>';
      return '<a href="' + s[1] + (bid ? '?book_id=' + encodeURIComponent(bid) : '') + '">'
        + s[2] + '</a>';
    }).join('<span class="sep">›</span>')
      + '<a class="hub" href="/web/books.html">书库</a>';
  });
})();
