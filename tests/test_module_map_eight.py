# -*- coding: utf-8 -*-
"""1019 模块总线 8 段结构测试 — 书里怎么说的准入 + 总结/下期预告锚拆分.

内嵌文稿样本 (不依赖 db — 旧 test_module_map 的 db fixture 空稿是另一病,
历史债不在本批范围)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.book_service import module_map


def _script(labels_and_bodies: list[tuple[str, str]]) -> str:
    out = []
    for i, (lab, body) in enumerate(labels_and_bodies):
        a, b = i * 30, (i + 1) * 30
        out.append(f"【{a}-{b}秒｜{lab}】")
        out.append(body)
    return "\n".join(out) + "\n"


def _names(script: str) -> list[str]:
    return [m["name"] for m in module_map.parse_modules(script)]


TOOL_EP = _script([
    ("钩子", "一百年前德国人把一切算死。\n结果全线崩盘。"),
    ("回顾+引入", "上集我们问：还靠直觉吗？\n答案是：不靠直觉，靠设计边界。"),
    ("核心概念拆解（一）", "第一步不是找答案。\n是先分清问题的性质。"),
    ("核心概念拆解（二）", "第二步定边界条件。\n超过百分之二十五利润的项目，不做。"),
    ("书里怎么说的", "书里写得很直接。\n你必须有个备选。"),
    ("核心概念拆解（三）", "第三步逼出替代方案。\n故意找人唱反调。"),
    ("总结+下期预告", "口诀八个字：先定边界，再找答案。\n今天睡前就做：写下三条底线。\n"
                  "下一集，五件工具都齐了，怎么变成本能？\n我是老谭，拆一本书，翻一座山。"),
])

TAIL_EP = _script([
    ("钩子", "七件工具最后一件。\n就从一页纸开始。"),
    ("回顾+引入", "上集我们问：怎么把五个工具合一页？\n答案是：一页带走。"),
    ("核心概念拆解（一）", "圈出最重要的那件。\n圈不出来就只圈一件。"),
    ("核心概念拆解（二）", "口诀八个字：五项合一，一页带走。"),
    ("核心概念拆解（三）", "系列到这儿收尾了。\n让这一页纸在桌上待满一个月。"),
    ("总结+下期预告", "这套系统现在是你的一套了。\n我是老谭，咱们，山顶见。"),  # 无预告句
])

HEAD_EP = _script([
    ("钩子", "客户为什么总绕开技术最强的你？"),
    ("回顾+引入", "这本书七集，讲一件被忽略的事。"),
    ("核心概念拆解（一）", "信任不是感觉。\n它是一道算术题。"),
    ("核心概念拆解（二）", "分子有三项。\n分母只有一项。"),
    ("核心概念拆解（三）", "先接情绪，再谈方案。"),
    ("总结+下期预告", "先做个诊断：你上来先讲技术，还是先问他在意什么？\n"
                  "下集讲：闭嘴之后，说什么？"),
])


def test_tool_ep_eight_modules():
    names = _names(TOOL_EP)
    assert names == ["钩子", "回顾+引入", "核心概念拆解（一）", "核心概念拆解（二）",
                     "书里怎么说的", "核心概念拆解（三）", "总结", "下期预告"]


def test_tail_ep_no_teaser_single_summary():
    names = _names(TAIL_EP)
    assert names == ["钩子", "回顾+引入", "核心概念拆解（一）", "核心概念拆解（二）",
                     "核心概念拆解（三）", "总结"]  # 无预告 → 末段单「总结」


def test_head_ep_seven_modules():
    names = _names(HEAD_EP)
    assert names == ["钩子", "回顾+引入", "核心概念拆解（一）", "核心概念拆解（二）",
                     "核心概念拆解（三）", "总结", "下期预告"]


def test_teaser_variant_alias_canonized():
    """ep5 实锤变体「总结+下集预告」(下集≠下期) 同样拆分."""
    s = TOOL_EP.replace("总结+下期预告", "总结+下集预告").replace("下一集，", "下集讲：")
    names = _names(s)
    assert names[-2:] == ["总结", "下期预告"]


def test_bare_teaser_label_direct():
    """裸【下期预告】标签 = 终态直入, 不经拆分不重拆."""
    head = TOOL_EP[:TOOL_EP.index("【180-210秒｜总结+下期预告】")]
    s = head + ("【180-195秒｜总结】\n口诀八个字。\n"
                "【195-210秒｜下期预告】\n下一集讲怎么变成本能。\n我是老谭，咱们下一集，见。")
    names = _names(s)
    assert names[-2:] == ["总结", "下期预告"]
    mods = module_map.parse_modules(s)
    assert [m["tail"] for m in mods[-2:]] == ["口诀八个字。", "我是老谭，咱们下一集，见。"]


def test_split_tails_locate_walls():
    """拆分模块的 tail 锚在行序列上可定位 (TTS 模块墙前置验证)."""
    lines = [ln.strip() for ln in TOOL_EP.splitlines() if ln.strip()]
    speech = [l for l in lines if not module_map.MODULE_LABEL_RE.fullmatch(l)]
    mods = module_map.parse_modules(TOOL_EP)
    walls, missed = module_map.find_walls(speech, mods)
    assert not missed, missed
    assert len(walls) == len(mods)  # 8 模块 8 墙


def test_idx_sequential_after_split():
    mods = module_map.parse_modules(TOOL_EP)
    assert [m["idx"] for m in mods] == list(range(8))
