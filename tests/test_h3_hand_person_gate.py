# -*- coding: utf-8 -*-
"""1108 批A-4: H3 手部/空镜两锁收口 — character_policy 无人物镜不注 +
identity lock 不断言 faces.

用户令 (两锁一次关): 手部镜视频 5/5 中途冒整人 (s30/s51/s52/s54/s55,
眼镜+胡子男) — H3 build_prompt 无人物判据, 无条件注入圣经 character_policy
("角色固定出场/必须有脸/胡须眼镜识别词") + identity lock "characters' faces"
断言。手=种子, 两锁=放大器; 纯空镜无种子不冒 (A类 4/4 佐证)。
zh_scene 不传 (v2 路) = 字节不动。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import h3

_BOOK = "测书-外观锚"


def _setup(monkeypatch, tmp_path):
    import json as _json
    from app.services.anim_pipeline import director2 as d2
    bb = tmp_path / "bible.json"
    bb.write_text(_json.dumps({
        "characters": [{"name": "老谭"}],
        "character_policy": "每集2-3名Q版三头身角色固定出场，卡通脸必须有五官",
    }), encoding="utf-8")
    monkeypatch.setattr(d2, "_bible_cache_path", lambda bt: bb)


_ANIM = {
    "opening_desc": "a man's hand spreads note papers on a desk under a warm lamp",
    "physical_lock": "desk and lamp stay fixed",
    "duration_s": 3.0,
    "beats": [{"t_start": 0, "t_end": 3.0, "motion": "the hand smooths the notes flat"}],
}


def test_hand_shot_drops_both_locks(monkeypatch, tmp_path):
    """手部镜 (剥词后零人物): 两锁收口, 保色锁仍在。"""
    _setup(monkeypatch, tmp_path)
    out = h3.build_prompt(dict(_ANIM), _BOOK,
                          zh_scene="一只男性的手把便签纸摊开在桌面上")
    assert "Character law" not in out          # character_policy 不注
    assert "characters' faces" not in out      # identity lock 无 faces 断言
    assert "Identity lock" in out              # 保色锁仍在 (袖口/物色)
    assert "hand spreads note papers" in out   # 画面句不丢


def test_person_shot_bytes_unchanged(monkeypatch, tmp_path):
    """人物镜 (卡名) 与不传参字节一致 — 渲染指纹不扰动。"""
    _setup(monkeypatch, tmp_path)
    a = h3.build_prompt(dict(_ANIM), _BOOK, zh_scene="老谭站在山脊上")
    b = h3.build_prompt(dict(_ANIM), _BOOK)
    assert a == b
    assert "Character law" in b                # 人物镜两锁照注
    assert "characters' faces" in b


def test_empty_scene_same_path(monkeypatch, tmp_path):
    """纯空镜: 与手部镜同路 (无人物判据)。"""
    _setup(monkeypatch, tmp_path)
    out = h3.build_prompt(dict(_ANIM), _BOOK,
                          zh_scene="特写：摊开的厚重书籍占据画面中心")
    assert "Character law" not in out
    assert "characters' faces" not in out


def test_build_workflow_forwards_zh_scene(monkeypatch, tmp_path):
    """build_workflow 透传 zh_scene (调用链 run_shot → workflow → prompt)。"""
    _setup(monkeypatch, tmp_path)
    _seen = {}
    import app.services.anim_pipeline.h3 as _h3
    _orig = _h3.build_prompt

    def _spy(anim, book_title="", page="", text_event=None, in_image_text=None,
             thesis="", zh_scene=""):
        _seen["zh"] = zh_scene
        return _orig(anim, book_title, page=page, thesis=thesis,
                     zh_scene=zh_scene)

    monkeypatch.setattr(_h3, "build_prompt", _spy)
    _h3.build_workflow("img.png", dict(_ANIM), 1, "p", book_title=_BOOK,
                       zh_scene="一只男性的手把便签纸摊开在桌面上")
    assert _seen["zh"] == "一只男性的手把便签纸摊开在桌面上"
