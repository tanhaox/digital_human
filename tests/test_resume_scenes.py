# -*- coding: utf-8 -*-
"""1105 批1 分镜续跑升级: 可续跑判定单一事实源 (resume_todo/_todo_set) + 投影接线 (零 GPU).

病灶1: 全场有镜但带病时 ▶️ 按钮被隐藏, 终产闸"续跑会自动重做带病场"指路断头。
病灶7: /plan-scenes 入口同步预检 (无骨架/无可续跑 → 422, 不再 doomed job)。
病灶9: _wf_warns 白名单投影 (card.js 死徽章)。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import planner


def _doc():
    """四态集: A1 缺镜 / A2 有镜带病 / A3 有镜健康 / A4 有镜带病但有产物(锁定)."""
    return {
        "arcs": [{"arc_id": "A1"}, {"arc_id": "A2"}, {"arc_id": "A3"}, {"arc_id": "A4"}],
        "shots": [
            {"shot_id": "s1", "arc_id": "A2", "status": "planned"},
            {"shot_id": "s2", "arc_id": "A3", "status": "planned"},
            {"shot_id": "s3", "arc_id": "A4", "status": "img_done"},  # 产物=锁定
        ],
    }


def test_todo_set_missing_sick_protected(monkeypatch):
    # 终产闸桩: 硬违规归因 A2 / 软⚠不拦 / 全集级归因不到任何场
    monkeypatch.setattr(planner, "_final_gate_issues", lambda doc, bible: [
        "人物密度不足 场A2[s1]", "⚠ 花字风格问题 场A2", "末组终点失配 (全集级)"])
    todo, sick, protected = planner._todo_set(_doc(), bible={"style_prefix": "x"})
    assert todo == ["A1", "A2"]          # A1 缺镜 + A2 带病; A3 健康, A4 锁定不重开
    assert list(sick) == ["A2"]          # 软⚠不进 sick, 全集级无人认领
    assert protected == {"A4"}


def test_todo_set_style_keyword_not_sick(monkeypatch):
    # 无 ⚠ 前缀但含风格关键词 (花字) — 与 _scenes_flow 同一把尺: 不阻断不重开
    monkeypatch.setattr(planner, "_final_gate_issues",
                        lambda doc, bible: ["场A2 花字单字违规"])
    todo, sick, _ = planner._todo_set(_doc(), bible={"style_prefix": "x"})
    assert todo == ["A1"] and sick == {}


def test_todo_set_no_bible_degrades_to_missing_only():
    # bible=None (只读路径缺圣经不 derive) → 只算缺镜场, 不炸
    todo, sick, _ = planner._todo_set(_doc(), bible=None)
    assert todo == ["A1"] and sick == {}


def test_resume_todo_ready_and_closed_loop_semantics(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "load",
                        lambda: type("C", (), {"db_path": ":memory:"})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    from app.services.anim_pipeline import director2
    monkeypatch.setattr(director2, "_bible_cache_path",
                        lambda bt: tmp_path / "missing_bible.json")
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: _doc())
    rt = planner.resume_todo("bid", 1)
    assert rt["ready"] is True and rt["todo"] == ["A1"] and rt["arcs"] == 4
    assert rt["protected"] == ["A4"]
    # 病灶1 语义: 全场有镜且过闸 → ready=False + 指路走场头 (而非隐藏按钮的假待续跑)
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: {
        "arcs": [{"arc_id": "A1"}],
        "shots": [{"shot_id": "s1", "arc_id": "A1", "status": "approved"}]})
    rt2 = planner.resume_todo("bid", 1)
    assert rt2["ready"] is False and "场头 🔁" in rt2["reason"]
    # 无骨架 → 明确 reason
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: {"shots": []})
    rt3 = planner.resume_todo("bid", 1)
    assert rt3["ready"] is False and "🧭" in rt3["reason"]


def test_resume_todo_sick_projection_truncated(monkeypatch, tmp_path):
    # 投影口径: sick 每场 ≤3 条 × 140 字 (fix_hints 全量留在 _todo_set 返回)
    import json
    monkeypatch.setattr(planner, "load",
                        lambda: type("C", (), {"db_path": ":memory:"})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    from app.services.anim_pipeline import director2
    _bible = tmp_path / "圣经_测试书.json"
    _bible.write_text(json.dumps({"style_prefix": "国风"}, ensure_ascii=False),
                      encoding="utf-8")
    monkeypatch.setattr(director2, "_bible_cache_path", lambda bt: _bible)
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: _doc())
    monkeypatch.setattr(planner, "_final_gate_issues", lambda doc, bible: [
        f"违规{i} 场A2[s1] " + "x" * 300 for i in range(5)])
    rt = planner.resume_todo("bid", 1)
    assert len(rt["sick"]["A2"]) == 3 and all(len(p) <= 140 for p in rt["sick"]["A2"])


def test_wiring_source_asserts():
    # 源码级断言 (test_audio_stamp.py 范式): 投影/预检/白名单 防断链
    import inspect
    from pathlib import Path
    from app.services import anim_service
    from app.routers import anim as anim_router
    sv = inspect.getsource(anim_service.status_view)
    assert "resume_todo" in sv and "plan_warnings" in sv and '"_wf_warns"' in sv
    ps = inspect.getsource(anim_router.ep_plan_scenes)
    assert "resume_todo" in ps and "HTTPException(status_code=422" in ps
    ut = Path("web/anim/util.js").read_text(encoding="utf-8")
    assert "st.resume_todo" in ut           # scenesReady 单一事实源
    cj = Path("web/anim/concept.js").read_text(encoding="utf-8")
    assert "st.resume_todo" in cj
    tb = Path("web/anim/topbar.js").read_text(encoding="utf-8")
    assert "plan_warnings" in tb and "resume_todo" in tb
    sj = Path("web/anim/shooting.js").read_text(encoding="utf-8")
    assert "fileUrl('shooting_script.json', media.ver)" in sj
