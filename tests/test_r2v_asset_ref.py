# -*- coding: utf-8 -*-
"""B-ASSET1 素材参考镜 (1024): r2v prompt 注入 / 步数路由 / 工位路由 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import h3, r2v

_BASE = {
    "shot_id": "sT",
    "keyframe_zh": "中景, 现代办公空间, 墙上有空白画框",
    "anim": {
        "opening_desc": "medium shot, a modern office with a blank framed panel",
        "physical_lock": "the main structural elements",
        "beats": [{"t_start": 0.0, "t_end": 6.0, "motion": "Dust drifts."}],
        "ending": "",
        "duration_s": 6.0,
    },
}
_ASSET = {
    "image": "assets/sT_ref.png",
    "anchor_en": "framed panel",
    "content_en": "a time-audit table",
    "has_text": True,
}


def _prompt(shot):
    return r2v._build_prompt(shot, [], book_title="卓有成效的管理者", thesis="")


def test_asset_mapping_and_hold():
    p = _prompt({**_BASE, "asset_ref": dict(_ASSET)})
    assert "The framed panel shows exactly a time-audit table from the first reference image (Object reference, fully preserved)" in p
    assert "completely static like a printed poster" in p
    assert "Camera stays stable, only a very slight push-in" not in p
    assert "holds absolutely still on this composition" in p  # 定格持镜替换


def test_asset_ordinal_with_chars():
    p = r2v._build_prompt({**_BASE, "asset_ref": dict(_ASSET)},
                          ["laotan"], book_title="卓有成效的管理者", thesis="")
    assert "from the second reference image" in p
    assert "Each person keeps his own face" in p  # 角色行共存


def test_asset_text_policy_and_event_suppression():
    p = _prompt({**_BASE, "asset_ref": dict(_ASSET),
                 "h3_text_event": {"text": "花字", "at_s": 1}})
    assert "The only text in the video is the content of framed panel" in p
    assert "Text event:" not in p  # 事件字分支被抑制
    p2 = _prompt({**_BASE, "asset_ref": {**_ASSET, "has_text": False}})
    assert "No text, no human voice." in p2  # 无字素材维持现行为


def test_no_asset_untouched():
    p = _prompt(dict(_BASE))
    assert "Camera stays stable, only a very slight push-in. " in p
    assert "No text, no human voice." in p
    assert "reference image (Object reference" not in p


def test_steps_for():
    assert r2v._steps_for({"asset_ref": {"image": "a.png"}}) == 12
    assert r2v._steps_for({}) == 4
    assert r2v._steps_for({"asset_ref": {"anchor_en": "x"}}) == 4  # 无 image 不算


def test_workflow_steps_and_ref_order(monkeypatch):
    monkeypatch.setattr(r2v.comfy, "upload_image", lambda p: p.name)  # 挡真上传
    wf = r2v._build_workflow("p", ["char.png", "asset.png"], "scene.png",
                             1024, 576, 192, 42, steps=12)
    assert wf["361"]["inputs"]["steps"] == 12
    assert wf["140"]["inputs"]["image"] == "char.png"
    assert wf["141"]["inputs"]["image"] == "asset.png"
    assert wf["142"]["inputs"]["image"] == "scene.png"  # 场景锚恒在末位
    assert wf["133"]["inputs"]["ref_images.ref_image_2"] == ["142", 0]


def test_station_routing():
    assert h3._pick_video_station(
        {**_BASE, "asset_ref": dict(_ASSET)}) == "r2v"
    assert h3._pick_video_station(
        {**_BASE, "keyframe_zh": "一座雕像", "asset_ref": dict(_ASSET)}) == "r2v"
    assert h3._pick_video_station({**_BASE, "keyframe_zh": "一座雕像"}) == "h3"
    assert h3._pick_video_station(dict(_BASE)) == "h3"
