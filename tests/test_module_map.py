# -*- coding: utf-8 -*-
"""六拍模块总线 (0917) — 解析器与真稿回归.

覆盖: 空格/全半角竖线变体、ep3 真稿(带空格标签)、ep2 真稿(裸稿=空表)、
人工修稿丢标后的 tail 对齐恢复、清洗器剥标签(旧口径剥不掉的地雷回归)。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.book_service import module_map
from app.services.script_parser import clean_episode_script

BOOK = "97ac3fd5-3eaf-4070-a2c5-3075250831d9"
DB = Path(__file__).resolve().parents[1] / "data" / "pipeline.db"


def _ep_script(ep: int) -> str:
    con = sqlite3.connect(DB)
    row = con.execute(
        "select script_text from book_episodes where book_id=? and ep_index=?",
        (BOOK, ep)).fetchone()
    con.close()
    return row[0] if row else ""


def test_label_variants():
    """空格/半角竖线/紧凑三种变体都要认 (ep3 实锤带空格, 旧口径剥不掉)."""
    for line in ("【0-28 秒｜钩子】", "【0-28秒｜钩子】", "【0 - 28 秒 | 钩子】"):
        assert module_map.MODULE_LABEL_RE.fullmatch(line), line
        assert module_map.parse_modules(line + "\n第一句。")[0]["name"] == "钩子"


def test_parse_modules_ep3_real():
    """ep3 真稿 (带空格标签): 六拍模块齐全且段名规范.

    【双评论钩子】无时间数字 → 不是语音段标签, 不入模块表 (正确行为)."""
    mods = module_map.parse_modules(_ep_script(3))
    names = [m["name"] for m in mods]
    assert len(mods) == 6, names  # 六拍; 双评论钩子(尾注构件)不属语音段
    assert names[0] == "钩子" and names[1] == "回顾+引入"
    assert names[2].startswith("核心概念拆解")
    assert all(m["tail"] for m in mods), "每模块应有末行锚"


def test_parse_modules_bare_ep1():
    """ep1 裸稿 (人工修稿事故, 尚未补标记): 解析为空表 — 走恢复/告警路径.

    (ep2 原也裸稿, 0917 用户存档落库后已是 4 段带标记稿, 见
    test_ep2_archive_modules.py; 裸稿回归改用 ep1。)"""
    assert module_map.parse_modules(_ep_script(1)) == []


def test_walls_for_cleaned_ep3():
    """ep3 清洗后: 六墙全部定位, 且墙行号严格递增."""
    raw = _ep_script(3)
    cleaned, issues = clean_episode_script(raw)
    assert not any("标签" in i for i in issues), f"标签应被剥净: {issues}"
    assert not module_map.MODULE_LABEL_RE.search(cleaned), "清洗后不应残留标签"
    mods = module_map.parse_modules(raw)
    walls, missed = module_map.walls_for_cleaned(cleaned, mods)
    assert not missed, missed
    assert len(walls) == len(mods)
    n_lines = len([l for l in cleaned.splitlines() if l.strip()])
    assert all(0 <= w < n_lines for w in walls)


def test_walls_survive_light_edit():
    """丢标签但正文未动: 旧模块表 tail 在清洗稿上仍全量对齐 (修稿恢复路径)."""
    raw = _ep_script(3)
    cleaned, _ = clean_episode_script(raw)
    mods = module_map.parse_modules(raw)
    # 模拟人工编辑: 把正文一行改一个字 → 该模块墙丢失, 其余保留
    lines = cleaned.splitlines()
    victim_idx = None
    for i, l in enumerate(lines):
        if l.strip() == mods[0]["tail"]:
            victim_idx = i
            break
    assert victim_idx is not None
    lines[victim_idx] = lines[victim_idx] + "了"
    walls, missed = module_map.walls_for_cleaned("\n".join(lines), mods)
    assert mods[0]["name"] in missed and len(walls) == len(mods) - 1


def test_clean_strips_spaced_labels():
    """旧地雷回归: 带空格标签【0-28 秒｜钩子】必须被清洗剥掉 (此前会被念出)."""
    text = "【0-28 秒｜钩子】\n第一句。\n【28-56 秒｜回顾 + 引入】\n第二句。"
    cleaned, issues = clean_episode_script(text)
    assert "第一句。" in cleaned and "第二句。" in cleaned
    assert "钩子" not in cleaned and "0-28" not in cleaned
