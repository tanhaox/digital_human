# -*- coding: utf-8 -*-
"""R9 自动编排 — 分类→同帧语义音效 + 内联划重点(金/红) + 密度规则.

知识来源: 45期协同三件套 / 音效语义库(用户标注) / 密度规则(用户口径) — 全确定性, 无 LLM
视觉强调 v2: 砍掉独立强调轨, 改为字幕行内双色划重点 (解决与字幕/HF卡重合+截断)
拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import logging
from typing import Any

from pyJianYingDraft import TextIntro

from app.services.jy_draft_service.common import _ANIM_PAIRS, _US
from app.services.jy_draft_service.sfx import attach_sound, sound_path
from app.services.jy_draft_service.subtitle_text import find_highlight_ranges

logger = logging.getLogger(__name__)

__all__ = ["_auto_choreograph"]

# 类别 → TextIntro 动画 (首条可见字幕用卡拉OK 逐字点亮); 配对音库缺时回退 _SFX_FAMILY
_CAT_ANIM: dict[str, tuple[str, int]] = {
    "suspense": ("向上滑动", 400),
    "punchline": ("放大", 400),
    "money": ("星光闪闪", 400),
}

# 金额语境词 (数字+语境 → money 族; 纯数字/专名 → punchline 叮族; 问句 → 悬疑族)
_MONEY_CTX = ("万", "亿", "元", "美元", "收入", "赚", "营收", "薪", "融资", "估值",
              "利润", "市值", "ARR", "GMV", "价格", "涨价", "降价", "关税", "成本")
_SFX_FAMILY = {
    "money": ("金币到账叮咚声", "叮", "综艺叮~~"),
    "punchline": ("叮", "综艺叮~~"),
    "suspense": ("诡异的滴水声", "紧张转场音效"),
}


def _suspense_core(chunk: str) -> str | None:
    """问句强调短语 (2026-08-26 v2): 疑问代词锚定 + 虚指兜底, 代替旧"剥疑问词取前14字"。

    旧规则把"是什么样的/怎么办呢"这类纯虚指尾巴也整块放大 (用户实测选字不精细)。
    - 实体代词 (谁/多少/几/啥): 取代词前后各 ~3 字窗口 — "算在谁头上"/"记在谁头上"
    - 方式代词 (什么/怎么/为什么…): 取最后一个标点后、代词前的实义词段 (≤6 字),
      剥边缘虚词 — "我们被灌输的美军是什么样的"→"灌输的美军"; "伊朗为什么这么干"→"伊朗"
    - 兜底: 句中数字/专名 (find_highlight_ranges); 再无 → None 不放大 (宁缺毋滥)。
    """
    import re

    text = chunk.strip().rstrip("？?。！")
    last = None  # (start, end, kind) — 取结束最靠后者; 同结束取更长词 ("为什么"胜过其内嵌"什么")
    for q in ("为什么", "凭什么", "什么样", "怎么", "多少", "哪些", "哪个", "什么", "谁", "啥", "哪", "几"):
        for m in re.finditer(q, text):
            cand = (m.start(), m.end(), "entity" if q in ("谁", "多少", "几", "啥") else "manner")
            if last is None or (cand[1], -cand[0]) > (last[1], -last[0]):
                last = cand
    if last is None:
        rngs = find_highlight_ranges(text)
        if rngs:
            s, e = rngs[0]
            return text[s:e][:8]
        return None

    s, e, kind = last
    if kind == "entity":
        win = text[max(0, s - 4):min(len(text), e + 3)]  # 前4字: 覆盖"到底/究竟"整词剥除
        win = re.sub(r"[，。！？；、,]", "", win)
        win = re.sub(r"(?:到底|究竟|最后|还是|就是|了|的|地|得)+$", "", win)
        win = re.sub(r"^(?:到底|究竟|最后|还是|真的)+", "", win)
        if len(win) >= 2:
            return win[:8]
    # 方式代词: 代词前最后一个分句; 不足 2 字则回溯倒数第二个分句
    # ("咱们普通人，又凭什么该关心…" → 尾分句"又"剥空 → 回溯"普通人")
    head = text[:s]
    clauses = [c.strip() for c in re.split(r"[，。！？；、,]", head) if c.strip()]
    for seg in reversed(clauses[-2:] if len(clauses) >= 2 else clauses):
        seg = re.sub(r"(?:到底|究竟|最后|真的|还是|就是|要是|能|会|是|了|的|地|得|又|也|才)+$", "", seg)
        seg = re.sub(r"^(?:到底|究竟|最后|可是|但是|所以|而且|咱们|我们|他们|又|也|才)+", "", seg)
        if len(seg) > 6:
            seg = seg[-6:]
        if len(seg) >= 2:
            return seg
    # 兜底: 代词本身+后 1-3 实字 ("多少钱"/"谁头上"型)
    mm = re.match(r"((?:谁|什么|多少|几)[一-鿿]{1,3})", text[s:])
    if mm:
        return mm.group(1)
    rngs = find_highlight_ranges(text)
    if rngs:
        st, en = rngs[0]
        return text[st:en][:8]
    return None


def _classify_chunk(chunk: str) -> tuple[str, str | None]:
    """字幕块语义分类: (类别, 强调短语). 类别 ∈ money/punchline/suspense/plain.

    强调短语升级 (2026-08-17 用户反馈: 之前只抓裸关键词 AI/32/SK, 完全没有
    金句/反转/概念级长内容) — 按优先级提取:
      ① 引号内容 → 金句/概念 (整段引用)
      ② 反转/金句标记词所在分句 → 概念句 (≤16字)
      ③ 数字+语境单位 → 金额/数据短语 (如 "涨了34%""20亿美元", 非裸数字)
      ④ 问句 → 疑问核心短语
    """
    import re

    if re.search(r"[?？]$", chunk.strip()):
        core = _suspense_core(chunk)
        return "suspense", core

    # ① 引号金句/概念: 「...」 “...” "..." （TTS 读法稿里引号保留完整）
    m = re.search(r"[「“\"]([^「」”\"]{4,24})[」”\"]", chunk)
    if m:
        return "punchline", m.group(1)

    # ② 反转/金句标记词 → 所在分句 (金句的"肉"在标记词附近)
    for marker in ("其实", "根本", "真相", "意味着", "这就是", "关键在于",
                   "没想到", "说白了", "恰恰是", "最狠的", "最讽刺"):
        if marker in chunk:
            # 取含标记词的分句 (按标点切), 超长则取标记词前后共 14 字
            for clause in re.split(r"[，。！？；、]", chunk):
                if marker in clause and len(clause.strip()) >= 4:
                    c = clause.strip()
                    if len(c) > 16:
                        i = c.index(marker)
                        c = c[max(0, i - 4):i + 12]
                    return "punchline", c
    # ③ 数字短语: 数字 + 单位/语境 (非裸数字)
    m = re.search(
        r"([一-鿿]{0,5}?[\d.]+\s*[万亿]?(?:%|倍|美元|元|日元|欧元|年|个月|天|次|人|条|名)?)",
        chunk,
    )
    if m and re.search(r"\d", m.group(1)):
        phrase = m.group(1).strip()
        if len(phrase) >= 2 and any(w in chunk for w in _MONEY_CTX):
            return "money", phrase[:16]
        if len(phrase) >= 3:  # 有语境的数字才做强调, 裸短数字放过
            return "punchline", phrase[:16]

    # ④ 专名兜底 (latin ≥3 字符, 排除已剥标签后的短缩写滥用)
    ranges = find_highlight_ranges(chunk)
    for s, e in ranges:
        cand = chunk[s:e]
        if len(cand) >= 4 and re.fullmatch(r"[A-Za-z][A-Za-z0-9 .+-]*", cand):
            return "punchline", cand
    return "plain", None


def _auto_choreograph(script: Any, chunk: str, start_us: int,
                      stats: dict[str, int], last_sound_us: list[int],
                      first: bool = False) -> tuple[list, list, str | None, int | None]:
    """R9 v3: 每个字幕块 → 内联高亮 (金/红) + 动效动画 + 同帧音效.

    v3 (2026-08-18): 动效 = TextIntro 动画挂字幕段本身 (不加层, 零重合);
    动画与音效同一次决策同帧触发 — 音效优先取动画配对
    (jy_animation_sound_pairs), 库缺回退类别族 _SFX_FAMILY。
    首条可见字幕用卡拉OK (逐字点亮跟音频)。
    密度闸门管整个事件 (动画+音效): 前 30s 全类别, 之后仅非 plain 且间隔 ≥4s。
    Returns: (gold_ranges, red_ranges, anim_name, anim_ms)。
    """
    cat, kw = _classify_chunk(chunk)

    gold: list[tuple[int, int]] = []
    red: list[tuple[int, int]] = []
    if kw:
        idx = chunk.find(kw)
        if idx >= 0:
            rng = (idx, idx + len(kw))
            if cat == "suspense":
                red.append(rng)
            else:
                gold.append(rng)
            stats["emphasis"] += 1
        # 兜底: 分类词不在 chunk 里(改写后), 用 find_highlight_ranges 补数字/专名
        elif cat in ("money", "punchline"):
            gold.extend(find_highlight_ranges(chunk)[:1])

    # 动效决策: 首条可见字幕卡拉OK; 其余按类别; 动画名须为 TextIntro 合法枚举
    anim: str | None = None
    anim_ms: int | None = None
    if first:
        anim = "卡拉OK"
    elif cat in _CAT_ANIM:
        anim, anim_ms = _CAT_ANIM[cat]
    if anim is not None and not hasattr(TextIntro, anim):
        anim, anim_ms = None, None

    # 同帧音效 + 密度闸门 (闸门管整个事件: 动画+音效)
    if cat != "plain":
        if last_sound_us[0] is not None and start_us - last_sound_us[0] < 4_000_000 and start_us > 30_000_000:
            stats["sfx_density_skip"] += 1
            return gold, red, None, None
        # 音效: 动画配对优先, 回退类别族; 库内可用者轮换
        fam: list[str] = []
        if anim:
            fam += list(_ANIM_PAIRS.get(anim, {}).get("sounds", []))
        fam += list(_SFX_FAMILY[cat])
        avail = list(dict.fromkeys(s for s in fam if sound_path(s)))
        if avail:
            rot = stats.get(f"_rot_{cat}", 0)
            name = avail[rot % len(avail)]
            stats[f"_rot_{cat}"] = rot + 1
            if attach_sound(script, "sfx", name, start_us / _US, volume=0.9):
                stats["sfx"] += 1
                last_sound_us[0] = start_us
            else:
                stats["sfx_missing"] += 1
        else:
            stats["sfx_missing"] += 1
    return gold, red, anim, anim_ms
