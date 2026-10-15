"""Parse broadcast script into sentence-level segments.

``||`` is a TTS pause hint, NOT a segment boundary. Segments are split by
sentence delimiters (。！？；) so that each segment is a complete utterance.
"""
from __future__ import annotations

import re
from typing import Any, Literal

SegmentType = Literal["opening", "hook", "body", "cta", "ending", "references"]

_SENTENCE_DELIMS = "。！？；"

# 拆书弹性时间标签: 【A-B秒｜段名】/【钩子】/【核心概念拆解（一）｜副题】.
# 生成稿结构标记, 不发音. 0917 放宽: 时间数字可选 (人工存档【钩子】实锤),
# 与 book_service.module_map.MODULE_LABEL_RE 同口径 — 任何【】标签行都剥,
# 含【双评论钩子】等非标准名 (此前带时间版不认 → 会进 TTS 被念出)。
_LABEL_RE = re.compile(
    r"【\s*(?:\d+\s*-\s*\d+\s*秒\s*[｜|]\s*)?([^】｜|]+?)\s*(?:[｜|]\s*[^】]+?)?\s*】")
# 拆书六段名 (与 orchestrator._LABEL_BASE 对齐)
_BOOK_LABELS = ["钩子", "回顾+引入", "核心概念拆解（一）", "核心概念拆解（二）",
                "核心概念拆解（三）", "总结+下期预告"]

# 句子有效字数下限: 低于此值的短句 (如 "您好。" "哎哟！") 合并进相邻句,
# 避免产生 <2s 的碎音频/碎镜头 (中文口播约 4-5 字/秒)
_MIN_SENTENCE_CHARS = 10


def _effective_len(text: str) -> int:
    """可发音字符数: 去掉情绪标签/停顿符/标点后的长度."""
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\([^)]+\)", "", text)
    text = text.replace("||", "")
    text = re.sub(r"[。！？；，,\s]", "", text)
    return len(text)


def _merge_short_sentences(sentences: list[str], min_chars: int = _MIN_SENTENCE_CHARS) -> list[str]:
    """合并过短句, 保证每句有足够发音时长.

    策略: 短句优先并入前一句 (语义上多为补充/感叹); 首句过短则并入后一句.
    合并后再扫一遍, 直到没有短句或只剩一句.
    """
    merged = list(sentences)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, s in enumerate(merged):
            if _effective_len(s) >= min_chars:
                continue
            if i > 0:
                merged[i - 1] = merged[i - 1] + s
            else:
                merged[1] = s + merged[1]
            merged.pop(i)
            changed = True
            break
    return merged


def _detect_segment_type(
    text: str,
    index: int,
    total: int,
    fixed_opening: str | None = None,
    fixed_ending: str | None = None,
) -> SegmentType:
    """Heuristic classification for segment type."""
    if fixed_opening and text.startswith(fixed_opening[:8]):
        return "opening"
    if fixed_ending and text.startswith(fixed_ending[:8]):
        return "ending"
    if index == 0:
        return "opening" if "大家好" in text or "我是" in text else "hook"
    if index == total - 1:
        return "ending" if any(w in text for w in ("关注", "点赞", "收藏", "再见", "下期")) else "cta"
    if any(w in text for w in ("关注我", "点赞", "收藏", "转发", "评论区", "下期见")):
        return "cta"
    return "body"


