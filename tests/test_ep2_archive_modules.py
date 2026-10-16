# -*- coding: utf-8 -*-
"""0917 用户 ep2 存档稿 (四段无时间标记) — 解析/锚/清洗回归."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.book_service.module_map import (
    MODULE_LABEL_RE,
    parse_modules,
    walls_for_cleaned,
)
from app.services.script_parser import clean_episode_script

ARCHIVE = """【钩子】
你的增长曲线突然变平，不是团队懈怠。
是你能圈的市场空间，已经圈完了。
投手没换，素材没停，预算照批。

上集我们问：你铺着几个渠道？
这张表的骨架就三档：还在涨、归零、全趴下。
我是老谭，读一本书，多一个硬本事。

【核心概念拆解（一）｜撞墙测试】
这张表，我叫它撞墙测试。
正式说法有点绕，叫空间饱和度判定法。

《HBO 的内容战略》里记着，它到一九八四年，栽了个跟头。
不是你不行，是分母满了。

【核心概念拆解（二）｜动作】
最后给动作：下一笔钱投哪儿？
A档，继续覆盖，但加条预警线。
你不批预算也一样。

【总结 + 下期预告】
这张表干一件事：看两条增长线，哪条先归零。
我是老谭，读一本书，多一个硬本事。咱们下一集，见。"""


def test_label_forms():
    assert MODULE_LABEL_RE.fullmatch("【钩子】")
    assert MODULE_LABEL_RE.fullmatch("【0-28 秒｜钩子】")
    assert MODULE_LABEL_RE.fullmatch("【核心概念拆解（一）｜撞墙测试】")
    assert MODULE_LABEL_RE.fullmatch("【双评论钩子】")


def test_archive_parses_four_modules():
    mods = parse_modules(ARCHIVE)
    names = [m["name"] for m in mods]
    assert names == ["钩子", "核心概念拆解（一）", "核心概念拆解（二）", "总结+下期预告"]
    assert mods[1]["label"] == "撞墙测试" and mods[2]["label"] == "动作"
    assert mods[0]["tail"] == "我是老谭，读一本书，多一个硬本事。"
    # 0918: 锚=模块末句 (耐 TTS 短句合并) — 总结末句"咱们下一集，见。"
    # 在 TTS 行空间会被并入前句 (<10字), 行尾仍以它结尾 → endswith 匹配
    assert mods[3]["tail"] == "咱们下一集，见。"


def test_archive_walls_all_located():
    cleaned, _ = clean_episode_script(ARCHIVE)
    walls, missed = walls_for_cleaned(cleaned, parse_modules(ARCHIVE))
    assert not missed
    assert len(walls) == 4


def test_clean_strips_bare_labels():
    """无时间【钩子】行必须剥掉 (否则 TTS 念'钩子'两个字)."""
    cleaned, _ = clean_episode_script(ARCHIVE)
    assert "【" not in cleaned and "】" not in cleaned  # 标签行全剥
    assert "你的增长曲线" in cleaned and "撞墙测试" in cleaned  # 正文全留


def test_ep3_tail_note_label_excluded():
    """ep3【双评论钩子】= 非标准名 → 标签行剥离不念, 正文口播保留;
    不入六拍模块表 (切场时按 module_id 自成独立尾场, 语义正确)."""
    text = ("【0-28 秒｜钩子】\n钩子句。\n【233-300 秒｜总结 + 下期预告】\n总结句。\n"
            "【双评论钩子】\n评论钩子是口播句。")
    mods = parse_modules(text)
    assert [m["name"] for m in mods] == ["钩子", "总结+下期预告"]
    cleaned, _ = clean_episode_script(text)
    assert "双评论钩子" not in cleaned
    assert "钩子句。" in cleaned and "总结句。" in cleaned
    assert "评论钩子是口播句。" in cleaned  # 标签后正文=口播, 保留
