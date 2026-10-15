# -*- coding: utf-8 -*-
"""豆瓣书页爬虫 (2026-08-23) — 搜索书名 → subject id → 高赞短评.

拆书素材/评论层用: 真实读者高赞短评比 LLM 生成的评论层真实得多,
且含真人槽点(康复太快/结尾投资房地产/读过的觉得浅显) — 做稿时可规避或回应.

两步一体: search_book(书名) 拿 subject id, fetch_comments(id) 爬高赞短评.
豆瓣反爬: 仅书页/comments 静态页, 无登录墙; 带常规 UA, 低频调用.
"""
from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

__all__ = ["search_book", "fetch_comments", "fetch_reviews", "book_highlights"]

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
_SESSION = requests.Session()
_SESSION.headers.update(_UA)

_COMMENT_RE = re.compile(r'class="comment-item[^"]*"[^>]*>(.*?)</li>', re.S)


def search_book(title: str, limit: int = 5) -> list[dict]:
    """豆瓣搜索 → [{id, title, author, url}]. suggest JSON 接口, 第一个通常是正版."""
    try:
        r = _SESSION.get("https://book.douban.com/j/subject_suggest",
                         params={"q": title}, timeout=20)
        r.raise_for_status()
        out = []
        for d in r.json()[:limit]:
            sid = d.get("id")
            if not sid:
                continue
            out.append({"id": sid, "title": d.get("title", ""), "author": d.get("author", ""),
                        "url": f"https://book.douban.com/subject/{sid}/"})
        return out
    except Exception as exc:
        logger.warning("[douban] 搜索失败 %s: %s", title, exc)
        return []


def _parse_comment(block: str) -> dict:
    votes = re.search(r'class="vote-count"[^>]*>(\d+)<', block)
    star = re.search(r"allstar(\d{2})", block)  # 豆瓣50分制: allstar50=5星
    author = re.search(r'class="comment-info"[^>]*>.*?<a[^>]*>([^<]+)</a>', block, re.S)
    m = re.search(r'class="short"[^>]*>(.*?)</span>', block, re.S)
    text = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    return {"votes": int(votes.group(1)) if votes else 0,
            "star": int(star.group(1)) / 10 if star else None,
            "author": author.group(1).strip() if author else "",
            "text": text}


def fetch_comments(subject_id: str, limit: int = 20, sort: str = "new_score") -> list[dict]:
    """高赞短评 → [{votes, star, text, author}].

    comments 页按'有用'(new_score)排序 = 高赞优先; 不传分页只取首屏(约20条).
    """
    try:
        r = _SESSION.get(f"https://book.douban.com/subject/{subject_id}/comments",
                         params={"sort": sort, "status": "P"}, timeout=20)
        r.raise_for_status()
        blocks = _COMMENT_RE.findall(r.text)
        out = [_parse_comment(b) for b in blocks]
        out = [c for c in out if c["text"]]
        return sorted(out, key=lambda c: -c["votes"])[:limit]
    except Exception as exc:
        logger.warning("[douban] 短评抓取失败 subject=%s: %s", subject_id, exc)
        return []


def _fetch_review_body(review_id: str, limit: int = 2500) -> str:
    """单篇书评正文 (link-report 区)."""
    try:
        r = _SESSION.get(f"https://book.douban.com/review/{review_id}/", timeout=20)
        r.raise_for_status()
        m = re.search(r'id="link-report[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>',
                      r.text, re.S)
        if not m:
            return ""
        txt = re.sub(r"<[^>]+>", "", m.group(1))
        return re.sub(r"\s+", " ", txt).strip()[:limit]
    except Exception as exc:
        logger.warning("[douban] 书评正文失败 review=%s: %s", review_id, exc)
        return ""


def fetch_reviews(subject_id: str, limit: int = 5, body_limit: int = 2500) -> list[dict]:
    """书评(长评) → [{id, title, author, star, votes, summary, body}].

    短评=情绪/共识; 书评=笔记/深入分析(核心知识点梳理、咨询过程分析) — 深度素材.
    列表页拿元数据, 按有用数取 top N 爬单篇正文.
    """
    try:
        r = _SESSION.get(f"https://book.douban.com/subject/{subject_id}/reviews", timeout=20)
        r.raise_for_status()
        t = r.text
        ids = re.findall(r'class="main review-item" id="(\d+)"', t)
        out: list[dict] = []
        for i, rid in enumerate(ids):
            start = t.find(f'id="{rid}"')
            end = t.find(f'id="{ids[i + 1]}"') if i + 1 < len(ids) else len(t)
            block = t[start:end]
            title = re.search(r"<h2><a[^>]*>(.*?)</a></h2>", block, re.S)
            author = re.search(r'class="name">(.*?)</a>', block, re.S)
            star = re.search(r"allstar(\d{2})", block)
            votes = re.search(r'class="action-btn up"[^>]*title="有用">\s*<span[^>]*>(\d+)', block)
            if not votes:
                votes = re.search(r'title="有用">.*?(\d+)\s*</a>', block, re.S)
            short = re.search(r'class="short-content[^"]*"[^>]*>(.*?)</div>', block, re.S)
            out.append({
                "id": rid,
                "title": re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else "",
                "author": author.group(1).strip() if author else "",
                "star": int(star.group(1)) / 10 if star else None,
                "votes": int(votes.group(1)) if votes else 0,
                "summary": re.sub(r"<[^>]+>", "", short.group(1))[:200] if short else "",
                "body": "",
            })
        out = [x for x in out if x["title"]]
        for x in sorted(out, key=lambda c: -c["votes"])[:limit]:
            x["body"] = _fetch_review_body(x["id"], body_limit)
        return out
    except Exception as exc:
        logger.warning("[douban] 书评抓取失败 subject=%s: %s", subject_id, exc)
        return []


def book_highlights(title: str, limit: int = 20) -> dict:
    """一步到位: 搜书名 → 高赞短评 + 高赞书评."""
    hits = search_book(title)
    if not hits:
        return {"subject": None, "comments": [], "reviews": []}
    best = hits[0]
    return {"subject": best, "comments": fetch_comments(best["id"], limit=limit),
            "reviews": fetch_reviews(best["id"], limit=5)}


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "蛤蟆先生去看心理医生"
    hits = search_book(q)
    print(f"搜索《{q}》: {len(hits)} 个结果")
    for h in hits[:3]:
        print(f"  id={h['id']} {h['title']} {h['author']}")
    if hits:
        cs = fetch_comments(hits[0]["id"])
        print(f"高赞短评 {len(cs)} 条:")
        for c in cs[:6]:
            print(f"  [{c['votes']}赞·{c['star']}星] {c['text'][:50]} ({c['author']})")
