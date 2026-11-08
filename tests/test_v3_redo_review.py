# -*- coding: utf-8 -*-
"""1108 v2 机制 v3 继承 (用户令「重渲标记 v3 没继承」): R 标 + k2 生图次数."""
import inspect
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())


def test_mark_redo_review_v3_status_gates(monkeypatch):
    """R 标只认「产物是新的」: to_image 线认 img_done, 全链线认 anim_done."""
    from app.services.anim_pipeline.v3_pipeline import redo
    from app.services import anim_service
    from app.services.anim_pipeline import shots as shots_mod

    doc = {"v3": {"shots": [
        {"sid": "s1", "k2": {"status": "img_done"}},
        {"sid": "s2", "k2": {"status": "failed"}},
    ]},
        "shots": [
            {"shot_id": "s1", "status": "anim_done"},
            {"shot_id": "s2", "status": "anim_fail"},
        ]}
    monkeypatch.setattr(shots_mod, "load_doc", lambda bt, ep: doc)
    saved: dict = {}
    monkeypatch.setattr(anim_service, "_load_review_flags", lambda bt, ep: {})
    monkeypatch.setattr(anim_service, "_save_review_flags",
                        lambda bt, ep, fl: saved.update(fl))
    job = {"book_title": "X", "ep": 1}

    redo._mark_redo_review_v3(job, ["s1", "s2"], "img_done")
    assert saved == {"s1": True}, saved          # s2 生图失败不标

    saved.clear()
    redo._mark_redo_review_v3(job, ["s1", "s2"], "anim_done")
    assert saved == {"s1": True}, saved          # s2 视频失败不标


def test_mark_redo_review_v3_fail_open(monkeypatch):
    """打标失败不抛 (不污染任务结果) — load_doc 炸也静默."""
    from app.services.anim_pipeline.v3_pipeline import redo
    from app.services.anim_pipeline import shots as shots_mod

    def _boom(bt, ep):
        raise RuntimeError("盘不存在")

    monkeypatch.setattr(shots_mod, "load_doc", _boom)
    redo._mark_redo_review_v3({"book_title": "X", "ep": 1}, ["s1"], "img_done")


def test_router_wiring_reviewed_and_projection():
    from app.routers import anim_v3

    src = inspect.getsource(anim_v3)
    assert "/reviewed/{sid}" in src            # viewed 端点 (点开/播放即消)
    assert src.count("redo_review") >= 2       # ⑥ GET + ⑧ GET 两投影
    assert "review_cleared" in src             # 过审顺带清标有回执
    assert "k2_runs" in src                    # ⑥ GET 投影生图次数


def test_k2_runs_counter_survives_redo_pop():
    """计数在镜级 (s['k2_runs']) — redo 整个 pop('k2') 清不掉它."""
    from app.services.anim_pipeline.v3_pipeline import k2 as v3k2
    from app.services.anim_pipeline.v3_pipeline import redo

    assert '"k2_runs"' in inspect.getsource(v3k2)      # 成功分支 +1
    assert 'pop("k2"' in inspect.getsource(redo)       # redo 只清 k2 子键
