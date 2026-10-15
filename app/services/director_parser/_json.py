"""JSON 提取 — 从 LLM 输出中抽取第一个 JSON 对象/数组.

行为逐字迁移自原 director_parser.py (2026-08-08 包化重构).
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["extract_json"]


def _strip_code_fence(text: str) -> str:
    """移除首尾 ``` 代码围栏(若有)."""
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _clean_control_chars(text: str) -> str:
    """剔除非法控制字符 (LLM 偶尔输出含 \x00-\x1f 的非法字符导致 JSONDecodeError).

    保留 JSON 合法的转义 (\n \t \r \\ ")，剔除其他 \x00-\x1f 控制字符。
    避免破坏字符串内已转义的内容: 只在字符串值外清理会破坏结构,
    保守做法是替换为空格 (保留 JSON 结构完整性)。
    """
    import re

    # 剔除未转义的控制字符 (除 \n \t \r 及 JSON 合法转义后)
    # 简单可靠: 移除所有 \x00-\x08 \x0b \x0c \x0e-\x1f (保留 \n \t \r)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)


def extract_json(text: str) -> Any:
    """Extract the first JSON object or array from LLM output."""
    text = _clean_control_chars(_strip_code_fence(text.strip()))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # LLM 可能把 JSON 混在解释文字里，尝试提取第一个对象或数组
    for start_ch, pattern in (("{", r"\{.*\}"), ("[", r"\[.*\]")):
        idx = text.find(start_ch)
        if idx == -1:
            continue
        match = re.search(pattern, text[idx:], re.DOTALL)
        if match:
            return json.loads(match.group(0))
    return json.loads(text)