def _clean_text(text: str) -> str:
    """Normalize control characters.

    - Removes redundant comma + pause marker combinations such as ",||" or
      "，||" and leaves only the comma.
    - Collapses consecutive whitespace.
    """
    text = re.sub(r"[,，]\s*\|\|", "，", text)
    text = re.sub(r"\|\|\s*[,，]", "，", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


_URL_PREFIX_RE = re.compile(r"https?://[^\s。！？；,，]+")


def _split_sentences(text: str) -> list[str]:
    """Split text into complete sentences, preserving trailing delimiters.

    ``||`` is treated as a pause hint inside a sentence, never as a boundary.
    URLs (http/https) are treated as atomic tokens — sentence delimiters
    inside or immediately after a URL do NOT cause a split, so that numbered
    reference items like ``1. 新华网。https://…。`` stay intact.
    """
    text = _clean_text(text)
    if not text:
        return []

    sentences: list[str] = []
    current = ""
    i = 0
    n = len(text)
    while i < n:
        # Check if a URL starts at position i
        m = _URL_PREFIX_RE.match(text, i)
        if m:
            # Consume the entire URL as an atomic token (no splitting inside)
            current += m.group()
            i = m.end()
            continue
        ch = text[i]
        current += ch
        i += 1
        if ch in _SENTENCE_DELIMS:
            stripped = current.strip()
            if stripped:
                sentences.append(stripped)
            current = ""
    if current.strip():
        fragment = current.strip()
        if sentences:
            sentences[-1] = (sentences[-1] + fragment).strip()
        else:
            sentences.append(fragment)
    return sentences


# ---------------------------------------------------------------------------
# 参考来源段检测
# ---------------------------------------------------------------------------

_REFERENCE_HEADER_RE = re.compile(
    r"(参考|引用|来源|参考资料|参考来源|公开报道|官方文件)", re.IGNORECASE
)
_NUMBERED_URL_RE = re.compile(
    r"^\s*\d+[\.\)、]\s*.+https?://", re.IGNORECASE
)


def _is_reference_header(text: str) -> bool:
    """Detect reference section start.

    Matches patterns like:
    - '本文参考了以下公开报道：'  (classic header with 以下/如下/trailing colon)
    - '参考来源：1. 新华网。https://…'  (header + first item on same line)
    """
    if not _REFERENCE_HEADER_RE.search(text):
        return False
    # Classic header: ends with colon or contains 以下/如下
    if "以下" in text or "如下" in text or text.rstrip().endswith(("：", ":")):
        return True
    # Inline header: reference keyword + URL on same line
    if "http" in text:
        return True
    return False


_NUMBERED_PREFIX_RE = re.compile(r"\d+[\.\)、]")


def _is_reference_item(text: str) -> bool:
    """带编号 + URL 的条目, 如 '1. 商务部官网：...https://...'."""
    return bool(_NUMBERED_URL_RE.search(text))


def _is_bare_url(text: str) -> bool:
    """纯 URL 行 (断句后 URL 被单独切出的情况)."""
    stripped = text.strip().rstrip("。！？；")
    return stripped.startswith("http://") or stripped.startswith("https://")


def _is_reference_header_with_number(text: str) -> bool:
    """参考关键词 + 编号但无 URL (header 与首条 item 同行被断句后的前半)."""
    return bool(_REFERENCE_HEADER_RE.search(text)) and bool(_NUMBERED_PREFIX_RE.search(text))


def _parse_control_chars(text: str) -> dict[str, Any]:
    """Extract TTS control characters from segment text."""
    return {
        "pauses": text.count("||"),
        "periods": text.count("。"),
        "exclamations": text.count("！"),
        "questions": text.count("？"),
    }


def clean_episode_script(script_text: str) -> tuple[str, list[str]]:
    """拆书逐集稿清洗 (2026-08-20): 剥离【A-B秒｜段名】时间标签行 + 校验六段完整.

    生成稿的六段弹性标签是结构标记, 进音频会读出"零到二十二秒,钩子" → 必须剥离.
    同时校验六段是否齐全/有序/无重复, 缺失或重复记入 issues 供人工处理.
    返回 (清洗后文本, issues). 标签行从文本移除, 不影响 parse_script 后续拆句.
    """
    issues: list[str] = []
    lines = script_text.splitlines()
    kept: list[str] = []
    found: list[str] = []  # 按出现顺序记录标签段名 (规范化, 去空白)
    for line in lines:
        m = _LABEL_RE.match(line.strip())
        if m:
            found.append(re.sub(r"\s+", "", m.group(1) if m.groups() else
                                m.group(0).split("｜")[-1].rstrip("】")))
            continue  # 标签行不进语音
        # 打字卡行 (0912 ep1 收益承诺开局): 剪辑层专用文案, 无口播 — 不进 TTS
        if line.strip().startswith("打字卡：") or line.strip().startswith("打字卡:"):
            continue
        kept.append(line)

    # 六段校验: 齐全 / 有序 / 无重复 (0917 段名规范化后比对, 空格变体不再误报)
    _norm_labels = [re.sub(r"\s+", "", n) for n in _BOOK_LABELS]
    if not found:
        issues.append("缺少六段时间标签 (结构异常, 建议重新生成)")
    else:
        if len(found) != len(set(found)):
            dup = [n for n in found if found.count(n) > 1]
            issues.append(f"时间标签重复: {dict.fromkeys(dup)}")
        missing = [n for n in _norm_labels if n not in found]
        if missing:
            issues.append(f"缺少时间标签段: {missing}")
        ordered = [n for n in found if n in _norm_labels]
        base_order = [n for n in _norm_labels if n in found]
        if ordered != base_order:
            issues.append("时间标签顺序异常 (建议重新生成)")

    cleaned = "\n".join(kept).strip()
    return cleaned, issues


def parse_script(
    script_text: str,
    fixed_opening: str | None = None,
    fixed_ending: str | None = None,
) -> list[dict[str, Any]]:
    """Split script into segments, one per complete sentence.

    Sentence delimiters (。！？；) define segment boundaries. ``||`` is kept
    inside the segment as a pause hint for the TTS/post-processing stage.

    Reference detection runs BEFORE short-sentence merging so that URL-only
    lines are never merged into adjacent body text.
    """
    lines = [line.strip() for line in script_text.splitlines() if line.strip()]

    sentences: list[str] = []
    for line in lines:
        sentences.extend(_split_sentences(line))

    # Remove exact duplicates
    seen: set[str] = set()
    unique_sentences: list[str] = []
    for s in sentences:
        if s in seen:
            continue
        seen.add(s)
        unique_sentences.append(s)

    # ── Phase 1: detect references BEFORE merging ──
    ref_flags: list[bool] = [False] * len(unique_sentences)
    in_references = False
    for idx, text in enumerate(unique_sentences):
        is_ref = False
        if not in_references:
            if _is_reference_header(text):
                in_references = True
                is_ref = True
            elif _is_reference_header_with_number(text):
                in_references = True
                is_ref = True
            elif _is_reference_item(text):
                in_references = True
                is_ref = True
        else:
            if _is_reference_item(text) or _is_bare_url(text):
                is_ref = True
            elif _REFERENCE_HEADER_RE.search(text) or _NUMBERED_PREFIX_RE.search(text):
                is_ref = True
            else:
                in_references = False
        ref_flags[idx] = is_ref or in_references

    # ── Phase 2: merge short sentences ONLY among non-reference sentences ──
    body_sentences = [s for i, s in enumerate(unique_sentences) if not ref_flags[i]]
    ref_sentences = [s for i, s in enumerate(unique_sentences) if ref_flags[i]]
    body_sentences = _merge_short_sentences(body_sentences)

    # Rebuild ordered list: body (merged) first, then references appended at end
    # (references are always at the tail of the script anyway)
    ordered = body_sentences + ref_sentences

    # ── Phase 3: build segments ──
    segments = []
    total = len(ordered)
    for idx, text in enumerate(ordered):
        is_ref = text in ref_sentences
        if is_ref:
            seg_type: SegmentType = "references"
            selected = False
        else:
            seg_type = _detect_segment_type(text, idx, total, fixed_opening, fixed_ending)
            selected = True

        segments.append(
            {
                "line_index": idx,
                "text": text,
                "control_chars": _parse_control_chars(text),
                "segment_type": seg_type,
                "selected_for_host": selected,
                "host_order": idx,
            }
        )
    return segments
