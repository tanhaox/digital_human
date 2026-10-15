"""URL content fetcher for article auto-fill (ID-001)."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)


# Site-specific extractors keyed by hostname fragment.
_TOUTIAO_HOSTS = {"toutiao.com", "toutiao.cn", "www.toutiao.com", "www.toutiao.cn"}

# ── 证据图抓取 (2026-09-04 证据图管线①) ──
# 装饰图黑名单: icon/logo/头像/表情/占位符/按钮等 URL 模式
_IMG_JUNK_RE = re.compile(
    r"icon|logo|avatar|emoji|spacer|pixel|badge|qrcode|favicon|rating|arrow|"
    r"btn|button|loading|placeholder|/small_|face/|\.svg|"
    r"login|logout|register|/thumb_|_default\.|share[_-]|follow[_-]|weibo[_-]?(btn|share)|"
    r"app\d?[x@]|_app\d|download[_-]|qr[_-]?code|comment[_-]|zanshang|reward",
    re.I,
)
# 懒加载属性优先于 src (正文图多为 data-src 懒加载, src 常是占位 1px 图)
_IMG_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "src")
_MAX_IMAGES_PER_PAGE = 8
_IMG_MIN_DIM = 400


def extract_images(html: str, base_url: str = "",
                   cap: int = _MAX_IMAGES_PER_PAGE) -> list[dict[str, Any]]:
    """提取页面 <img> 候选证据图 (仅 URL 层; 下载/质检在 evidence_service).

    过滤: 仅 http(s)、剔装饰图黑名单、属性标明宽度 < 400px 剔除 (缺尺寸保留
    交 VLM 判)、懒加载取 data-src 族、相对路径以 base_url 补全。每页 cap 张。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        src = None
        for attr in _IMG_SRC_ATTRS:
            am = re.search(rf'{attr}\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if am and am.group(1).strip():
                src = am.group(1).strip()
                break
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif not src.startswith(("http://", "https://")):
            if not base_url:
                continue
            src = urljoin(base_url, src)
        if not src.startswith(("http://", "https://")):
            continue
        if _IMG_JUNK_RE.search(src):
            continue
        wm = re.search(r'width\s*=\s*["\']?(\d+)', tag, re.I)
        if wm and int(wm.group(1)) < _IMG_MIN_DIM:
            continue
        hm = re.search(r'height\s*=\s*["\']?(\d+)', tag, re.I)
        am = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', tag, re.I)
        key = src.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "url": src,
            "alt": (_html_unescape(am.group(1)).strip() if am else "")[:120],
            "w": int(wm.group(1)) if wm else None,
            "h": int(hm.group(1)) if hm else None,
        })
        if len(out) >= cap:
            break
    return out


def _request_html(url: str, timeout: int) -> str:
    """下载页面 HTML (含编码兜底). 失败抛异常, 调用方自决降级."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;" "q=0.9,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.toutiao.com/",
        "Cache-Control": "no-cache",
    }
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    # Best-effort encoding handling; prefer apparent_encoding when confident.
    if resp.encoding and resp.encoding.lower() in {"iso-8859-1", "ascii"}:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def fetch_html(url: str, timeout: int = 15) -> str | None:
    """下载页面 HTML 原文 (证据图扫图专用, 不做正文解析). 失败返回 None."""
    try:
        return _request_html(url, timeout)
    except Exception as exc:
        logger.debug("fetch_html failed for %s: %s", url, exc)
        return None


def fetch_url(url: str, timeout: int = 15, with_images: bool = False) -> dict[str, Any]:
    """Fetch a URL and return {title, source_url, raw_text, ok, error}.

    The strategy is intentionally layered:
      1. Try site-specific rules (Toutiao article pages).
      2. Fall back to generic meta + paragraph extraction.

    with_images=True 时附带提取本页 <img> 候选图 (零额外请求), 返回 dict 多一个
    images 字段 (证据图管线①; 关闭时行为与历史完全一致)。
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"ok": False, "error": "无效的 URL", "title": None, "source_url": url, "raw_text": None}

    try:
        html = _request_html(url, timeout)
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "抓取超时，请手动粘贴", "title": None, "source_url": url, "raw_text": None}
    except Exception as exc:
        logger.warning("fetch_url failed for %s: %s", url, exc)
        return {"ok": False, "error": f"抓取失败: {exc}", "title": None, "source_url": url, "raw_text": None}

    if parsed.netloc in _TOUTIAO_HOSTS or parsed.netloc.endswith("toutiao.com") or parsed.netloc.endswith("toutiao.cn"):
        result = _extract_toutiao(html, url)
    else:
        result = _extract_generic(html, url)

    if not result["ok"]:
        return result

    # Merge segments back into paragraphs; remove empty/whitespace-only pieces.
    raw_text = _normalize_text(result.get("raw_text") or "")
    title = _normalize_text(result.get("title") or "", single_line=True)

    if not title and not raw_text:
        return {
            "ok": False,
            "error": "未能从页面解析出标题或正文",
            "title": None,
            "source_url": url,
            "raw_text": None,
        }

    result = {
        "ok": True,
        "error": None,
        "title": title or None,
        "source_url": url,
        "raw_text": raw_text or None,
    }
    if with_images:
        result["images"] = extract_images(html, url)
    return result


