# -*- coding: utf-8 -*-
"""TTS 模块墙 (0917 模块总线) — 断包/归属/气口纯逻辑测试 (不碰 GPU)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from tts_lib.lines import _stamp_module_ids
from tts_lib.text import _group_lines_semantic


def _mk_lines(n: int) -> list[str]:
    return [f"第{i}句话说完。" for i in range(1, n + 1)]


def test_module_walls_force_pack_break():
    """模块墙行必须开新包: 墙前的包末行 = 墙行号-1."""
    lines = _mk_lines(12)
    walls = {3, 8}  # 两道模块墙
    groups = _group_lines_semantic(lines, 140, walls=frozenset({0} | walls))
    # 每组末行: 墙前一行或墙行; 断言墙行必为某组末行(即下一组从墙行+1开始? 不——
    # walls 语义=该行开新包, 故墙行是某组的首行; 验证: 无组跨墙 (组内 min..max
    # 不含墙在中间位置)
    for g in groups:
        for w in walls:
            assert not (g[0] < w <= g[-1]), f"组 {g} 跨模块墙 {w}"
    # 墙行必为某组首行
    starts = {g[0] for g in groups}
    assert walls <= starts


def test_module_id_stamping():
    """module_id = 包末行落在第几个模块 (墙=模块末行)."""
    walls = {3, 8}
    segs = [
        {"line_indices": [0, 1, 2, 3]},   # 末行=3=墙 → 模块0
        {"line_indices": [4, 5, 6, 7, 8]},  # 末行=8=墙 → 模块1
        {"line_indices": [9, 10, 11]},    # 末行=11 → 模块2
    ]
    _stamp_module_ids(segs, walls)
    assert [s["module_id"] for s in segs] == [0, 1, 2]


def test_pack_never_crosses_module_boundary_with_chars():
    """字数贪心撞模块墙: 墙优先于字数 — 包永不跨墙, 哪怕字数远未满."""
    lines = [f"句{i}。" for i in range(1, 11)]  # 每行3字
    groups = _group_lines_semantic(lines, 140, walls=frozenset({0, 5}))
    # 140 字上限下无墙会全并一包; 有墙=5行处必断
    assert len(groups) == 2
    assert groups[0][-1] == 4 and groups[1][0] == 5
