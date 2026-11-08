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
             thesis="", zh_scene="", cast_names=()):
        _seen["zh"] = zh_scene
        _seen["cast"] = cast_names
        return _orig(anim, book_title, page=page, thesis=thesis,
                     zh_scene=zh_scene, cast_names=cast_names)

    monkeypatch.setattr(_h3, "build_prompt", _spy)
    _h3.build_workflow("img.png", dict(_ANIM), 1, "p", book_title=_BOOK,
                       zh_scene="一只男性的手把便签纸摊开在桌面上",
                       cast_names=("背影", "老谭"))
    assert _seen["zh"] == "一只男性的手把便签纸摊开在桌面上"
    assert _seen["cast"] == ("背影", "老谭")   # 1109 令53: extras 同链透传


# ── 1108 批A-5: 召唤词不上桌 — policy 识别句剥离 + 无人物镜种子词剥离 ──

def _bible_with(monkeypatch, tmp_path, policy):
    import json as _json
    from app.services.anim_pipeline import director2 as d2
    bb = tmp_path / "bible.json"
    bb.write_text(_json.dumps({
        "characters": [{"name": "老谭"}],
        "character_policy": policy,
    }), encoding="utf-8")
    monkeypatch.setattr(d2, "_bible_cache_path", lambda bt: bb)


def test_policy_summon_stripped(monkeypatch, tmp_path):
    """① 胡子族根修: 「名人靠…胡须、眼镜…识别」句不上桌, 其余条款保留。"""
    _bible_with(monkeypatch, tmp_path, (
        "每集2-3名Q版三头身角色固定出场，卡通脸必须有五官。"
        "名人（如书中真实人物）靠标志性发型、胡须、眼镜、着装剪裁等特征识别，不做写实肖像。"
        "男性角色一律下巴光洁无胡须"))
    out = h3.build_prompt(dict(_ANIM), _BOOK)  # 不传 zh_scene = 人物镜旧路, policy 照注
    assert "Character law" in out
    assert "标志性发型" not in out and "不做写实肖像" not in out   # 识别召唤句整句剥
    assert "卡通脸必须有五官" in out                                 # 非召唤条款保留
    # 1210 串旗机制: 否定句的具象词同样被当正向提示 — 面部毛词句任何框架都剥
    # (视频层外观唯一事实源 = Picture 1/参考图 + identity lock)
    assert "一律下巴光洁无胡须" not in out
    assert "胡" not in out.split("Character law")[1].split("\n")[0]


def test_hand_seed_stripped_only_when_no_person(monkeypatch, tmp_path):
    """② 无人物镜 EN 种子词剥离: man's hand → the hand; v2 路字节不动。"""
    _setup(monkeypatch, tmp_path)
    out = h3.build_prompt(dict(_ANIM), _BOOK,
                          zh_scene="一只男性的手把便签纸摊开在桌面上")
    assert "man's hand" not in out
    assert "the hand spreads note papers" in out
    # v2 路 (不传 zh_scene): 种子词保留 = 字节不动
    out_v2 = h3.build_prompt(dict(_ANIM), _BOOK)
    assert "a man's hand spreads note papers" in out_v2


def test_sys_sleeve_anchor_rule():
    """③ 断手防复发: 画面句契约含袖口锚规则 (s52/s55 K2 首帧断手实锤)。"""
    from app.services.anim_pipeline.v3_pipeline import shots as shots_mod
    assert "袖口或前臂" in shots_mod._SYS
    # ♻️ 列队重做同尺 (1108 用户问机制一致性实锤: _REDO_SYS 旧版只到规则8,
    # 手部镜经列队重做会重出孤手/外观漂移)
    from app.services.anim_pipeline.v3_pipeline import redo as redo_mod
    assert "袖口或前臂" in redo_mod._REDO_SYS
    assert "角色外观继承" in redo_mod._REDO_SYS


def test_write_hit_scans_opening_desc(monkeypatch, tmp_path):
    """批A-5b: 书写反律扫描面含 opening_desc — s52 实锤 (笔在画面句不在 beats,
    反律没开火, 视频照样在纸上写字)。"""
    _setup(monkeypatch, tmp_path)
    anim = {
        "opening_desc": "a desk at night, a capped pen lying flat beside a blank notebook",
        "physical_lock": "desk stays fixed",
        "duration_s": 3.0,
        "beats": [{"t_start": 0, "t_end": 3.0,
                   "motion": "the sleeved forearm rests still on the desk edge"}],
    }
    out = h3.build_prompt(dict(anim), _BOOK)
    assert "THE WRITING NEVER HAPPENS" in out   # opening 含 pen → 反律开火
    # beats 无笔词、opening 也无 → 不开火 (零书写语义镜不注, 字节最小扰动)
    anim2 = dict(anim, opening_desc="a desk at night, a blank notebook and a cup of tea")
    out2 = h3.build_prompt(anim2, _BOOK)
    assert "THE WRITING NEVER HAPPENS" not in out2
