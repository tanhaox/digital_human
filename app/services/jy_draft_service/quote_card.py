# -*- coding: utf-8 -*-
"""quote_card — 截屏卡 (1023 用户令: 3-4 个金句停留画面, 纯文字编辑手段加字).

稿件里的清单式三连句 / 金句断言句是观众截屏收藏的瞬间, 但画面规划层不认识它们。
本模块在剪映草稿生成侧做确定性检测, 命中区间由 job_draft 以文本素材直加
(不动规划层/H3/后期合同 — 用户明令)。

检测四型 (每集上限 4 张, 先到先得):
  L 清单型: ≥3 连续段命中报幕行标签 (一问：/第一条，…), 序数从一起步且连续递增;
            分隔符冒号/逗号都收, 段首 ≤12 字 "xx：" 引子剥掉不上卡
            (ep2 三问 / ep3 判定三问+开工三句 / ep4 开工三句 / ep6 三条线模板;
            1025 ep6 实锤: 写手逗号式 "第一条，"+首行引子 "给一张可截图模板：第一条，…",
            冒号式 ^ 锚定正则全漏 → 放宽; ep4 报幕行 "第二件，" 靠序数闸不误收)
  P 平行句型: 3-4 连续段, 有效字数 6~16, 长度离散 ≤1.8, 句末均为 。！？；
            (ep3 白话三句: 有人用/照着做/长本事)
  M 口诀型: 单段三短句严格等长 (4-6 字), 全角逗号分隔, 句末 。！？;
            段内无顿号/引号/冒号等杂符 (对仗口诀签名)
            (ep6 "三问定性质，三线定底线，三案防翻车。" / "先判性质，再钉三线，后找反方。")
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

# 1025 ep6 实锤: 报幕标签写成逗号式 ("第一条，") 且首行带引子
# ("给一张可截图模板：第一条，…") — 分隔符收冒号/逗号; 引子 ≤12 字 "xx："。
_LEAD_IN_RE = re.compile(r"^[^：:，。！？\s]{1,12}[：:]")
_LABEL_RE = re.compile(
    r"(?:([一二三四五])(?:问|句|步|招|条)|第([一二三四五])(?:句|件|步|问|招|条))[:：，,]")
_ORD_VAL = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
# M 口诀型: 杂符 (顿号/引号/冒号/分号/英文标点) 一律不收 — ep6 负例
# 7/8/7 长短句与带引号 6/6/6 均靠等长+字符类排除
_MANTRA_CLS = r"[^，,。！？；;：:、\s“”‘’\"'·—…]"
_MANTRA_RE = re.compile(
    rf"({_MANTRA_CLS}{{4,6}})，({_MANTRA_CLS}{{4,6}})，({_MANTRA_CLS}{{4,6}})[。！？]$")
# 报幕元句 (句尾带计数词, "翻成白话就三句。"式) — 是清单的引子不是成员
_COUNT_TAIL_RE = re.compile(r"[一二三四五](?:句|问|条|件|步|招|件事)[。！？；]?$")
_PUNCT_RE = re.compile(r"[。！？；;，,\s:：、——…·\|]")
_ASSERT_IS_RE = re.compile(r"^[^，,。！？]{2,10}[，,]是[^，,。！？]{4,16}[。！？]?$")
_ASSERT_NOT_RE = re.compile(r"^[^，,]{2,12}不是[^，,]{0,12}[，,](?:而是|是)")


def _eff_len(s: str) -> int:
    return len(_PUNCT_RE.sub("", s or ""))


def _label_at(text: str) -> tuple[int, int] | None:
    """段首报幕标签 → (序数, 标签起点字符位); 无标签 None.

    先试段首, 再试剥引子后 — 引子正则不能先跑: 冒号式标签自己就是
    "xx：" 形 ("第一句：…"), 先跑会把标签当引子吞掉。标签起点供卡上行
    剥引子用 (引子是报幕, 不上卡)。
    """
    t = (text or "").strip()
    m = _LABEL_RE.match(t)
    if m:
        return _ORD_VAL[m.group(1) or m.group(2)], 0
    lead = _LEAD_IN_RE.match(t)
    if lead:
        m = _LABEL_RE.match(t, lead.end())
        if m:
            return _ORD_VAL[m.group(1) or m.group(2)], lead.end()
    return None


def detect_quote_cards(texts: list[str]) -> list[dict]:
    """逐段文本 → 截屏卡清单. 返回 [{start, end, lines, kind}], 段索引左闭右开.

    texts: 与字幕同源的逐段文本 (manifest/display 行, 原始未洗涤)。
    """
    n = len(texts)
    used: set[int] = set()
    cards: list[dict] = []

    def _add(start: int, end: int, kind: str,
             lines: list[str] | None = None) -> None:
        if len(cards) >= QUOTE_CARD_MAX:
            return
        for i in range(start, end):
            used.add(i)
        cards.append({"start": start, "end": end,
                      "lines": lines or [texts[i].strip() for i in range(start, end)],
                      "kind": kind})

    # L: 报幕行标签连排 (序数从一起步且连续递增 — ep4 报幕行 "第二件，" 排在
    # 清单前, 放开逗号分隔符后会被误收进卡, 序数闸挡住)
    i = 0
    while i < n and len(cards) < QUOTE_CARD_MAX:
        _l0 = _label_at(texts[i])
        if i in used or not _l0 or _l0[0] != 1:
            i += 1
            continue
        j, _next_ord = i, 1
        while j < n and j not in used:
            _lj = _label_at(texts[j])
            if not _lj or _lj[0] != _next_ord:
                break
            j += 1
            _next_ord += 1
        if j - i >= 3:
            _lines = []
            for k in range(i, j):
                _lk = _label_at(texts[k])
                _lines.append(texts[k].strip()[_lk[1]:])  # 剥引子: 报幕不上卡
            _add(i, j, "list", lines=_lines)
        i = max(j, i + 1)

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
               and not _label_at(texts[j])
               and not _COUNT_TAIL_RE.search(texts[j])
               and not re.search(r"[，,：:——]", texts[j])
               and 6 <= _eff_len(texts[j]) <= 16):
            j += 1
        if 3 <= j - i <= 4:
            lens = [_eff_len(texts[k]) for k in range(i, j)]
            if max(lens) / max(min(lens), 1) <= 1.8:
                _add(i, j, "parallel")
        i = max(j, i + 1)

    # M: 对仗口诀 (1025 ep6 实锤: 三短句等长口诀三型全漏 → 新增;
    #    等长由 _MANTRA_RE 字数区间 + 下方三段长度全等共同保证)
    for i, t in enumerate(texts):
        if len(cards) >= QUOTE_CARD_MAX:
            break
        if i in used:
            continue
        m = _MANTRA_RE.match(t.strip())
        if m and len({len(g) for g in m.groups()}) == 1:
            _add(i, i + 1, "mantra")

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
