# -*- coding: utf-8 -*-
"""1019-B parse_script 去重豁免品牌仪式句 — 卓有ep2 实锤回归.

落款身份句 段中自报家门+结尾落款 是设计内双现; 旧去重删第二次出现 →
收尾不发声 + A8 尾段瘦身。普通句重复仍去重 (LLM 复读防线不撤)。
(计数用子串: 短句合并会把句并进邻段, 精确等值计数不稳)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.script_parser import parse_script

IDENTITY = "我是老谭，读一本书，多一个硬本事。"


def _blob(script: str) -> str:
    return "\n".join(s["text"] for s in parse_script(script, None, None))


def test_identity_line_kept_twice():
    """身份句段中+结尾双现, 两处都保留 (ep2 实锤场景)."""
    script = ("上集我们问了那个关于忙碌却说不清成果的问题。\n" + IDENTITY +
              "\n下一集问个更扎心的问题：组织真的需要你吗。\n" + IDENTITY)
    assert _blob(script).count(IDENTITY) == 2


def test_ep2_tail_regression():
    """ep2 真实尾段形态回归: 段中身份句 + 结尾[预告问句+身份句+收尾半句] —
    结尾身份句保留, 收尾半句 "咱们下一集，见。" 并入预告句行尾 (manifest seg78 形态)。"""
    script = ("上集我们问了那个关于忙碌却说不清成果的问题。\n" + IDENTITY +
              "\n下一集问个更扎心的问题：你做的事组织真的需要吗。\n" + IDENTITY +
              "咱们下一集，见。")
    blob = _blob(script)
    assert blob.count(IDENTITY) == 2
    assert "咱们下一集，见。" in blob


def test_ordinary_duplicates_still_removed():
    """普通句重复仍去重 (豁免只给品牌仪式句)."""
    dup = "这套问话，转给那个天天加班却交不出成果的同事。"
    script = "开头是一个足够长的开场句子，不会被短句合并规则吞掉。\n" + dup + "\n" + dup + "\n结尾也是一个足够长的收束句，不会被合并。"
    assert _blob(script).count(dup) == 1
