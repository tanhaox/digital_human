# -*- coding: utf-8 -*-
"""refs 协议 (0917 模块总线) — 句号引用/查表展开/密度执法纯逻辑测试."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.anim_pipeline.director2 import expand_refs_groups, refs_parse


def _table(specs: list[tuple[float, str]]) -> list[dict]:
    """(时长, 文本) → 句级表 (t 连续铺)."""
    out, cur = [], 0.0
    for d, txt in specs:
        out.append({"t_start": round(cur, 2), "t_end": round(cur + d, 2),
                    "duration": d, "text": txt})
        cur += d
    return out


# 10 句 × ~2.2s ≈ 22s 场窗
TABLE = _table([(2.2, f"第{i}句话。") for i in range(1, 11)])


def test_refs_parse_forms():
    assert refs_parse("c01-c03") == [0, 1, 2]
    assert refs_parse("c1-c2") == [0, 1]
    assert refs_parse("c01,c03") == [0, 2]
    assert refs_parse(["c01", "c02"]) == [0, 1]
    assert refs_parse("c05") == [4]
    assert refs_parse(2) == [1]
    assert refs_parse(None) is None
    assert refs_parse("随便写的") is None
    assert refs_parse("c03-c01") is None  # 倒序非法


def test_expand_basic():
    """两组 refs → 查表 t/narration 精确, 覆盖全部句."""
    groups = [{"shots": [
        {"refs": "c01-c04", "keyframe_zh": "镜1"},
        {"refs": "c05-c08", "keyframe_zh": "镜2"},
        {"refs": "c09-c10", "keyframe_zh": "镜3"},
    ]}]
    out, notes = expand_refs_groups(groups, TABLE, 0.0, 22.0)
    shots = out[0]["shots"]
    assert len(shots) == 3
    assert shots[0]["t_start"] == 0.0 and shots[0]["t_end"] == 8.8
    assert shots[1]["t_start"] == 8.8 and shots[2]["t_end"] == 22.0
    assert shots[0]["narration"] == "第1句话。第2句话。第3句话。第4句话。"
    assert not notes


def test_expand_leaked_sentence_merged():
    """漏句 c05 → 并入相邻镜 (c04 或 c06 所在镜)."""
    groups = [{"shots": [
        {"refs": "c01-c04"}, {"refs": "c06-c10"},
    ]}]
    out, _ = expand_refs_groups(groups, TABLE, 0.0, 22.0)
    shots = out[0]["shots"]
    covered = "".join(s["narration"] for s in shots)
    assert "第5句话。" in covered  # 漏句不丢
    # c05 并入含 c04 或 c06 的镜
    host = next(s for s in shots if "第5句话。" in s["narration"])
    assert "第4句话。" in host["narration"] or "第6句话。" in host["narration"]


def test_expand_density_merge():
    """<4s 短镜自动并入邻镜 (密度执法); 并镜后超限 (>10s) 再按句切块 (H3 执法)."""
    # c03 (2.2s) 单独成镜 → 必被并入; c04-c10 (15.4s) 超限 → 切块
    groups = [{"shots": [
        {"refs": "c01-c02"}, {"refs": "c03"}, {"refs": "c04-c10"},
    ]}]
    out, notes = expand_refs_groups(groups, TABLE, 0.0, 22.0)
    shots = out[0]["shots"]
    assert any("密度执法" in n for n in notes)
    # c03 并入 c01-c02 镜
    assert "第3句话。" in shots[0]["narration"]
    # 拆分后每镜 ≤ H3 上限, 全覆盖首尾相接
    spans = [round(s["t_end"] - s["t_start"], 2) for s in shots]
    assert max(spans) <= 10.5, spans
    assert shots[0]["t_start"] == 0.0 and shots[-1]["t_end"] == 22.0
    for a, b in zip(shots, shots[1:]):
        assert b["t_start"] == a["t_end"]


def test_expand_broken_refs_fallback():
    """refs 不可解析 → 整场回退 (None)."""
    groups = [{"shots": [{"refs": "不明的引用"}]}]
    out, notes = expand_refs_groups(groups, TABLE, 0.0, 22.0)
    assert out is None and notes


def test_expand_out_of_window_clamp():
    """越界引用 clamp 进场窗 (整窗 22s 超限 → 合法切块)."""
    groups = [{"shots": [{"refs": "c01-c99"}]}]
    out, notes = expand_refs_groups(groups, TABLE, 0.0, 22.0)
    assert out is not None
    assert any("越界" in n or "clamp" in n for n in notes)
    shots = out[0]["shots"]
    assert shots[-1]["t_end"] == TABLE[-1]["t_end"]
    assert max(s["t_end"] - s["t_start"] for s in shots) <= 10.5


def test_expand_window_filter():
    """句级表全集 > 场窗: 只用窗内句, 编号按窗内局部重排 (c01=窗内首句)."""
    big = _table([(2.2, f"句{i}。") for i in range(30)])
    # 窗 = 22.0-44.0 → 窗内 10 句 = 句10..句19 (局部 c01-c10)
    groups = [{"shots": [{"refs": "c01-c10"}]}]
    out, _ = expand_refs_groups(groups, big, 22.0, 44.0)
    shots = out[0]["shots"]
    covered = "".join(s["narration"] for s in shots)
    assert shots[0]["t_start"] == 22.0
    assert "句10。" in covered and "句9。" not in covered
    assert shots[-1]["t_end"] == 44.0


def test_expand_h3_cap_split():
    """>10s 超长镜按句集中点拆两镜 (0918 s26 实锤: refs 镜界=句边, 旧包级拆镜失配)."""
    segs = _table([(2.6, f"句{i}。") for i in range(1, 21)])  # 20 句 52s 同场
    groups = [{"shots": [{"refs": "c01-c20"}]}]  # LLM 出了一镜 52s
    out, notes = expand_refs_groups(groups, segs, 0.0, 52.0)
    shots = out[0]["shots"]
    assert len(shots) >= 5, [round(s["t_end"] - s["t_start"], 1) for s in shots]
    spans = [round(s["t_end"] - s["t_start"], 1) for s in shots]
    assert max(spans) <= 10.5, spans  # 拆后每镜 ≤ H3 上限
    assert any("H3上限" in n for n in notes)
    # 拆分镜首尾相接
    for a, b in zip(shots, shots[1:]):
        assert b["t_start"] == a["t_end"]


def test_h3_split_tail_balance():
    """拆块尾块均衡: 尾块 <4s 时前块末句下放, 分身不出短镜 (s08b 实锤)."""
    # 7 句: 1.4s×6 + 末句 1.6s = 10.0s 单镜 → 拆块贪心在 8s 处切 → 尾 2s 短块 → 均衡
    durs = [1.4] * 6 + [1.6]
    segs = _table(list(zip(durs, [f"句{i}。" for i in range(1, 8)])))
    groups = [{"shots": [{"refs": "c01-c07"}]}]
    out, _ = expand_refs_groups(groups, segs, 0.0, 10.0)
    spans = [round(s["t_end"] - s["t_start"], 2) for s in out[0]["shots"]]
    assert max(spans) <= 10.05
    assert min(spans) >= 3.0, spans  # 无碎尾


def test_strip_span_growth_clears_video():
    """槽变长>0.3s 且有视频 → 清视频回 approved (0918 根因回归: 旧序 stretch→读
    恒读新值, 保护从未生效 — s17/s23 短片粉卡实锤)。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.anim_pipeline import shots as shots_mod
    old_doc = {"shots": [{"shot_id": "sA", "narration": "同一句",
                          "t_start": 0, "t_end": 8.0, "anim": {"duration_s": 8.0}}]}
    new_doc = {"shots": [{"shot_id": "sA", "narration": "同一句",
                          "t_start": 0, "t_end": 9.4, "anim": {"duration_s": 8.0},
                          "video_file": "anim/sA.mp4", "status": "anim_done",
                          "attempts": {"k2": 1, "h3": 1}}]}
    st = shots_mod.strip_media_on_narr_change(old_doc, new_doc)
    s = new_doc["shots"][0]
    assert st and "槽" in st[0], st
    assert "video_file" not in s and s["status"] == "approved"
    assert s["anim"]["duration_s"] == 9.4  # beats 已按新槽重排
