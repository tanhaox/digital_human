# -*- coding: utf-8 -*-
"""1101 批8 立意版本戳 (AUDIT E2): 保存/重掷升版 → 分镜 stale 可见 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import director2


def _doc():
    return {"arcs": [{"arc_id": "A1", "metaphor_core": "旧核心", "narrative": "场"}],
            "shots": [{"shot_id": "s1", "arc_id": "A1", "status": "planned",
                       "concept_rev": 0, "t_start": 0, "t_end": 5}]}


def _patch_io(monkeypatch, holder):
    monkeypatch.setattr(director2.shots_mod, "load_doc", lambda bt, ep: holder["doc"])
    monkeypatch.setattr(director2.shots_mod, "save", lambda d: holder.setdefault("saved", []).append(d) or None)
    monkeypatch.setattr(director2, "write_concept_md", lambda d: None)
    monkeypatch.setattr(director2, "_audit_concept", lambda arcs: [])


def test_save_concept_bumps_rev(monkeypatch):
    holder = {"doc": _doc()}
    _patch_io(monkeypatch, holder)
    director2.save_concept("书", 1, {"scenes": [
        {"arc_id": "A1", "metaphor_core": "新核心"}]})
    assert holder["doc"]["arcs"][0]["concept_rev"] == 1
    director2.save_concept("书", 1, {"scenes": [
        {"arc_id": "A1", "metaphor_core": "再新一版"}]})
    assert holder["doc"]["arcs"][0]["concept_rev"] == 2


def test_reroll_concept_bumps_rev(monkeypatch):
    holder = {"doc": _doc()}
    _patch_io(monkeypatch, holder)
    monkeypatch.setattr(director2, "load_or_derive_bible", lambda *a, **k: {})
    monkeypatch.setattr(director2, "_audience_rows", lambda bid: ("", []))
    monkeypatch.setattr(director2, "_audience_block", lambda l1, pools: "")
    monkeypatch.setattr(director2, "_reroll_concept_llm",
                        lambda arcs, arc, txt, wl, aud, banned, d="", dom_line="": dict(arc))
    director2.reroll_concept("b1", "书", 1, "A1")
    assert holder["doc"]["arcs"][0]["concept_rev"] == 1


def test_stamp_points_wired():
    # 源码级断言: plan_scene 合并尾 / plan_shot 落 concept_rev 戳 (防断链)
    import inspect
    src = inspect.getsource(director2)
    assert src.count('s["concept_rev"] = ') >= 2  # plan_scene 循环 + plan_shot
    assert 'a["concept_rev"] = int(a.get("concept_rev") or 0) + 1' in src


def test_status_and_gpu_gate_projection():
    import inspect

    from app.services import anim_service
    assert "stale_concept" in inspect.getsource(anim_service.status_view)
    assert "立意过期" in inspect.getsource(anim_service._phase_gpu)
    from pathlib import Path
    card = (Path("web/anim/card.js")).read_text(encoding="utf-8")
    assert "stale_concept" in card and "立意✱" in card
