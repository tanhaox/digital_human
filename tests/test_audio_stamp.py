# -*- coding: utf-8 -*-
"""1101 U9 批2 工坊音频指纹: stamp 计算/换轨判定/接线断言 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_draft, anim_service


def _audio(job="j1", packs=(("001.wav", "第一包", 2.0), ("002.wav", "第二包", 3.0))):
    return {"job_id": job, "output_dir": "/x",
            "files": [{"file": f"/x/{n}", "text": t, "dur_s": d} for n, t, d in packs]}


def test_stamp_deterministic():
    a = anim_draft.audio_stamp({}, _audio())
    b = anim_draft.audio_stamp({}, _audio())
    assert a == b and a["packs"] == 2 and a["total_s"] == 5.0
    assert len(a["text_sha"]) == 16


def test_stamp_detects_text_change():
    base = anim_draft.audio_stamp({}, _audio())
    changed = anim_draft.audio_stamp({}, _audio(packs=(("001.wav", "第一包", 2.0),
                                                       ("002.wav", "第二包改字", 3.0))))
    assert changed != base            # 改字 → sha 变


def test_stamp_detects_duration_change():
    base = anim_draft.audio_stamp({}, _audio())
    reroll = anim_draft.audio_stamp({}, _audio(packs=(("001.wav", "第一包", 1.6),
                                                      ("002.wav", "第二包", 3.0))))
    assert reroll != base             # reroll 时长变 → total_s 变


def test_stamp_detects_job_switch():
    base = anim_draft.audio_stamp({}, _audio(job="j1"))
    other = anim_draft.audio_stamp({}, _audio(job="j2"))
    assert other != base              # 再生成换轨 → job_id 变


def test_wiring_source_asserts():
    # 源码级断言: 三个写入点 + status_view 投影 + GPU 前置警告 (防断链)
    import inspect
    ad = inspect.getsource(anim_draft)
    assert 'doc["audio_stamp"] = audio_stamp(doc, audio)' in ad       # align 落盘尾
    sv = inspect.getsource(anim_service.status_view)
    assert '"stale"' in sv and "_audio" in sv
    gpu = inspect.getsource(anim_service._phase_gpu)
    assert "音频已换轨" in gpu
    plan = inspect.getsource(anim_service._phase_plan)
    assert plan.count('audio_stamp(_doc0, audio)') + plan.count('audio_stamp(doc, audio)') >= 2
    from pathlib import Path
    tb = Path("web/anim/topbar.js").read_text(encoding="utf-8")
    assert "音频已换轨" in tb and "验收版已被重建删除" in tb
