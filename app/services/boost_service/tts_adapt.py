# -*- coding: utf-8 -*-
"""TTS 读法适配 — 手改稿的数字/符号转中文读法 (1:1 逐字保留守卫).

拆包自 boost_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

from app.services.boost_service.llm import _call
from app.services.boost_service.prompts import TTS_ADAPT_PROMPT

__all__ = ["adapt_tts_readability"]


def adapt_tts_readability(text: str) -> tuple[str, int]:
    """口播适配: 全文阿拉伯数字/百分号/连字符 → 中文读法, 其余 1:1.

    返回 (适配稿, 变更行数). 手改稿专用——洗稿模板的数字禁令只覆盖首产,
    之后人工编辑引入的数字靠本函数补转 (确定性优先, 不动 LLM 润色)。

    守卫: LLM 篡改检测——转换只让文本变长 (50%→百分之五十) 或小幅变短
    (删星号/列表符), 变更后长度超原文 60% 或缩水逾 18% 即判失败抛异常;
    结果只回编辑区不落库, 人工目检后保存。
    """
    import re

    original = (text or "").strip()
    if not original:
        return "", 0
    # 快路径: 无数字/百分号/星号/连字符 → 原样返回, 省一次 LLM 调用
    if not re.search(r"[0-9%*]|-", original):
        return original, 0

    prompt = TTS_ADAPT_PROMPT + "\n\n【待适配口播稿】\n" + original
    raw = _call(prompt, max_tokens=8000, temperature=0.2)
    adapted = raw.strip()
    # 剥可能的 markdown 代码围栏 (LLM 偶发包 ```)
    if adapted.startswith("```"):
        adapted = re.sub(r"^```[a-z]*\n?|```$", "", adapted).strip()

    drift = len(adapted) / max(len(original), 1)
    if not adapted or drift > 1.6 or drift < 0.82:
        raise RuntimeError(
            f"口播适配守卫拦截: 输出长度漂移 {drift:.2f}x (读法转换应在 0.82~1.6x), 疑似 LLM 篡改原文"
        )

    # 变更行数 = 前后按行对齐统计差异行 (供前端提示)
    old_lines = original.splitlines()
    new_lines = adapted.splitlines()
    changed = sum(
        1 for a, b in zip(old_lines, new_lines) if a.strip() and a.strip() != b.strip()
    )
    return adapted, changed
