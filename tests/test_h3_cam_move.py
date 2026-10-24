# -*- coding: utf-8 -*-
"""B-CAM1 运镜镜 (1024): 触发词 / prompt 三补丁 / 普通镜零变化 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import h3

_ANIM = {
    "opening_desc": "eye-level wide shot, a fog sea, no text, locked-off camera.",
    "physical_lock": "the main structural elements and layout",
    "beats": [{"t_start": 0.0, "t_end": 4.0, "motion": "Fog drifts slowly."}],
    "ending": "",
    "duration_s": 4.0,
}


def _prompt(cam_move):
    wf = h3.build_workflow("x.png", _ANIM, 42, "t", book_title="卓有成效的管理者",
                           cam_move=cam_move)
    return wf["187"]["inputs"]["value"]


def test_cam_move_patches():
    p = _prompt("pan_left")
    assert h3._CAM_BAN not in p, "军规句应被替换"
    assert "pans left with large amplitude at slow speed" in p
    assert p.startswith("For the target video, at 0.00 seconds")
    assert "nothing new is added at any moment" not in p, "era 禁新内容句应被换"


def test_cam_move_default_untouched():
    p = _prompt("")
    assert h3._CAM_BAN in p, "普通镜军规句原样"
    assert not p.startswith("For the target video")
    assert "nothing new is added at any moment" in p or "Scene era lock" not in p


def test_cam_move_kind_words():
    assert h3.cam_move_kind("平视 左摇") == "pan_left"
    assert h3.cam_move_kind("缓推") == ""
    assert h3.cam_move_kind("上摇全景") == "tilt_up"
    assert h3.cam_move_kind("拉近") == "push_in"
    assert h3.cam_move_kind("") == ""
    assert h3.cam_move_kind("右摇, 俯瞰") == "pan_right"


def test_cam_strip_locked():
    out = h3._cam_strip_locked(_ANIM["opening_desc"])
    assert "locked-off" not in out
    assert out.endswith(".")
    assert h3._cam_strip_locked("plain desc").endswith(".")
