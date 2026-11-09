# -*- coding: utf-8 -*-
"""1109 源稿指纹闸: 改稿后 ① 快照过期 → 相位/变更端点全链拒 (ep7 实锤:
改稿 → ⑤ TTS 仍读老稿 — ① 跑在改稿前, 快照无人作废)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import triage as v3triage
from app.services.anim_pipeline.v3_pipeline import run_phase


# ── 指纹本体 ───────────────────────────────────────────────────────

def test_source_hash_stable_and_discriminating():
    a = v3triage.source_hash("第一句。\n第二句。")
    assert a == v3triage.source_hash("第一句。\n第二句。")     # 稳定
    assert a != v3triage.source_hash("第一句。\n第二句改。")    # 改一字即变
    assert len(a) == 12


# ── ① 采指纹 ──────────────────────────────────────────────────────

def test_triage_run_stamps_source_hash(monkeypatch, tmp_path):
    from app.services.anim_pipeline import planner
    from app.services.anim_pipeline import config as anim_cfg
    from app.services.anim_pipeline import shots as shots_mod

    script = "第一句口播。\n第二句口播。"
    monkeypatch.setattr(anim_cfg, "load",
                        lambda: type("C", (), {"db_path": str(tmp_path / "x.db")})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    monkeypatch.setattr(planner, "fetch_episode", lambda db, bid, ep: {"script_text": script})
    monkeypatch.setattr(v3triage, "llm_triage",
                        lambda lines: (_ for _ in ()).throw(RuntimeError("无 LLM")))
    monkeypatch.setattr(shots_mod, "shots_path", lambda t, e: tmp_path / "nope.json")
    saved = {}
    monkeypatch.setattr(shots_mod, "save", lambda doc: saved.update(doc))
    v3triage.run({"book_id": "bid", "ep": 1})
    assert saved["v3"]["source_hash"] == v3triage.source_hash(script)


def test_triage_rerun_resets_downstream_gates_on_hash_change(monkeypatch, tmp_path):
    """指纹变 → ②③④ 闸回退; 同稿重跑 → 闸不动 (不误伤)。"""
    from app.services.anim_pipeline import planner
    from app.services.anim_pipeline import config as anim_cfg
    from app.services.anim_pipeline import shots as shots_mod

    doc_on_disk = {"book_title": "测试书", "ep": 1,
                   "v3": {"source_hash": v3triage.source_hash("老稿"),
                          "arcs": [{"arc_id": "A1"}],
                          "gates": {"triage": True, "arcs": True,
                                    "shots": True, "direct": True}}}
    cur = {"script_text": "老稿"}
    monkeypatch.setattr(anim_cfg, "load",
                        lambda: type("C", (), {"db_path": str(tmp_path / "x.db")})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    monkeypatch.setattr(planner, "fetch_episode", lambda db, bid, ep: cur)
    monkeypatch.setattr(v3triage, "llm_triage",
                        lambda lines: (_ for _ in ()).throw(RuntimeError("无 LLM")))
    p = tmp_path / "snap.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(shots_mod, "shots_path", lambda t, e: p)
    monkeypatch.setattr(shots_mod, "load_doc", lambda t, e: doc_on_disk)
    saved = {}
    monkeypatch.setattr(shots_mod, "save", lambda doc: saved.update(doc))

    v3triage.run({"book_id": "bid", "ep": 1})          # 同稿重跑
    assert saved["v3"]["gates"]["arcs"] is True         # 闸不动

    cur["script_text"] = "新稿"                          # 改稿
    v3triage.run({"book_id": "bid", "ep": 1})
    assert saved["v3"]["gates"]["arcs"] is False        # 闸回退
    assert saved["v3"]["gates"]["shots"] is False
    assert saved["v3"]["gates"]["direct"] is False
    assert saved["v3"]["source_hash"] == v3triage.source_hash("新稿")


def test_script_stale_compares_fingerprint(monkeypatch, tmp_path):
    from app.services.anim_pipeline import planner
    from app.services.anim_pipeline import config as anim_cfg
    from app.services.anim_pipeline import shots as shots_mod

    doc = {"v3": {"source_hash": v3triage.source_hash("老稿")}}
    cur = {"script_text": "新稿"}
    (tmp_path / "y.json").write_text("{}", encoding="utf-8")   # 快照在盘
    monkeypatch.setattr(anim_cfg, "load",
                        lambda: type("C", (), {"db_path": str(tmp_path / "x.db")})())
    monkeypatch.setattr(planner, "resolve_book", lambda db, ref: ("bid", "测试书"))
    monkeypatch.setattr(planner, "fetch_episode", lambda db, bid, ep: cur)
    monkeypatch.setattr(shots_mod, "shots_path", lambda t, e: tmp_path / "y.json")
    monkeypatch.setattr(shots_mod, "load_doc", lambda t, e: doc)
    assert v3triage.script_stale("bid", 1) is True          # 稿变 → 过期
    cur["script_text"] = "老稿"
    assert v3triage.script_stale("bid", 1) is False         # 同稿 → 新鲜
    doc2 = {"v3": {}}                                        # 无指纹 (存量集) → 不判
    monkeypatch.setattr(shots_mod, "load_doc", lambda t, e: doc2)
    assert v3triage.script_stale("bid", 1) is None


# ── 相位闸 ─────────────────────────────────────────────────────────

def test_run_phase_refuses_stale_snapshot(monkeypatch):
    """① 之外的相位: 快照过期 → SystemExit; ① 本体不查 (重跑=刷新指纹)。"""
    import pytest
    from app.services.anim_pipeline.v3_pipeline import triage as triage_mod

    monkeypatch.setattr(triage_mod, "script_stale",
                        lambda ref, ep: True, raising=True)
    with pytest.raises(SystemExit, match="稿件已改"):
        run_phase({"phase": "v3_arcs", "book_id": "b", "ep": 1})
    # 新鲜/无指纹 → 放行分发
    monkeypatch.setattr(triage_mod, "script_stale", lambda ref, ep: None)
    from app.services.anim_pipeline.v3_pipeline import arcs as arcs_mod
    hit = []
    monkeypatch.setattr(arcs_mod, "run", lambda job: hit.append(job["phase"]))
    run_phase({"phase": "v3_arcs", "book_id": "b", "ep": 1})
    assert hit == ["v3_arcs"]
    # ① 本体不走闸 (改稿后重跑分诊 = 正确动作)
    monkeypatch.setattr(triage_mod, "script_stale",
                        lambda ref, ep: (_ for _ in ()).throw(AssertionError("① 不许查")))
    monkeypatch.setattr(triage_mod, "run", lambda job: hit.append(job["phase"]))
    run_phase({"phase": "v3_triage", "book_id": "b", "ep": 1})
    assert hit[-1] == "v3_triage"


# ── ① 页编辑 → 下游闸回退 (1109 令55 补) ──────────────────────────

def test_invalidate_downstream_levels():
    v3 = {"gates": {"triage": True, "arcs": True, "shots": True, "direct": True}}
    assert v3triage.invalidate_downstream(v3, "arcs") is True      # 有闸被动
    assert v3["gates"] == {"triage": True, "arcs": False,
                           "shots": False, "direct": False}
    v3b = {"gates": {"triage": True, "arcs": True, "shots": True, "direct": False}}
    assert v3triage.invalidate_downstream(v3b, "shots") is True    # 采纳级: ② 不动
    assert v3b["gates"]["arcs"] is True
    assert v3b["gates"]["shots"] is False and v3b["gates"]["direct"] is False
    v3c = {"gates": {"triage": True, "arcs": False, "shots": False, "direct": False}}
    assert v3triage.invalidate_downstream(v3c, "arcs") is False    # 本就关 → 无动作


def test_triage_save_wiring_resets_downstream():
    src = open("app/routers/anim_v3.py", encoding="utf-8").read()
    assert "invalidate_downstream" in src                     # save 端点接线
    assert '"reset_downstream": reset' in src                 # 回包携带
    assert '"edited": bool(v3.get("lines_edited"))' in src    # ① GET 曝光手改旗标
    assert 'v3["lines_edited"] = True' in src
    tri_src = open("app/services/anim_pipeline/v3_pipeline/triage.py", encoding="utf-8").read()
    assert 'pop("lines_edited", None)' in tri_src             # 重导入清旗标
    js = open("web/v3/triage.js", encoding="utf-8").read()
    assert "②③④ 闸已打回" in js and "✏️ 有手改" in js        # 页面提示


# ── 路由接线 (源级断言) ────────────────────────────────────────────

def test_router_stale_gate_wiring():
    src = open("app/routers/anim_v3.py", encoding="utf-8").read()
    # 17 个变更端点全部带 ep 进闸 (GET 不拒 — 页面要能开才能看见警告)
    assert src.count("_line_gate(book_title, ep)") == 17
    # ① 本体 (triage/save) 不闸 — 分诊页编辑是重跑前奏
    assert "def v3_triage_save" in src
    # GET 曝光 stale 标志 (入口页/③ 页横幅数据源)
    assert '"script_stale": v3_pipeline.script_stale(book_title, ep)' in src
    js = open("web/v3/shots.js", encoding="utf-8").read()
    assert "稿件已改 — 本页是老稿快照" in js and "script_stale" in js
    idx = open("web/v3/index.js", encoding="utf-8").read()
    assert "script_stale" in idx and "① 分诊快照是老稿" in idx
