# -*- coding: utf-8 -*-
"""1105 批6 (T1) LLM token 台账: 作用域隔离/落盘字段/job 累计/两套栈接线 (零 LLM)."""
import json
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import ledger
from app.services.anim_pipeline import llm as pipeline_llm


def _tmp_ep(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger.shots_mod, "ep_dir",
                        lambda bt, ep: tmp_path / f"{bt}/ep{ep}")


def test_note_usage_noop_without_scope(monkeypatch, tmp_path):
    _tmp_ep(monkeypatch, tmp_path)
    ledger.note_usage("plan_scene", "kimi", 100, 50)   # 无 scope → 静默不落
    assert not (tmp_path / "llm_ledger.jsonl").exists()
    assert not list(tmp_path.rglob("llm_ledger.jsonl"))


def test_llm_scope_records_and_accumulates(monkeypatch, tmp_path):
    _tmp_ep(monkeypatch, tmp_path)
    with ledger.llm_scope("测试书", 3, "plan") as sc:
        ledger.note_usage("plan_scene", "kimi", 1000, 200)
        ledger.note_usage("plan_scene", "kimi", 500, 100)
        ledger.note_usage("huazi", "deepseek", 300, 60)
        assert sc.calls == 3 and sc.ptok == 1800 and sc.ctok == 360
        assert sc.by_tag["plan_scene"] == [2, 1500, 300]
    f = tmp_path / "测试书" / "ep3" / "llm_ledger.jsonl"
    lines = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    assert lines[0]["kind"] == "llm" and lines[0]["tag"] == "plan_scene"
    assert lines[0]["ptok"] == 1000 and lines[0]["phase"] == "plan"
    assert lines[2]["tag"] == "huazi" and lines[2]["ctok"] == 60
    # scope 退出后不再记
    ledger.note_usage("late", "kimi", 1, 1)
    assert len(f.read_text(encoding="utf-8").splitlines()) == 3


def test_scope_thread_isolation(monkeypatch, tmp_path):
    # contextvar 线程隔离: 主线程 scope 不泄漏到别的线程
    import threading
    _tmp_ep(monkeypatch, tmp_path)
    seen = {}
    with ledger.llm_scope("测试书", 1, "k2"):
        t = threading.Thread(target=lambda: seen.setdefault(
            "noted", ledger._LLM_SCOPE.get() is None))
        t.start()
        t.join()
    assert seen.get("noted") is True


def test_run_job_llm_tokens_with_tmp(monkeypatch, tmp_path):
    from app.services import anim_service
    monkeypatch.setattr(ledger.shots_mod, "ep_dir",
                        lambda bt, ep: tmp_path / f"{bt}/ep{ep}")
    job = {"id": "j-tok2", "phase": "plan", "status": "running", "book_id": "b",
           "book_title": "测试书", "ep": 1, "cancel": False, "log": [],
           "result": None, "error": None, "progress": None,
           "started_at": "", "finished_at": None}
    monkeypatch.setattr(anim_service, "_publish", lambda *a, **k: None)

    def fake_plan(j):
        # 模拟相位内两次 LLM 调用 (经 note_usage 真链路)
        ledger.note_usage("plan_scene", "kimi", 1000, 200)
        ledger.note_usage("plan_scene", "kimi", 500, 100)

    monkeypatch.setattr(anim_service, "_phase_plan", fake_plan)
    anim_service._run_job(job)
    assert job["status"] == "completed"
    lt = job["result"]["llm_tokens"]
    assert lt["calls"] == 2 and lt["ptok"] == 1500 and lt["ctok"] == 300
    assert lt["top_tags"] == {"plan_scene": 1800}
    f = tmp_path / "测试书" / "ep1" / "llm_ledger.jsonl"
    assert len(f.read_text(encoding="utf-8").splitlines()) == 2


def test_two_stacks_wiring_source_asserts():
    # 源码级断言: 两套 LLM 栈都接台账; _run_job 设入 scope + result 回报
    import inspect
    from app.services import llm_service
    from app.services import anim_service
    lu = inspect.getsource(llm_service.LLMService._log_usage)
    assert "note_usage" in lu                       # llm_service.chat/chat_turns 栈
    assert "tag" in inspect.signature(pipeline_llm.chat_json).parameters
    pl = inspect.getsource(pipeline_llm.chat_json)
    assert pl.count("_note_llm_usage(") >= 3         # primary/fallback/local 三路
    rj = inspect.getsource(anim_service._run_job)
    assert "llm_scope(" in rj and "llm_tokens" in rj
