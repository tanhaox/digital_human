# -*- coding: utf-8 -*-
"""1101 U3 批3 状态迁移表 + U2 媒体失效单写者 + U7 beats 不变量 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import shots as shots_mod


def _shot(sid="s1", **kw):
    s = {"shot_id": sid, "status": "planned", "t_start": 0.0, "t_end": 5.0,
         "image_file": None, "video_file": None, "attempts": {"k2": 0, "h3": 0}}
    s.update(kw)
    return s


def test_transition_table_legal_paths():
    for old, new in [("planned", "img_done"), ("img_done", "approved"),
                     ("img_done", "planned"), ("approved", "anim_done"),
                     ("approved", "anim_fail"), ("approved", "planned"),
                     ("anim_done", "approved"), ("anim_done", "planned"),
                     ("anim_fail", "approved"), ("anim_fail", "anim_done"),
                     ("anim_fail", "planned")]:
        s = _shot(status=old)
        shots_mod.transition(s, new)
        assert s["status"] == new


def test_transition_illegal_warns_but_applies(caplog):
    s = _shot(status="planned")
    with caplog.at_level("WARNING", logger="app.services.anim_pipeline.shots"):
        shots_mod.transition(s, "approved")  # planned→approved 无此路 (未过图审批)
    assert s["status"] == "approved"          # warn-first: 放行不炸
    assert any("非法迁移" in r.message for r in caplog.records)


def test_invalidate_media_full_clear():
    s = _shot(status="img_done", image_file="img/s1.png", video_file="anim/s1.mp4",
              text_passed=True, face_fixed=True, prompt_gate=["x"], gen_narr_sha="ab12")
    cleared = shots_mod.invalidate_media(s, reset_attempts=("k2", "h3"), reason="测试")
    assert set(cleared) >= {"image", "video", "text_passed", "face_fixed",
                            "prompt_gate", "gen_narr_sha"}
    assert s["status"] == "planned" and s["attempts"] == {"k2": 0, "h3": 0}
    # 无媒体 = 键缺席 (与旧 pop 语义一致, 全库 .get() 读法兼容)
    assert "image_file" not in s and "video_file" not in s


def test_invalidate_media_keep_image_keeps_flags():
    # 图保留 (视频侧失效): 压字/修脸旗标随图有效, 不清
    s = _shot(status="anim_done", image_file="img/s1.png", video_file="anim/s1.mp4",
              text_passed=True, face_fixed=True, gen_narr_sha="ab12")
    cleared = shots_mod.invalidate_media(s, keep_image=True, to_status="approved")
    assert "video" in cleared and "gen_narr_sha" in cleared
    assert "text_passed" not in cleared and "face_fixed" not in cleared
    assert s["image_file"] == "img/s1.png" and s["status"] == "approved"


def test_reject_for_reroll_clears_media_and_flags():
    # B2 根治: 打回换seed重出净图 — 旧版残留 image_file+text_passed=压字静默丢失
    s = _shot(status="img_done", image_file="img/s1.png", video_file="anim/s1.mp4",
              text_passed=True, face_fixed=True)
    shots_mod.reject_for_reroll(s, note="构图克隆")
    assert s["status"] == "planned"
    assert "image_file" not in s and "text_passed" not in s and "face_fixed" not in s
    assert s["reject_note"] == "构图克隆" and s["attempts"]["k2"] == 1


def _anim(dur, beats=None):
    return {"duration_s": dur, "opening_desc": "d",
            "beats": beats or [{"t_start": 0, "t_end": dur, "motion": "m"}]}


def test_heal_beats_span_strips_real_drift():
    s = _shot(status="anim_done", t_end=8.0, video_file="anim/s1.mp4",
              gen_narr_sha="x", anim=_anim(5.0))
    assert shots_mod.heal_beats_span(s) == "stripped"
    assert "video_file" not in s and s["status"] == "approved" and "gen_narr_sha" not in s
    assert s["anim"]["duration_s"] == 8.0  # beats 已拉到槽


def test_heal_beats_span_exemptions():
    # 品牌镜: 不碰视频
    b = _shot(status="anim_done", t_end=8.0, brand_card=True, video_file="v.mp4",
              anim=_anim(5.0))
    assert shots_mod.heal_beats_span(b) == "healed" and b["video_file"] == "v.mp4"
    # beats 已在 H3 上限 (10s): 溢出=定格吸收, 不清
    c = _shot(status="anim_done", t_end=12.0, video_file="v.mp4", anim=_anim(10.0))
    assert shots_mod.heal_beats_span(c) == "healed" and c["video_file"] == "v.mp4"
    # 静音垫豁免: 槽长出 ≤ 0.6+pad
    d = _shot(status="anim_done", t_end=6.0, video_file="v.mp4",
              _silence_pad_s=1.0, anim=_anim(5.0))
    assert shots_mod.heal_beats_span(d) == "healed" and d["video_file"] == "v.mp4"
