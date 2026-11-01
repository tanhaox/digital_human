# -*- coding: utf-8 -*-
"""1101 批2 增强治理 (AUDIT A2): writer 免疫 / only 点名放行 / 多拍保拍 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import director2


def _shot(sid, **kw):
    base = {
        "shot_id": sid, "status": "planned", "t_start": 0.0, "t_end": 5.0,
        "keyframe_zh": "平视中景，讲台右侧立一面完全空白的白板，讲师侧身指向白板，暖光。",
        "motion_zh": "讲师手臂抬起指向白板，台下两人同时转头，环境光渐暖。",
        "keyframe_en": "eye-level medium shot, a blank whiteboard stands right of the podium.",
        "attempts": {"k2": 0, "h3": 0},
    }
    base.update(kw)
    return base


class _Boom:
    def chat(self, *a, **k):  # 任何 LLM 调用即测试失败
        raise AssertionError("LLM 不应被调用")


class _OkLLM:
    def __init__(self, payload):
        self._p = payload

    def chat(self, *a, **k):
        return self._p


def test_pass1_writer_shots_immune(monkeypatch):
    monkeypatch.setattr("app.services.llm_service.get_llm_service", lambda: _Boom())
    monkeypatch.setattr(director2, "load_or_derive_bible", lambda *a, **k: {})
    doc = {"arcs": [], "groups": [], "shots": [_shot("s1", writer_source="writer")]}
    n = director2._enhance_pass1(doc, "书", None, set())
    assert n == 0 and not doc["shots"][0].get("_enhanced1")


def test_pass1_writer_named_in_only_passes(monkeypatch):
    # only 显式点名 = 人工放行逃生口 (LLM 真被调, 增强产物过闸)
    monkeypatch.setattr(director2, "load_or_derive_bible", lambda *a, **k: {})
    kf = _shot("s1", writer_source="writer")["keyframe_zh"]
    monkeypatch.setattr("app.services.llm_service.get_llm_service",
                        lambda: _OkLLM(kf + "，尘埃在光柱里缓慢漂浮。"))
    doc = {"arcs": [], "groups": [], "shots": [_shot("s1", writer_source="writer")]}
    n = director2._enhance_pass1(doc, "书", {"s1"}, set())
    assert n == 1 and doc["shots"][0]["_enhanced1"]


def test_pass2_multibeat_preserved_no_llm(monkeypatch):
    # 保拍版: ≥2 拍不让位 LLM — 只跟锚 (opening_desc=keyframe_en), beats 原样
    monkeypatch.setattr("app.services.llm_service.get_llm_service", lambda: _Boom())
    beats = [{"t_start": 0, "t_end": 2.4, "motion": "beat A"},
             {"t_start": 2.4, "t_end": 5.0, "motion": "beat B"}]
    doc = {"shots": [_shot("s1", writer_source="fallback",
                           anim={"duration_s": 5.0, "opening_desc": "old desc",
                                 "beats": beats})]}
    n = director2._enhance_pass2(doc, None, set())
    assert n == 0
    s = doc["shots"][0]
    assert s["_enhanced2"]
    assert s["anim"]["beats"] == beats           # 拍结构/motion 未动
    assert s["anim"]["opening_desc"] == s["keyframe_en"]  # 锚已跟


def test_pass2_writer_immune(monkeypatch):
    monkeypatch.setattr("app.services.llm_service.get_llm_service", lambda: _Boom())
    doc = {"shots": [_shot("s1", writer_source="writer",
                           anim={"duration_s": 5.0, "opening_desc": "x",
                                 "beats": [{"t_start": 0, "t_end": 5.0, "motion": "m"}]})]}
    assert director2._enhance_pass2(doc, None, set()) == 0
    assert not doc["shots"][0].get("_enhanced2")
