# -*- coding: utf-8 -*-
"""1105 现场修 病灶14: 句级表 <1.5s 孤立短句并入邻句 (规划真相源侧, 零 LLM).

ep6 job 8320fcbd 实锤: refs 协议镜界=句边, 静默口 0.42s/碎句 1.13s 成镜必然
违反 validate_doc ≥1.5s 最低时长闸, 系统查表定界与 LLM 无关 → 两把重掷同死。
合并只动 sentence_table (planner manifest 唯一消费方); _sentence_segs 细粒度不动。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_draft


def _segs(items):
    """[(t0, t1, text)] → segs 形状"""
    return [{"t_start": a, "t_end": b, "duration": round(b - a, 2), "text": t}
            for a, b, t in items]


def test_short_sentence_merged_into_prev(monkeypatch):
    monkeypatch.setattr(anim_draft, "_sentence_segs",
                        lambda files: _segs([(0, 5, "正常句。"), (5, 5.42, ""), (5.42, 9, "后续句。")]))
    out = anim_draft.sentence_table([])
    assert len(out) == 2                      # 静默口 0.42s 并入
    assert out[0]["t_start"] == 0 and out[0]["t_end"] == 5.42
    assert out[0]["text"] == "正常句。"
    assert out[1]["t_start"] == 5.42 and out[1]["t_end"] == 9


def test_leading_short_absorbs_next(monkeypatch):
    # 首句过短 → 悬挂等下一句吸收 (窗口保首句 t_start)
    monkeypatch.setattr(anim_draft, "_sentence_segs",
                        lambda files: _segs([(0, 1.0, "短。"), (1.0, 6.0, "长句内容。")]))
    out = anim_draft.sentence_table([])
    assert len(out) == 1
    assert out[0]["t_start"] == 0 and out[0]["t_end"] == 6.0
    assert out[0]["duration"] == 6.0
    assert "短。" in out[0]["text"] and "长句" in out[0]["text"]


def test_trailing_short_merged_back(monkeypatch):
    monkeypatch.setattr(anim_draft, "_sentence_segs",
                        lambda files: _segs([(0, 6, "长句。"), (6, 6.8, "尾碎。")]))
    out = anim_draft.sentence_table([])
    assert len(out) == 1 and out[0]["t_end"] == 6.8 and "尾碎" in out[0]["text"]


def test_no_short_untouched_and_single_short_kept(monkeypatch):
    src = _segs([(0, 5, "甲。"), (5, 10, "乙。"), (10, 16, "丙。")])
    monkeypatch.setattr(anim_draft, "_sentence_segs", lambda files: [dict(s) for s in src])
    out = anim_draft.sentence_table([])
    assert [(s["t_start"], s["t_end"]) for s in out] == [(0, 5), (5, 10), (10, 16)]
    # 全集仅一句且过短的极端态: 原样返回 (无邻可并)
    monkeypatch.setattr(anim_draft, "_sentence_segs",
                        lambda files: _segs([(0, 1.0, "孤。")]))
    assert len(anim_draft.sentence_table([])) == 1


def test_merged_boundaries_subset_of_original_edges(monkeypatch):
    # 关键安全性质: 合并后的外边界 ⊆ 原边界 → 时间纪律闸 (吃 _sentence_segs) 不破
    raw = _segs([(0, 5, "甲。"), (5, 5.42, ""), (5.42, 10, "乙。"), (10, 11.1, "碎。"), (11.1, 15, "丙。")])
    monkeypatch.setattr(anim_draft, "_sentence_segs", lambda files: [dict(s) for s in raw])
    out = anim_draft.sentence_table([])
    raw_edges = {(s["t_start"], s["t_end"]) for s in raw}
    flat = set()
    for s in raw:
        flat |= {s["t_start"], s["t_end"]}
    for s in out:
        assert s["t_start"] in flat and s["t_end"] in flat
    assert all((s["t_end"] - s["t_start"]) >= 1.5 for s in out)
    assert out[-1]["t_end"] == 15            # 总时长无损
