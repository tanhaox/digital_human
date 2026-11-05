# -*- coding: utf-8 -*-
"""1105 批2 同集互斥闸 + 锁纪律 (病灶2+11): 409 闸/GPU 锁回放/超时锁/GPU 段边界 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_service


def _fake_job(phase="plan", book="bid", ep=1):
    return {"id": "j-00000001-" + phase, "phase": phase, "status": "running",
            "book_id": book, "ep": ep}


def _patch_entry(monkeypatch, book="bid"):
    monkeypatch.setattr(anim_service, "resolve_ep", lambda ref, ep: (book, "测试书"))
    from app.services.anim_pipeline import config as anim_cfg
    monkeypatch.setattr(anim_cfg, "style_binding_missing", lambda bt: False)


def test_same_ep_gate_409(monkeypatch):
    _patch_entry(monkeypatch)
    with anim_service._JOBS_LOCK:
        anim_service._JOBS["j-gate1"] = _fake_job()
    try:
        try:
            anim_service.start_phase("bid", 1, "plan")
            assert False, "同集双开应 409"
        except anim_service.AnimBusyError as exc:
            assert "本集已有任务进行中 (plan" in str(exc)
    finally:
        with anim_service._JOBS_LOCK:
            anim_service._JOBS.pop("j-gate1", None)
    # 被拦下不得留半成品注册 (闸与注册同临界区)
    assert not [j for j in anim_service._JOBS.values() if j.get("book_id") == "bid"]


def test_gpu_lock_released_when_same_ep_gate_rejects(monkeypatch):
    # GPU 相位被同集闸拦下 → 已预取的 GPU 锁必须放回 (否则全局 GPU 死锁)
    _patch_entry(monkeypatch, book="bid2")
    with anim_service._JOBS_LOCK:
        anim_service._JOBS["j-gate2"] = _fake_job(phase="k2", book="bid2")
    try:
        try:
            anim_service.start_phase("bid2", 1, "h3")
            assert False, "同集双开应 409"
        except anim_service.AnimBusyError:
            pass
        assert anim_service._GPU_JOB_LOCK.acquire(blocking=False)   # 锁已放回
        anim_service._GPU_JOB_LOCK.release()
    finally:
        with anim_service._JOBS_LOCK:
            anim_service._JOBS.pop("j-gate2", None)


def test_ep_lock_timed_raises_on_busy():
    import threading
    lock = anim_service._ep_lock("bidX", 9)
    acquired = threading.Event()
    let_go = threading.Event()
    released = threading.Event()

    def holder_fn():   # 持锁/放锁都在同一线程 (RLock 只能持有者放)
        lock.acquire()
        acquired.set()
        let_go.wait(2.0)
        lock.release()
        released.set()

    t = threading.Thread(target=holder_fn)
    t.start()
    assert acquired.wait(1.0)
    try:
        try:
            with anim_service._ep_lock_timed("bidX", 9, wait=0.05):
                pass
            assert False, "锁忙应 raise 不再静默挂死"
        except RuntimeError as exc:
            assert "写锁被占" in str(exc)
    finally:
        let_go.set()
        assert released.wait(1.0)
        t.join(1.0)
    # 空闲时正常进出 (同线程重入也照常)
    with anim_service._ep_lock_timed("bidX", 9, wait=0.05):
        pass


def test_wait_gpu_lock_cancel_and_idle():
    anim_service._GPU_JOB_LOCK.acquire()
    try:
        job = {"cancel": True, "id": "j-wait", "progress": None}
        assert anim_service._wait_gpu_lock(job, 3) is False   # ⛔停止可中断等待
    finally:
        anim_service._GPU_JOB_LOCK.release()
    assert anim_service._wait_gpu_lock({"cancel": False, "id": "j-idle"}, 1) is True


def test_lock_wiring_source_asserts():
    import inspect
    # redo 移出 GPU 预取群 (LLM 段不再占 GPU 锁); k2/h3 等仍在
    assert "redo" not in anim_service.GPU_PHASES
    assert {"k2", "h3", "brand", "facefix", "textpass"} <= set(anim_service.GPU_PHASES)
    sp = inspect.getsource(anim_service.start_phase)
    assert "本集已有任务进行中" in sp
    rd = inspect.getsource(anim_service._phase_redo)
    assert "_wait_gpu_lock" in rd and "_GPU_JOB_LOCK.release()" in rd
    assert "gpu_skipped" in rd          # 等待中被停不算失败, 指路手动 K2
    rj = inspect.getsource(anim_service._run_job)
    assert "GPU_PHASES" in rj           # finally 放锁路径仍在 (k2/h3 族)
    pp = inspect.getsource(anim_service._phase_plan)
    assert "_ep_lock_timed" in pp       # plan/replan 超时锁接线
    rp = inspect.getsource(anim_service._phase_replan)
    assert "_ep_lock_timed" in rp
