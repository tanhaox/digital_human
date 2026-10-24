# -*- coding: utf-8 -*-
"""quote_card — 截屏卡 (1023 用户令: 3-4 个金句停留画面, 纯文字编辑手段加字).

稿件里的清单式三连句 / 金句断言句是观众截屏收藏的瞬间, 但画面规划层不认识它们。
命中区间由 job_draft 以文本素材直加 (不动规划层/H3/后期合同 — 用户明令)。
1025 用户令: 正则抓稿式漂移极难, LLM 选区间简单 → detect_quote_cards_smart
LLM 主判 (只选区间, 卡上文字一律取原文切片 — 零幻觉) + 下列正则四型兜底。

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

__all__ = ["detect_quote_cards", "detect_quote_cards_smart", "wash_card_lines", "QUOTE_CARD_TAIL_SEC"]

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

# ── 1025 验收实锤: 卡上文字必须干净 — 停顿标记/报幕引子/拼音/markdown 全剥 ──
_CARD_PAUSE_RE = re.compile(r"-\d+(?:\.\d+)?s-")          # TTS 停顿标记 -0.8s-
_CARD_PINYIN_RE = re.compile(r"<([^|>]+)\|[^>]+>")          # 拼音标记 <字|PINYIN>
_CARD_MD_RE = re.compile(r"[*_]+")                            # markdown 强调符
# 冒号前 ≤10 字无逗号的句首报幕短语 (记一句话就行：/先说结论：/给一张可截图模板：)
# — 冒号后有正文才剥; 含逗号的引导 (第一步，先判：) 是正文结构, 不动
_CARD_LEAD_RE = re.compile(r"^[^：:，。！？\s]{1,10}[：:](?=\S)")


def wash_card_lines(lines: list[str]) -> list[str]:
    """卡上文字洗涤 (出口闸): 剥停顿标记/拼音/markdown 与 "报幕：标签" 引子;
    整行以冒号收尾 = 报幕句 (内容在下一行) → 整行丢弃; 句尾句号剥 (大字排版惯例).
    """
    out: list[str] = []
    for l in lines:
        t = _CARD_PAUSE_RE.sub("", l or "")
        t = _CARD_PINYIN_RE.sub(lambda m: m.group(1), t)
        t = _CARD_LEAD_RE.sub("", t)
        t = _CARD_MD_RE.sub("", t).strip()
        if t.endswith(("：", ":")):
            continue
        t = t.rstrip("。")
        if t:
            out.append(t)
    return out


# ── 1025 LLM 主判 (用户令: 正则判卡极难, LLM 自动总结简单) ──────────────
# 分工: LLM 只选区间 (start/end), 卡上文字一律 texts 原文切片 — 幻觉零通道;
# 确定性闸在代码侧: 区间夹取/剥空句/去重叠/跨度与字数下限/上限 4 全部代码说了算。
_LLM_MAX_SPAN = 6  # 单卡最多句数 (清单 3-4 句 + 余量)
_LLM_SYSTEM = (
    "你是爆款知识短视频的剪辑指导。下面是口播逐句编号清单，从中挑出观众会"
    "截屏收藏、值得做成停留卡片的时刻。参考四类：清单/模板三连（如 第一条，…"
    "第二条，…）、对仗口诀（如 三问定性质，三线定底线，三案防翻车）、断言金句"
    "（不是…，是…）、平行短句三连。"
    '严格输出 JSON {"cards":[{"start":<int>,"end":<int>,'
    '"kind":"list|mantra|aphorism|parallel","why":"≤12字"}]}。'
    "start/end 用句编号 [n]（end 那句不含在卡内，左闭右开），区间内句子须连成"
    "同一张卡；报幕/引子句（如「给一张可截图模板：」「浓缩成一句口诀：」）"
    "不要收进卡，从内容句起选；-1s- 之类口播停顿标记不是文字；"
    "最多 4 张，宁缺毋滥；没有值得的输出 {\"cards\":[]}；"
    "禁止改写原句，禁止输出 JSON 以外任何文字。"
)


def _clamp_llm_cards(raw_cards: list, texts: list[str]) -> list[dict]:
    """LLM 原始区间 → 合法卡 (确定性闸: 全在代码侧, LLM 说了不算)."""
    n = len(texts)
    out: list[dict] = []
    taken: list[tuple[int, int]] = []
    for c in raw_cards if isinstance(raw_cards, list) else []:
        try:
            a, b = int(c["start"]) - 1, int(c["end"]) - 1  # 编号 1 起 → 索引 0 起
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(a, 0), min(b, n)
        while a < b and not texts[a].strip():  # 剥两端空句 (TTS 停顿行不上卡)
            a += 1
        while b > a and not texts[b - 1].strip():
            b -= 1
        if not (0 <= a < b <= n) or b - a > _LLM_MAX_SPAN:
            continue
        if any(a < e and s < b for s, e in taken):  # 与已收卡重叠 → 丢
            continue
        if sum(_eff_len(texts[k]) for k in range(a, b)) < 6:
            continue
        taken.append((a, b))
        out.append({"start": a, "end": b,
                    "kind": str(c.get("kind") or "llm")[:12],
                    "lines": [texts[k].strip() for k in range(a, b)],
                    "why": str(c.get("why") or "")[:20]})
        if len(out) >= QUOTE_CARD_MAX:
            break
    out.sort(key=lambda c: c["start"])
    return out


def _detect_quote_cards_llm(texts: list[str]) -> list[dict]:
    from app.services.llm_service import _parse_json, get_llm_service
    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts, start=1))
    raw = get_llm_service().chat(
        _LLM_SYSTEM, numbered, model="flash", temperature=0.2,
        timeout=25, max_tokens=600,
        response_format={"type": "json_object"}, tag="quote_card")
    data = _parse_json(raw) or {}
    _raw = data.get("cards") if isinstance(data, dict) else None
    if _raw:
        logger.info("[quote_card] LLM 判卡理由: %s",
                    [str(c.get("why") or "")[:16] for c in _raw])
    return _clamp_llm_cards(_raw or [], texts)


def detect_quote_cards_smart(texts: list[str]) -> list[dict]:
    """LLM 主判 + 正则兜底 (fail-open).

    LLM 空数组/超时/异常 → 回落 detect_quote_cards 正则四型
    (装配日志可辨: LLM 检出/正则检出 前缀不同)。
    """
    if not any((t or "").strip() for t in texts):
        return []
    try:
        cards = _detect_quote_cards_llm(texts)
    except Exception as exc:  # noqa: BLE001 — fail-open 回正则
        logger.warning("[quote_card] LLM 判卡失败, 回正则: %s", exc)
        cards = []
    if cards:
        logger.info("[quote_card] LLM 检出 %d 张: %s", len(cards),
                    [f"#{c['start']}-{c['end']}({c['kind']}) {c['lines'][0][:12]}…"
                     for c in cards])
        return cards
    return detect_quote_cards(texts)
