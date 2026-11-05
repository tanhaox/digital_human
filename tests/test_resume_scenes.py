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


def _patch_resume_base(monkeypatch, doc, audio=None, bible=None):
    """resume_scenes 入口桩: DB/音频/圣经/法层全离线, 尾段 _scenes_flow 打桩."""
    from app.services import anim_draft
    from app.services.anim_pipeline import director2
    audio = audio or {"job_id": "aj1", "files": [
        {"file": "/x/1.wav", "text": "第一包", "dur_s": 2.0}]}
    monkeypatch.setattr(planner, "load", lambda: type("C", (), {"db_path": ":memory:"})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    monkeypatch.setattr(planner, "fetch_episode", lambda db, bid, ep: {})
    monkeypatch.setattr(anim_draft, "fetch_episode_audio", lambda bid, ep: audio)
    monkeypatch.setattr(director2, "load_or_derive_bible", lambda bid, bt, *a, **k: (bible or {}))
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: doc)
    monkeypatch.setattr(planner, "_audio_manifest", lambda au: {"segments": []})
    monkeypatch.setattr(
        "app.services.anim_pipeline.shooting_script.load",
        lambda bt, ep: {"scenes": [{"arc_id": "A1"}]})   # 法层在盘 (默认过闸1)


def test_resume_rejects_stale_audio(monkeypatch):
    # 1105 批4-闸2 (病灶5, 用户拍板硬拒): 盘上 stamp ≠ 当前音频指纹 → SystemExit
    doc = {"arcs": [{"arc_id": "A1"}], "shots": [], "planner_flow": "director2",
           "audio_stamp": {"job_id": "OLD", "text_sha": "deadbeef", "packs": 1, "total_s": 9.9}}
    _patch_resume_base(monkeypatch, doc, audio={"job_id": "NEW", "files": [
        {"file": "/x/1.wav", "text": "第一包改字", "dur_s": 2.0}]})
    try:
        planner.resume_scenes("bid", 1)
        assert False
    except SystemExit as exc:
        assert "音频已换轨" in str(exc) and "重新验收" in str(exc)


def test_resume_same_track_passes_stamp_gate(monkeypatch, tmp_path):
    # 同轨 (stamp 一致) → 过指纹闸, 进 _scenes_flow
    doc = {"arcs": [{"arc_id": "A1"}], "shots": []}
    _patch_resume_base(monkeypatch, doc)
    from app.services import anim_draft
    doc["audio_stamp"] = anim_draft.audio_stamp(doc, {"job_id": "aj1", "files": [
        {"file": "/x/1.wav", "text": "第一包", "dur_s": 2.0}]})
    seen = {}
    monkeypatch.setattr(planner, "_scenes_flow",
                        lambda *a, **k: seen.setdefault("ok", True) or __import__("pathlib").Path("x"))
    planner.resume_scenes("bid", 1)
    assert seen.get("ok") is True


def test_resume_regenerates_missing_shooting(monkeypatch):
    # 1105 批4-闸1 (病灶4): 法层缺 → 现场重生; 成功后照常进 _scenes_flow
    calls = {"load": 0, "gen": 0}

    def fake_load(bt, ep):
        calls["load"] += 1
        return {"scenes": [{"arc_id": "A1"}]} if calls["load"] > 1 else None
    monkeypatch.setattr(planner, "load", lambda: type("C", (), {"db_path": ":memory:"})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    monkeypatch.setattr(planner, "fetch_episode", lambda db, bid, ep: {})
    from app.services import anim_draft
    from app.services.anim_pipeline import director2
    monkeypatch.setattr(anim_draft, "fetch_episode_audio", lambda bid, ep: {
        "job_id": "aj1", "files": [{"file": "/x/1.wav", "text": "一", "dur_s": 2.0}]})
    monkeypatch.setattr(director2, "load_or_derive_bible", lambda *a, **k: {})
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: {"arcs": [{"arc_id": "A1"}]})
    monkeypatch.setattr(planner, "_audio_manifest", lambda au: {"segments": []})
    monkeypatch.setattr("app.services.anim_pipeline.shooting_script.load", fake_load)
    monkeypatch.setattr("app.services.anim_pipeline.shooting_script.generate",
                        lambda bid, bt, ep: calls.__setitem__("gen", calls["gen"] + 1)
                        or {"scenes": [{"arc_id": "A1"}]})
    monkeypatch.setattr("app.services.anim_pipeline.shooting_script.validate",
                        lambda ss: [])
    monkeypatch.setattr(planner, "_scenes_flow",
                        lambda *a, **k: __import__("pathlib").Path("x"))
    planner.resume_scenes("bid", 1)
    assert calls["gen"] == 1            # 缺法层 → 重生了一把即成功


