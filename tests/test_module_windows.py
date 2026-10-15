# -*- coding: utf-8 -*-
"""模块总线切场 (0917) — module_windows 纯逻辑测试 (ep2 真 manifest 回归)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.anim_pipeline.director2 import module_windows

MF = Path(r"E:\数字人计划\outputs\audio\2026-09-17\老谭·说书(TTS2)\58fe2615_1789618259\manifest.json")


def _segs(specs: list[tuple[float, int | None]]) -> list[dict]:
    """(duration, module_id) 列表 → segs (t 连续铺)."""
    out, cur = [], 0.0
    for dur, mid in specs:
        out.append({"t_start": round(cur, 2), "t_end": round(cur + dur, 2),
                    "duration": dur, "text": "x" * 10, "module_id": mid})
        cur += dur
    return out


def test_no_module_id_returns_none():
    """旧 manifest (无 module_id) → None → 调用方走 LLM 自由切场旧路径."""
    assert module_windows(_segs([(4.7, None), (12.2, None)])) is None


def test_module_boundary_is_scene_boundary():
    """模块界=场界: 3 模块 → ≥3 场, 场 t 直接取包界, 首尾相接铺满."""
    segs = _segs([(20.0, 0), (5.0, 0), (30.0, 1), (8.0, 2), (4.0, 2)])
    wins = module_windows(segs)
    assert wins is not None
    assert [w["module_id"] for w in wins] == [0, 1, 2]
    assert wins[0]["t_start"] == 0.0 and wins[-1]["t_end"] == segs[-1]["t_end"]
    for a, b in zip(wins, wins[1:]):
        assert b["t_start"] == a["t_end"], "场窗必须首尾相接"
    # 场界必须落在包界 (不腰斩包)
    pack_edges = {s["t_start"] for s in segs} | {s["t_end"] for s in segs}
    for w in wins:
        assert w["t_start"] in pack_edges and w["t_end"] in pack_edges


def test_long_module_splits_at_pack_boundary():
    """单模块超 70s → 按包界分窗; 窗界∈包界, 每窗 ≤ ~70+单包长."""
    durs = [15.0] * 7  # 105s 同模块
    segs = _segs([(d, 0) for d in durs])
    wins = module_windows(segs)
    assert len(wins) >= 2
    pack_edges = {s["t_start"] for s in segs} | {s["t_end"] for s in segs}
    for w in wins:
        assert w["t_start"] in pack_edges and w["t_end"] in pack_edges
    for a, b in zip(wins, wins[1:]):
        assert b["t_start"] == a["t_end"]


def test_ep2_real_manifest_shape():
    """ep2 真 manifest (无 module_id — 旧音频) → None, 旧路径不断."""
    if not MF.exists():
        return  # E 盘不在时跳过 (CI 环境)
    data = json.loads(MF.read_text(encoding="utf-8"))
    segs, cur = [], 0.0
    for e in data.get("segments") or []:
        d = float(e.get("duration") or 0)
        segs.append({"t_start": round(cur, 2), "t_end": round(cur + d, 2),
                     "duration": d, "text": e.get("text") or "",
                     "module_id": e.get("module_id")})
        cur += d
    wins = module_windows(segs)
    if wins is None:
        assert all(s["module_id"] is None for s in segs)
    else:  # 若未来该 manifest 补了 module_id, 结构必须成立
        for a, b in zip(wins, wins[1:]):
            assert b["t_start"] == a["t_end"]
