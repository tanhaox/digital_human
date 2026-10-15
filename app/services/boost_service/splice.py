# -*- coding: utf-8 -*-
"""稿件拼接/清洗 — 内部标注剥离 / 正文锚点定位 / P1-P3 产物拼接.

拆包自 boost_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

from typing import Any

__all__ = ["clean_boosted_text", "_find_end"]

# laotan 模块标题标注 (应剥离, 不属于口播内容)
_MODULE_MARKERS = {
    "花边小新闻", "横向对照", "背景纵深", "极速钩子", "身份接管",
    "现象反差", "深度拆解", "价值观收割", "回收讨论", "预埋逻辑清单",
    "最终全稿", "呼吸点审计清单", "争议预埋点", "关注预埋点", "结尾回收点",
    "认知偏差分析", "改造后开头", "标题候选",
}


def clean_boosted_text(text: str) -> str:
    """剥离爆品改造稿里的内部标注，得到干净口播文本.

    需剥离:
      - 模块标题 【花边小新闻】【横向对照】 → 整段删
      - 结构清单 [预埋逻辑清单] 及跟随内容 → 删
      - 加粗内容句 **【xxx】** → 去 ** 和 【】, 保留文字
      - 裸内容句 【xxx】 → 去 【】, 保留文字
      - 空行整理
    """
    import re

    if not text:
        return text

    lines = text.splitlines()
    out: list[str] = []
    in_listing = False  # 是否在 [预埋逻辑清单] 结构内

    for line in lines:
        s = line.strip()
        if not s:
            continue

        # 结构清单头: [预埋逻辑清单] 开头的结构, 跳过直到空行/最终全稿
        m = re.match(r"^\[(预埋逻辑清单|最终全稿|呼吸点审计清单|认知偏差分析|标题候选|改造后开头)\]", s)
        if m:
            in_listing = True
            if m.group(1) == "最终全稿":
                in_listing = False  # 最终全稿后是正文
            continue
        if in_listing:
            # 清单内: 跳过 [xxx] 和 :: 结构行
            if re.match(r"^[\[\[]", s) or "→" in s or "：" in s[:20]:
                continue
            if s.endswith(("】", "]")) and len(s) < 100:
                continue
            in_listing = False  # 脱离清单

        # 正文锚点 (2026-08-12): 【888999000】是下游拼接的内部标记, 整行删除
        if "【888999000】" in s:
            continue

        # 模块标题整段删
        mm = re.match(r"^【([^】]{1,10})】", s)
        if mm and mm.group(1) in _MODULE_MARKERS:
            continue

        # markdown 标题整行删 (2026-08-14): ## 第X层 / # 精修清单 / # 最终全稿 等
        # 7层洗稿稿 LLM 偶发自加 ## 层标题; P4 也可能漏出 # 清单头. 兜底剥掉, 不进 boosted_text
        if re.match(r"^#{1,6}\s", s):
            continue

        # 加粗内容句 **【xxx】** → 去 ** 和 【】
        s = re.sub(r"\*\*", "", s)  # 去掉所有 ** (可能在句首/句中/句尾)
        # 去内容句的【】括号 (保留文字)
        s = re.sub(r"^【([^】]+)】", r"\1", s)
        s = re.sub(r"【([^】]+)】", r"\1", s)
        # 剥离"（此处口播为X）"标注 (2026-08-12): 多音字消歧误标专有名词如 昇腾→生疼。
        # 昇腾（此处口播为生疼）→ 昇腾；【昇腾（口播：生疼）】→ 昇腾
        s = re.sub(r"[（(](?:此处|口播)[：:][^）)]*[）)]", "", s)
        s = re.sub(r"[（(]此处口播为[^）)]*[）)]", "", s)
        # 清理孤立单 | (保留 || 停顿符)
        s = re.sub(r"(?<!\|)\|(?!\|)", "", s)
        s = s.strip()
        if s:
            out.append(s)

    return "\n".join(out)


def _parse_opening(text: str) -> dict[str, Any] | None:
    """P1: 解析 JSON, 校验 opening_30s / titles."""
    from app.services.boost_service.llm import _extract_json

    data = _extract_json(text)
    if not data:
        return None
    opening = data.get("opening_30s")
    titles = data.get("titles")
    if not isinstance(opening, list) or len(opening) < 4:
        return None
    return {
        "opening_30s": [str(s).strip() for s in opening[:4]],
        "titles": [str(t).strip() for t in (titles or [])[:3]],
    }


# laotan 稿的稳定引导词 (身份段结尾 → 正文开始). 用它们做拼接锚点.
_LAOTAN_ANCHORS = (
    "这事儿咱们得剥开看",
    "这事儿咱们得看透背后的算盘",
    "咱们得剥开看",
    "咱们得看透背后的算盘",
)


def _find_end(script_text: str) -> str:
    """返回 P2 需要的"去掉开头钩子"的正文.

    洗稿稿结构通常为: [钩子段] + [身份段(大家好/我是XX)] + [正文锚点] + [正文...].

    定位优先级 (2026-08-12):
    1. 正文锚点【888999000】—— 洗稿模板 (laotan 家族) 强制 LLM 在正文正式
       开始处输出, 最可靠。从标记后开始保留正文。
    2. 引导词锚点 (这事儿咱们得剥开看 等) —— 旧稿/未埋锚点稿兼容。
    3. 兜底: 去掉钩子段 + 身份段 (保守删 1-2 行, 不猜正文起点)。
    """
    # 1. 正文锚点 (2026-08-12): 洗稿模板埋入, 精准定位正文起点
    anchor_idx = script_text.find("【888999000】")
    if anchor_idx != -1:
        return script_text[anchor_idx + len("【888999000】"):]
    # 2. 引导词锚点
    for anchor in _LAOTAN_ANCHORS:
        idx = script_text.find(anchor)
        if idx != -1:
            # 从锚点所在行首开始 (保留引导词那整句)
            line_start = script_text.rfind("\n", 0, idx) + 1
            return script_text[line_start:]
    # 3. 兜底: 去掉钩子段 + 身份段 (保守, 不猜正文起点)
    lines = [ln for ln in script_text.splitlines() if ln.strip()]
    drop = 1
    if len(lines) > 2 and ("大家好" in lines[1] or "我是" in lines[1]):
        drop = 2
    return "\n".join(lines[drop:]) if drop < len(lines) else script_text


def _splice_boosted(p1: dict[str, Any] | None, p2_text: str | None, original: str) -> str:
    """拼接 P1 开头 + P2 预埋正文.

    P1 成功 → 用新开头替换原稿第一段; P1 失败 → 保留原稿开头.
    P2 成功 → 用预埋版正文; P2 失败 → 保留原稿正文.
    """
    if p1:
        new_opening = "\n".join(p1["opening_30s"])
    else:
        # P1 失败, 取原稿第一段
        lines = [ln for ln in original.splitlines() if ln.strip()]
        new_opening = lines[0] if lines else ""

    if p2_text:
        body = p2_text
    else:
        body = _find_end(original)

    return f"{new_opening}\n\n{body}"


def _strip_p3_head(text: str) -> str:
    """P3 输出含 [呼吸点审计清单] + [最终全稿] 两段, 只保留 [最终全稿] 之后正文.

    兼容模型不输出标记的情况 (直接返回全稿).
    """
    marker = "[最终全稿]"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker) :].strip()
    return text.strip()
