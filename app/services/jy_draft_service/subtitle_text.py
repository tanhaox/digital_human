# -*- coding: utf-8 -*-
"""字幕洗涤纯文本逻辑 — TTS 读法 → 阅读文本 (确定性规则, 无 LLM).

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

__all__ = ["wash_subtitle_text", "split_subtitle", "find_highlight_ranges"]

# ── 字幕洗涤 (TTS 读法 → 阅读文本) ──────────────────────────────
# 仅做确定性转换 (保守, 避免 LLM 成本/幻觉); 转换记录进日志供人工抽查。
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUM_WORD = "一二三四五六七八九十百"
_BREAK_PUNCT = "，。！？；：、—…,"


def _cn_to_int(s: str) -> int | None:
    """中文数字(≤999, 十/百 级) → 整数. 解析失败返回 None."""
    try:
        total, num = 0, 0
        for ch in s:
            if ch in _CN_DIGIT:
                num = num * 10 + _CN_DIGIT[ch]
            elif ch == "十":
                total += (num or 1) * 10
                num = 0
            elif ch == "百":
                total += (num or 1) * 100
                num = 0
            else:
                return None
        return total + num
    except Exception:
        return None


def _num_to_cn_pattern(text: str) -> str:
    """'X点Y' (两侧均为中文数字) → 'X.Y': 四点六→4.6, 二十八点三→28.3.

    右侧必须也是数字词, 排除'有点吓人'/'一点心意'这类真中文。
    """
    import re

    pat = re.compile(r"([一二三四五六七八九十百零〇]+)点([一二三四五六七八九十百零〇]+)")

    def repl(m: "re.Match[str]") -> str:
        a, b = _cn_to_int(m.group(1)), _cn_to_int(m.group(2))
        if a is None or b is None:
            return m.group(0)
        # 小数部分去掉无效前导零语义: 二十八点三 → 28.3 (b=3)
        return f"{a}.{b}"

    return pat.sub(repl, text)


def wash_subtitle_text(text: str) -> str:
    """TTS 读法 → 字幕阅读文本 (确定性规则).

    0. 剥情绪标签 (2026-08-17 bug 修复): manifest 文本带 P5 内联标签 [calm]/[serious]/
       [confident]/[surprised] 等 — 不剥则标签进字幕, 且 latin 正则把 calm/serious
       当专名抓成强调大字+挂音效 (三重污染), 必须第一道工序清除
    0.5 剥拼音标注 (2026-08-25): <行|HANG2>/<铟|YIN1> (词表纠音 + 稿内手写临时标注)
       只给 TTS 读, 字幕/观众可见文本一律还原裸字
    1. 'X点Y' 数字读法 → 'X.Y' (四点六 → 4.6)
    2. 拉丁字母后紧跟的中文数字 → 阿拉伯 + 空格 (Grok四点六/Grok4.6 → Grok 4.6;
       Mythos五 → Mythos 5)
    3. 折叠重复标点 (，，→ ，)、去首尾空白
    """
    import re

    from app.services.pinyin_fix import strip_pinyin_marks

    t = text.strip()
    t = strip_pinyin_marks(t)
    t = re.sub(r"\[[a-zA-Z]+\]\s*", "", t)  # 剥 [calm]/[serious] 等情绪标签
    t = _num_to_cn_pattern(t)
    # latin + 中文数字 → latin + 空格 + 阿拉伯
    def _latin_num(m):
        v = _cn_to_int(m.group(2))
        return f"{m.group(1)} {v}" if v is not None else m.group(0)
    t = re.sub(r"([A-Za-z])([一二三四五六七八九])", _latin_num, t)
    # latin + 小数 (X点Y 转换产物如 GLM5.3) → 补空格; 纯整数版本号 (V4) 不动
    t = re.sub(r"([A-Za-z])(\d+\.\d+)", r"\1 \2", t)
    # 折叠重复标点
    t = re.sub(r"([，。！？；、…—])\1+", r"\1", t)
    return t.strip()


def split_subtitle(text: str, limit: int) -> list[str]:
    """超长字幕断句: 优先在标点处断, 无标点则硬断 (CJK 安全)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        # 在 limit 窗口内找最后一个断点标点 (留 6 字下限防碎片)
        window = rest[: limit + 1]
        cut = -1
        for i in range(min(len(window) - 1, limit), 5, -1):
            if window[i] in _BREAK_PUNCT:
                cut = i + 1
                break
        if cut <= 0:
            cut = limit
        chunk = rest[:cut].strip(_BREAK_PUNCT + " ")
        if chunk:
            chunks.append(chunk)
        rest = rest[cut:].lstrip(_BREAK_PUNCT + " ")
    if rest.strip(_BREAK_PUNCT + " "):
        chunks.append(rest.strip(_BREAK_PUNCT + " "))
    return chunks


def find_highlight_ranges(text: str) -> list[tuple[int, int]]:
    """自动划重点: 数字与拉丁专有名词的字符区间 (0-based, 左闭右开).

    洗涤后的字幕里, 阿拉伯数字 (4.6 / 28.3%) 与模型名 (GLM / DeepSeek)
    天然是重点词 — schema 已在剪映"智能划重点"实测确认 (styles 多段 range)。
    """
    import re

    pat = re.compile(r"[0-9][0-9.,]*%?|[A-Za-z][A-Za-z0-9.+-]*")
    return [(m.start(), m.end()) for m in pat.finditer(text)]
