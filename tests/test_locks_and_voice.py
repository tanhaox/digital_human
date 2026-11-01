# -*- coding: utf-8 -*-
"""1101 批5 锁与单口 (AUDIT G1/E3/B3/F3): 概念写锁 / voice_full 单口 / 观察层投影 (零 GPU)."""
import sys
import time

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_draft, anim_service

# ── U5 概念写路径锁收口 ────────────────────────────────────────

def test_concept_writes_go_through_ep_guard(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(anim_service, "resolve_ep", lambda ref, ep: ("b1", "测试书"))
    monkeypatch.setattr(anim_service, "ep_guard",
                        lambda bid, ep, timeout=10.0: calls.append(("guard", bid, ep))
                        or _NullCtx())
    monkeypatch.setattr("app.services.anim_pipeline.director2.save_concept",
                        lambda bt, ep, payload: calls.append(("save", bt, ep)) or {"touched": 1})
    monkeypatch.setattr("app.services.anim_pipeline.director2.reroll_concept",
                        lambda bid, bt, ep, arc, d="": calls.append(("reroll", arc)) or {})
    assert anim_service.save_concept_locked("b1", 3, {"scenes": []}) == {"touched": 1}
    anim_service.reroll_concept_locked("b1", 3, "A2", "换个方向")
    assert ("guard", "b1", 3) in calls and ("save", "测试书", 3) in calls
    assert ("reroll", "A2") in calls


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── U4 voice_full 单口 ────────────────────────────────────────

class _Probe:
    def __init__(self, stdout=""):
        self.stdout = stdout


def _mk_pack(tmp_path, name, dur, text="句", older=False):
    p = tmp_path / name
    p.write_bytes(b"fake")
    t = time.time() - (3600 if older else 0)
    import os
    os.utime(p, (t, t))
    return {"file": str(p), "text": text, "dur_s": dur}


def test_voice_full_rebuild_when_missing(monkeypatch, tmp_path):
    built = []
    monkeypatch.setattr(anim_draft, "concat_voice",
                        lambda files, out: built.append(out) or out)
    monkeypatch.setattr(anim_draft, "fetch_episode_audio",
                        lambda bid, ep: {"files": [_mk_pack(tmp_path, "a.wav", 5.0)]})
    monkeypatch.setattr(anim_draft, "shots_mod",
                        type("M", (), {"ep_dir": staticmethod(lambda bt, ep: tmp_path)}))
    v = anim_draft.ensure_voice_full("b", 1, "书")
    assert built and v == built[0]


def test_voice_full_fresh_no_rebuild(monkeypatch, tmp_path):
    built = []
    voice = tmp_path / "voice_full.wav"
    voice.write_bytes(b"fake")
    packs = [_mk_pack(tmp_path, "a.wav", 5.0, older=True)]  # 包比轨旧
    monkeypatch.setattr(anim_draft, "concat_voice",
                        lambda files, out: built.append(out) or out)
    monkeypatch.setattr(anim_draft, "fetch_episode_audio", lambda bid, ep: {"files": packs})
    monkeypatch.setattr(anim_draft, "shots_mod",
                        type("M", (), {"ep_dir": staticmethod(lambda bt, ep: tmp_path)}))
    # ffprobe 假回 5.0s = 预期 5.0s → 不重建
    monkeypatch.setattr(anim_draft.subprocess, "run", lambda *a, **k: _Probe("5.0"))
    anim_draft.ensure_voice_full("b", 1, "书", packs)
    assert not built


def test_voice_full_rebuild_on_duration_drift(monkeypatch, tmp_path):
    built = []
    voice = tmp_path / "voice_full.wav"
    voice.write_bytes(b"fake")
    packs = [_mk_pack(tmp_path, "a.wav", 6.0, older=True), _mk_pack(tmp_path, "b.wav", 6.5, older=True)]
    monkeypatch.setattr(anim_draft, "concat_voice",
                        lambda files, out: built.append(out) or out)
    monkeypatch.setattr(anim_draft, "shots_mod",
                        type("M", (), {"ep_dir": staticmethod(lambda bt, ep: tmp_path)}))
    monkeypatch.setattr(anim_draft.subprocess, "run", lambda *a, **k: _Probe("9.0"))  # 漂 3.5s
    anim_draft.ensure_voice_full("b", 1, "书", packs)
    assert built  # 时长差>2s → 重建


def test_voice_full_no_packs_keeps_existing(monkeypatch, tmp_path):
    voice = tmp_path / "voice_full.wav"
    voice.write_bytes(b"fake")
    monkeypatch.setattr(anim_draft, "shots_mod",
                        type("M", (), {"ep_dir": staticmethod(lambda bt, ep: tmp_path)}))
    v = anim_draft.ensure_voice_full("b", 1, "书", files=[])
    assert v == voice


# ── B3/F3 观察层投影与留痕 (源码级断言) ───────────────────────

def test_projection_and_warnings_wired():
    import inspect
    src = inspect.getsource(anim_service.status_view)
    assert '"prompt_gate": s.get("prompt_gate")' in src
    assert '"video_station": s.get("video_station")' in src
    from app.services.anim_pipeline import planner
    psrc = inspect.getsource(planner)
    assert "_huazi_warnings" in psrc and "_hz_warn" in psrc
    from app.services.anim_pipeline import huazi_pass
    assert "_huazi_warnings" in inspect.getsource(huazi_pass)