def _extract_toutiao(html: str, url: str) -> dict[str, Any]:
    """Extract title/body from Toutiao article HTML."""
    title = _extract_meta(html, "og:title") or _extract_title(html)
    # Toutiao article body is usually in <div class="article-content">.
    body_match = re.search(r'<div[^>]*class=["\']article-content["\'][^>]*>(.*?)</div>\s*<(div|footer|script|section|article|div[^>]*class=["\'][^"\']*(share|comment|related))', html, re.S | re.I)
    if body_match:
        raw_text = _strip_tags(body_match.group(1))
    else:
        # Fallback: look for any large block of text near article-content markers.
        raw_text = _extract_generic(html, url)["raw_text"]
    return {"ok": True, "title": title, "raw_text": raw_text}


def _extract_generic(html: str, url: str) -> dict[str, Any]:
    """Generic extraction from meta tags and <p> paragraphs."""
    title = _extract_meta(html, "og:title") or _extract_meta(html, "twitter:title") or _extract_title(html)
    # Prefer JSON-LD articleBody when available.
    jsonld_body = _extract_jsonld_body(html)
    if jsonld_body:
        return {"ok": True, "title": title, "raw_text": jsonld_body}

    paragraphs = _extract_paragraphs(html)
    raw_text = "\n\n".join(paragraphs)
    return {"ok": True, "title": title, "raw_text": raw_text}


def _extract_meta(html: str, prop: str) -> str | None:
    """Extract content of a <meta property/name=... content=...> tag."""
    # Try property="..." first, then name="..."
    for attr in ("property", "name"):
        pattern = rf'<meta\s+{attr}=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)["\']'
        match = re.search(pattern, html, re.I)
        if match:
            return _html_unescape(match.group(1).strip())
    return None


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if match:
        text = _html_unescape(match.group(1).strip())
        # Strip common site suffixes like " - 今日头条".
        text = re.sub(r"[\s\-_|]+[^|]*?$", "", text)
        return text.strip() or None
    return None


def _extract_jsonld_body(html: str) -> str | None:
    """Extract articleBody from JSON-LD script tags."""
    for match in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        text = match.group(1)
        try:
            import json

            data = json.loads(text)
        except Exception:
            continue
        if isinstance(data, dict):
            body = data.get("articleBody")
            if isinstance(body, str) and body.strip():
                return body.strip()
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    body = item.get("articleBody")
                    if isinstance(body, str) and body.strip():
                        return body.strip()
    return None


def _extract_paragraphs(html: str) -> list[str]:
    """Extract readable paragraphs from <p>, <article>, <section>, and <div> tags.

    Heuristic: keep blocks with reasonable Chinese/ASCII text length and sentence-like
    punctuation, dropping navigation/footer-style fragments.
    """
    candidates: list[str] = []

    # Extract <p> tags first (most article sites).
    for match in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.S | re.I):
        text = _strip_tags(match.group(1))
        if _is_paragraph(text):
            candidates.append(text)

    if len(candidates) >= 2:
        return candidates

    # Fallback: scan larger containers.
    for tag in ("article", "section", "div"):
        for match in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S | re.I):
            text = _strip_tags(match.group(1))
            if _is_paragraph(text):
                # Split into sentences/paragraphs by sentence-ending punctuation.
                parts = re.split(r"(?<=[。！？.!?])\s+", text)
                candidates.extend(p for p in parts if _is_paragraph(p))
        if candidates:
            break

    return candidates


def _is_paragraph(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 15:
        return False
    # Reject pure dates, phone numbers, copyright fragments, navigation anchors.
    if re.fullmatch(r"[\d\-:/.\s]+", stripped):
        return False
    if re.search(r"copyright|©|免责声明|相关推荐|阅读下一篇|点击关注|分享至|点赞|收藏", stripped, re.I):
        return False
    # Prefer text with Chinese characters or reasonable Latin density.
    has_chinese = bool(re.search(r"[一-鿿]", stripped))
    word_like_count = len(re.findall(r"[a-zA-Z0-9一-鿿]{2,}", stripped))
    return has_chinese or word_like_count >= 3


def _strip_tags(html: str) -> str:
    """Remove HTML tags, scripts, styles, and normalize whitespace."""
    # Drop script/style blocks first.
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    # Drop remaining tags.
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities.
    text = _html_unescape(text)
    # Collapse whitespace.
    text = re.sub(r"[\s ]+", " ", text)
    return text.strip()


def _html_unescape(text: str) -> str:
    """Decode a handful of common HTML entities."""
    replacements = {
        "&nbsp;": " ",
        "&quot;": '"',
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&#39;": "'",
        "&mdash;": "—",
        "&ndash;": "–",
        "&hellip;": "…",
        "&ldquo;": "\"",
        "&rdquo;": "\"",
        "&lsquo;": "'",
        "&rsquo;": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Numeric entities.
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return text


def _normalize_text(text: str, single_line: bool = False) -> str:
    text = text.strip()
    if single_line:
        text = re.sub(r"\s+", " ", text)
    else:
        # Preserve paragraph breaks but collapse runs of blank lines.
        text = re.sub(r"[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text