def test_resume_rejects_when_shooting_regen_fails(monkeypatch):
    # 法层缺 + 重生两把均败 → 拒绝裸奔分镜
    monkeypatch.setattr(planner, "load", lambda: type("C", (), {"db_path": ":memory:"})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    monkeypatch.setattr(planner, "fetch_episode", lambda db, bid, ep: {})
    from app.services import anim_draft
    from app.services.anim_pipeline import director2
    monkeypatch.setattr(anim_draft, "fetch_episode_audio", lambda bid, ep: {
        "job_id": "aj1", "files": [{"file": "/x/1.wav", "text": "一", "dur_s": 2.0}]})
    monkeypatch.setattr(director2, "load_or_derive_bible", lambda *a, **k: {})
    monkeypatch.setattr(planner.shots_mod, "load_doc", lambda bt, ep: {"arcs": [{"arc_id": "A1"}]})
    monkeypatch.setattr(planner, "_audio_manifest", lambda au: {"segments": []})
    monkeypatch.setattr("app.services.anim_pipeline.shooting_script.load",
                        lambda bt, ep: None)

    def _gen_fail(bid, bt, ep):
        raise RuntimeError("LLM down")
    monkeypatch.setattr("app.services.anim_pipeline.shooting_script.generate", _gen_fail)
    try:
        planner.resume_scenes("bid", 1)
        assert False
    except SystemExit as exc:
        assert "裸奔" in str(exc)


def test_scenes_flow_stop_immediately(monkeypatch):
    # 1105 批3: stop_check 立即真 → 第 0 场循环顶即 SystemExit, 不碰任何 LLM
    monkeypatch.setattr(planner, "_todo_set", lambda d, b: (["A1", "A2"], {}, set()))

    def _boom(*a, **k):
        raise AssertionError("stop 应在任何场规划前拦下")
    monkeypatch.setattr(planner, "_dispatch_scene", _boom)
    try:
        planner._scenes_flow("bid", "书", 1, manifest={}, bible=None, doc=_doc(),
                             stop_check=lambda: True)
        assert False
    except SystemExit as exc:
        assert "用户停止" in str(exc) and "0/2" in str(exc)


def test_scenes_flow_stop_between_scenes(monkeypatch):
    # 第 1 场完成后拉 stop → 第 2 场循环顶 SystemExit("1/2"), 不规划第 2 场
    calls = {"n": 0}

    def fake_dispatch(*a, **k):
        calls["n"] += 1
        return _doc()
    monkeypatch.setattr(planner, "_todo_set", lambda d, b: (["A1", "A2"], {}, set()))
    monkeypatch.setattr(planner, "_dispatch_scene", fake_dispatch)
    monkeypatch.setattr(planner.shots_mod, "save", lambda d: None)
    monkeypatch.setattr(planner, "_arc_hard_issues", lambda d, b, a: [])
    try:
        planner._scenes_flow("bid", "书", 1, manifest={}, bible=None, doc=_doc(),
                             stop_check=lambda: calls["n"] >= 1)
        assert False
    except SystemExit as exc:
        assert "1/2" in str(exc) and "用户停止" in str(exc)
    assert calls["n"] == 1


def test_scenes_flow_progress_and_report(monkeypatch):
    # 1105 批5: 场级进度回调序列 + report 回报 (resumed/reopened_sick/planned_total)
    from pathlib import Path
    from app.services.anim_pipeline import director2
    calls = {"n": 0, "prog": []}

    def fake_dispatch(*a, **k):
        calls["n"] += 1
        return _doc()

    def fake_prog(done, total, cur):
        calls["prog"].append((done, total, cur))

    monkeypatch.setattr(planner, "_todo_set",
                        lambda d, b: (["A1", "A2"], {"A2": ["违规 场A2[s1]"]}, set()))
    monkeypatch.setattr(planner, "_dispatch_scene", fake_dispatch)
    monkeypatch.setattr(planner.shots_mod, "save", lambda d: None)
    monkeypatch.setattr(planner, "_arc_hard_issues", lambda d, b, a: [])
    monkeypatch.setattr(director2, "redesign_split_clones",
                        lambda doc, bible=None: {"redesigned": 0, "failed": 0})
    monkeypatch.setattr(director2, "consistency_review",
                        lambda doc, bible=None: {"patched": 0})
    monkeypatch.setattr(planner, "_final_gate_issues", lambda doc, bible: [])
    monkeypatch.setattr(planner, "_finalize_doc", lambda doc, segs, tag: Path("x"))
    report: dict = {}
    planner._scenes_flow("bid", "书", 1, manifest={}, bible=None, doc=_doc(),
                         on_progress=fake_prog, report=report)
    assert report == {"planned_total": 2, "resumed": ["A1", "A2"],
                      "reopened_sick": ["A2"]}
    assert calls["prog"][0] == (0, 2, "A1 规划中")
    assert calls["prog"][1] == (1, 2, "A2 规划中")
    assert calls["prog"][2][2].startswith("收尾链")
    # 接线: _phase_plan 闭包 + result 回报
    import inspect
    from app.services import anim_service as _svc
    pp = inspect.getsource(_svc._phase_plan)
    assert "on_progress=_prog" in pp and "**_report" in pp


def test_stop_check_wiring_asserts():
    # 源码级断言: stop_check 贯通 plan/_scorched_flow/resume_scenes/_scenes_flow
    import inspect
    assert "stop_check" in inspect.signature(planner.plan).parameters
    assert "stop_check" in inspect.signature(planner.resume_scenes).parameters
    assert "stop_check" in inspect.signature(planner._scenes_flow).parameters
    from app.services import anim_service as _svc
    pp = inspect.getsource(_svc._phase_plan)
    assert "stop_check=_stop" in pp
    rj = inspect.getsource(_svc._run_job)
    assert "已停止" in rj          # SystemExit+cancel → cancelled 分支


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
