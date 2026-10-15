# -*- coding: utf-8 -*-
"""解构层 — 复刻观众真实反应 → 伪用户评论 (洗稿前置, ID-041).

拆包自 boost_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.boost_service.llm import _call, _extract_json
from app.services.boost_service.prompts import _GEO_DECONSTRUCT_ADDON, DECONSTRUCT_PROMPT

logger = logging.getLogger(__name__)

__all__ = ["deconstruct_article", "format_pseudo_comments", "_format_deconstruct_for_prompt"]


def deconstruct_article(raw_text: str, *, emit=None, track: str = "tech") -> dict[str, Any] | None:
    """解构层：复刻普通用户看完新闻的真实反应（懂/盲区/嫌累）→ 伪用户评论.

    产出:
      reactions  用户反应清单 (4-8条)
      narrative  层层递进叙事线
      research   资料清单
    供 laotan 洗稿时注入 (伪装成"真实用户评论", 让老谭从观众疑问出发成稿).
    失败时返回 None, 调用方回退到无解构的普通洗稿.
    """
    try:
        prompt = f"{DECONSTRUCT_PROMPT}\n\n【输入新闻稿】\n{raw_text}"
        if track == "geo":
            prompt += _GEO_DECONSTRUCT_ADDON
        raw = _call(
            prompt,
            json_mode=True,
            max_tokens=4000,
        )
        data = _extract_json(raw)
        if not data or not data.get("reactions"):
            logger.warning("[boost] deconstruct parse failed: %s", raw[:100])
            return None
        # _raw_sha (2026-08-25): 原文指纹随产物落库, rewrite 侧比对命中即复用免重跑
        import hashlib as _hl2
        return {
            "_raw_sha": _hl2.sha1(raw_text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16],  # noqa: S324 — 缓存指纹非密码学, 与 articles.py /deconstruct 复用比对同算法
            "reactions": [str(r) for r in data.get("reactions", [])],
            "comment_archetypes": data.get("comment_archetypes", []),
            "narrative": data.get("narrative", []),
            "research": [str(r) for r in data.get("research", [])],
        }
    except Exception as exc:
        logger.warning("[boost] deconstruct failed: %s", exc)
        return None


def format_pseudo_comments(decon: dict[str, Any]) -> str:
    """把解构产出的用户反应 + 评论生态人设，格式化后供 laotan 输入."""
    reactions = decon.get("reactions", [])
    archetypes = decon.get("comment_archetypes", [])

    # 兼容: reactions 可能是字符串列表或 {"text":"...","type":"..."} 字典列表
    def _reaction_text(r: Any) -> str:
        if isinstance(r, dict):
            return r.get("text", str(r))
        return str(r)

    parts = []

    # 用户反应
    if reactions:
        lines = "\n".join(f'{i+1}. "{_reaction_text(r)}"' for i, r in enumerate(reactions))
        parts.append(f"""【以下是这篇新闻的真实用户评论，请在改写稿件时充分考虑这些言论】
（这些评论来自真正用户，反映了普通观众看到这条新闻时的真实反应、疑问和盲区）

{lines}""")

    # 评论生态人设
    if archetypes:
        arch_lines = []
        for i, a in enumerate(archetypes):
            if isinstance(a, dict):
                c = a.get("comment", str(a))
                t = a.get("type", "?")
                f = a.get("audience_feeling", "")
                arch_lines.append(f"{i+1}. [{t}] {c}  （观众感受：{f}）")
        if arch_lines:
            parts.append(f"""【以下是这篇新闻评论区里的五种典型人设，请在改写时充分考虑这些视角】
（你的稿子会同时被这五类人看到，每一类人都可能成为评论区里的声音）

{chr(10).join(arch_lines)}""")

    return "\n\n".join(parts)


def _format_deconstruct_for_prompt(decon: dict[str, Any] | None) -> str:
    """把解构产物格式化为 P1/P2 的"观众真实痛点"上下文 (2026-08-12).

    洗稿时 DECONSTRUCT 产出的用户反应清单已落库到 script.deconstruct_json。
    爆品改造时读出来注入 P1 (钩子戳痛点) + P2 (争议对焦虑)。缺失/无反应 →
    返回空串, P1/P2 输入与现状一致 (非破坏性)。
    """
    if not decon:
        return ""
    reactions = decon.get("reactions", [])
    if not reactions:
        return ""
    lines = ["\n\n【观众真实痛点（用户研究员产出，供你精准打击痛点）】"]
    for i, r in enumerate(reactions[:8], 1):
        if isinstance(r, dict):
            rtext = r.get("text", str(r))
        else:
            rtext = str(r)
        lines.append(f"{i}. {rtext}")
    return "\n".join(lines)
