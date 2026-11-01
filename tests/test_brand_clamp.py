# -*- coding: utf-8 -*-
"""1101 批9 品牌槽长钳制 (AUDIT I1): 归真层钳制·合法性护栏 + 实测时长 sidecar (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_draft
from app.services.anim_pipeline import brand as brand_mod

_TC = 0.0


def _shot(sid, t0, t1, **kw):
    s = {"shot_id": sid, "t_start": t0, "t_end": t1, "narration": kw.pop("narration", "我是老谭，拆一本书翻一座山"),
         "anim": {"duration_s": round(t1 - t0, 2), "opening_desc": "d",
                  "beats": [{"t_start": 0, "t_end": round(t1 - t0, 2), "motion": "m"}]}}
    s.update(kw)
    return s


def _segs(edges):
    out = []
    for i in range(0, len(edges) - 1):
        out.append({"t_start": edges[i], "t_end": edges[i + 1], "text": f"句{i}"})
    return out


def test_clamp_mid_brand(monkeypatch):
    monkeypatch.setattr(brand_mod, "brand_duration", lambda bt: 5.0)
    doc = {"book_title": "书", "shots": [
        _shot("s1", 0, 10, brand_card=True), _shot("s2", 10, 15, narration="下一句口播")]}
    n = anim_draft.clamp_brand_slots(doc, _segs([0, 5.5, 10, 15]), _TC)
    assert n == 1
    b, nx = doc["shots"]
    assert b["t_end"] == 5.5 and nx["t_start"] == 5.5     # 钳到句边, 差额并下镜
    assert nx["anim"]["duration_s"] == 9.5                 # 下镜 beats 已拉到新槽


def test_clamp_skips_tail_brand(monkeypatch):
    monkeypatch.setattr(brand_mod, "brand_duration", lambda bt: 5.0)
    doc = {"book_title": "书", "shots": [_shot("s1", 0, 10, brand_card=True)]}  # 尾镜无下镜
    assert anim_draft.clamp_brand_slots(doc, _segs([0, 5.5, 10]), _TC) == 0
    assert doc["shots"][0]["t_end"] == 10                  # 不钳 (装配端定格兜底)


def test_clamp_skips_when_no_legal_edge(monkeypatch):
    monkeypatch.setattr(brand_mod, "brand_duration", lambda bt: 5.0)
    doc = {"book_title": "书", "shots": [
        _shot("s1", 0, 10, brand_card=True), _shot("s2", 10, 15, narration="口播")]}
    # 句边只有 4.0 (< t0+5-0.05) 与 10.0 (= t1) → 窗口内无合法边
    assert anim_draft.clamp_brand_slots(doc, _segs([0, 4.0, 10, 15]), _TC) == 0
    assert doc["shots"][0]["t_end"] == 10


def test_clamp_within_tolerance_noop(monkeypatch):
    monkeypatch.setattr(brand_mod, "brand_duration", lambda bt: 5.0)
    doc = {"book_title": "书", "shots": [
        _shot("s1", 0, 5.2, brand_card=True), _shot("s2", 5.2, 9, narration="口播")]}
    assert anim_draft.clamp_brand_slots(doc, _segs([0, 5.2, 9]), _TC) == 0


def test_clamp_skips_long_copy_brand(monkeypatch):
    # 口播含非仪式句 (长文案镜) — 归属未定, 不钳 (装包器级挂账)
    monkeypatch.setattr(brand_mod, "brand_duration", lambda bt: 5.0)
    doc = {"book_title": "书", "shots": [
        _shot("s1", 0, 10, brand_card=True, narration="这一章我们讲信任的三大支柱与实操方法"),
        _shot("s2", 10, 15, narration="口播")]}
    assert anim_draft.clamp_brand_slots(doc, _segs([0, 5.5, 10, 15]), _TC) == 0


def test_brand_duration_sidecar(monkeypatch, tmp_path):
    class _P:
        def __init__(self, p):
            self.p = p

        def exists(self):
            return True

        def with_suffix(self, suf):
            return self.p.with_suffix(suf)

    vid = tmp_path / "品牌卡_书.mp4"
    vid.write_bytes(b"f")
    monkeypatch.setattr(brand_mod, "brand_paths",
                        lambda bt: {"video": vid, "keyframe": tmp_path / "k.png"})
    calls = []

    class _Probe:
        stdout = "5.17"

    monkeypatch.setattr(brand_mod.subprocess, "run",
                        lambda *a, **k: calls.append(1) or _Probe())
    assert brand_mod.brand_duration("书") == 5.17
    assert brand_mod.brand_duration("书") == 5.17           # 第二次读 sidecar
    assert len(calls) == 1                                  # ffprobe 只探一次
