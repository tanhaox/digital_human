# -*- coding: utf-8 -*-
"""quote_card — 截屏卡 (1023 用户令: 3-4 个金句停留画面, 纯文字编辑手段加字).

稿件里的清单式三连句 / 金句断言句是观众截屏收藏的瞬间, 但画面规划层不认识它们。
本模块在剪映草稿生成侧做确定性检测, 命中区间由 job_draft 以文本素材直加
(不动规划层/H3/后期合同 — 用户明令)。

检测两型 (每集上限 4 张, 先到先得):
  L 清单型: ≥3 连续段命中报幕行标签 ^(一|二|三|四|五)(问|句|步|招|条)[:：]
            或 ^第[一二三四五](句|件|步|问|招|条)[:：]
            (ep2 三问 / ep3 判定三问+开工三句 / ep4 开工三句)
  P 平行句型: 3-4 连续段, 有效字数 6~16, 长度离散 ≤1.8, 句末均为 。！？；
            (ep3 白话三句: 有人用/照着做/长本事)
  A 断言型: 单段 ≤22 有效字, "X，是…。" 主系表断言 或 "…不是…，是/而是…" 反差断言
            ("专业自嗨，是管理者最贵的一种浪费。" / "贡献从来不是你觉得好，是外面有人接。")
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

__all__ = ["detect_quote_cards", "QUOTE_CARD_TAIL_SEC"]

# 截屏窗: 末行念完后卡片再停留的秒数 (静置截屏窗口)
QUOTE_CARD_TAIL_SEC = 1.5
# 每集上限 — 多了稀释 (用户口径 3-4 个)
QUOTE_CARD_MAX = 4

_LABELED_RE = re.compile(
    r"^(?:[一二三四五](?:问|句|步|招|条)|第[一二三四五](?:句|件|步|问|招|条))[:：]")
# 报幕元句 (句尾带计数词, "翻成白话就三句。"式) — 是清单的引子不是成员
_COUNT_TAIL_RE = re.compile(r"[一二三四五](?:句|问|条|件|步|招|件事)[。！？；]?$")
_PUNCT_RE = re.compile(r"[。！？；;，,\s:：、——…·\|]")
_ASSERT_IS_RE = re.compile(r"^[^，,。！？]{2,10}[，,]是[^，,。！？]{4,16}[。！？]?$")
_ASSERT_NOT_RE = re.compile(r"^[^，,。！？]{2,12}不是[^，,]{0,12}[，,](?:而是|是)")


def _eff_len(s: str) -> int:
    return len(_PUNCT_RE.sub("", s or ""))


def detect_quote_cards(texts: list[str]) -> list[dict]:
    """逐段文本 → 截屏卡清单. 返回 [{start, end, lines, kind}], 段索引左闭右开.

    texts: 与字幕同源的逐段文本 (manifest/display 行, 原始未洗涤)。
    """
    n = len(texts)
    used: set[int] = set()
    cards: list[dict] = []

    def _add(start: int, end: int, kind: str) -> None:
        if len(cards) >= QUOTE_CARD_MAX:
            return
        for i in range(start, end):
            used.add(i)
        cards.append({"start": start, "end": end,
                      "lines": [texts[i].strip() for i in range(start, end)],
                      "kind": kind})

    # L: 报幕行标签连排
    i = 0
    while i < n and len(cards) < QUOTE_CARD_MAX:
        if i in used or not _LABELED_RE.match(texts[i]):
            i += 1
            continue
        j = i
        while j < n and j not in used and _LABELED_RE.match(texts[j]):
            j += 1
        if j - i >= 3:
            _add(i, j, "list")
        i = j

    # P: 平行短句连排 (排除已用/带标签行 — 标签行走 L; 含逗号冒号的复合句不算,
    #    ep2 拆解一 11/15/16 字三连无逗号过滤会误报 parallel 实锤)
    i = 0
    while i < n and len(cards) < QUOTE_CARD_MAX:
        if i in used:
            i += 1
            continue
        j = i
        while (j < n and j not in used
               and texts[j].rstrip()[-1:] in "。！？；"
               and not _LABELED_RE.match(texts[j])
               and not _COUNT_TAIL_RE.search(texts[j])
               and not re.search(r"[，,：:——]", texts[j])
               and 6 <= _eff_len(texts[j]) <= 16):
            j += 1
        if 3 <= j - i <= 4:
            lens = [_eff_len(texts[k]) for k in range(i, j)]
            if max(lens) / max(min(lens), 1) <= 1.8:
                _add(i, j, "parallel")
        i = max(j, i + 1)

    # A: 断言金句
    for i, t in enumerate(texts):
        if len(cards) >= QUOTE_CARD_MAX:
            break
        if i in used:
            continue
        s = t.strip()
        if 8 <= _eff_len(s) <= 22 and (_ASSERT_IS_RE.match(s) or _ASSERT_NOT_RE.match(s)):
            _add(i, i + 1, "aphorism")

    logger.info("[quote_card] 检出 %d 张: %s", len(cards),
                [f"#{c['start']}-{c['end']}({c['kind']}) {c['lines'][0][:12]}…" for c in cards])
    return cards
