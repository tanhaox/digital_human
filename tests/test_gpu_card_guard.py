# -*- coding: utf-8 -*-
"""卡级预检 (card_guard) — 裁决矩阵: 自家他族停/本服务跳/后端告警/外来告警不杀."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.gpu_service_manager._card_guard import card_preflight

UUID = "GPU-aaa"
PINNED = SimpleNamespace(env={"CUDA_VISIBLE_DEVICES": UUID})
UNPINNED = SimpleNamespace(env={})


def _apps(monkeypatch, rows):
    monkeypatch.setattr(
        "app.services.gpu_service_manager._card_guard._compute_apps",
        lambda: [(u, pid, name) for u, pid, name in rows])


def _used(monkeypatch, mb):
    monkeypatch.setattr(
        "app.services.gpu_service_manager._card_guard._used_mb",
        lambda uuid: mb if uuid == UUID else 0)


def test_unpinned_spec_noop(monkeypatch):
    called = []
    monkeypatch.setattr(
        "app.services.gpu_service_manager._card_guard._compute_apps",
        lambda: called.append(1) or [])
    assert card_preflight("indextts", UNPINNED, tracked_pids={},
                          stop_service=lambda k: None, notify=lambda m: None)
    assert not called  # 未钉卡不查 nvidia


def test_own_service_pid_skipped(monkeypatch):
    _apps(monkeypatch, [(UUID, 7220, "comfy")])
    _used(monkeypatch, 0)
    stops = []
    ok = card_preflight("comfyui", PINNED, tracked_pids={7220: "comfyui"},
                        stop_service=stops.append, notify=lambda m: None)
    assert ok and not stops  # 本服务在岗 → 跳过


def test_other_family_stopped(monkeypatch):
    _apps(monkeypatch, [(UUID, 999, "tts"),
                        ("GPU-bbb", 1, "别卡进程")])
    _used(monkeypatch, 500)
    stops, notes = [], []
    ok = card_preflight("comfyui", PINNED, tracked_pids={999: "indextts25"},
                        stop_service=stops.append, notify=notes.append)
    assert ok and stops == ["indextts25"]
    assert any("让位" in n for n in notes)


def test_backend_pid_warn_only(monkeypatch):
    import app.services.gpu_service_manager._card_guard as g
    _apps(monkeypatch, [(UUID, g.os.getpid(), "python")])
    _used(monkeypatch, 500)
    kills = []
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: kills.append(a) or None)
    ok = card_preflight("comfyui", PINNED, tracked_pids={},
                        stop_service=lambda k: None,
                        notify=lambda m: None)
    assert ok and not kills  # 不自杀


def test_foreign_process_warn_no_kill(monkeypatch):
    _apps(monkeypatch, [(UUID, 4242, "somebody.exe")])
    _used(monkeypatch, 6000)
    stops, notes = [], []
    ok = card_preflight("comfyui", PINNED, tracked_pids={},
                        stop_service=stops.append, notify=notes.append)
    assert not ok and not stops
    assert any("外来" in n for n in notes)  # 告警未杀 (fail-open)


def test_clean_card_returns_true(monkeypatch):
    _apps(monkeypatch, [])
    assert card_preflight("comfyui", PINNED, tracked_pids={},
                          stop_service=lambda k: None, notify=lambda m: None)
