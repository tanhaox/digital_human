# -*- coding: utf-8 -*-
"""1101 U1 军规单一真源: 词表/条款在四人格与执法点间一致, 防再断链 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import director2, h3, law_blocks
from app.services.anim_pipeline.scene_writer import WRITER_SYS, writer_blocks

_PERSONAS = {
    "DIRECTOR_SYS": director2.DIRECTOR_SYS,
    "DIRECTOR_SYS_STRUCT": director2.DIRECTOR_SYS_STRUCT,
    "WRITER_SYS": WRITER_SYS,
}


def test_no_leftover_placeholders():
    # 占位符必须全部被 law_blocks 注入替换 — 漏注入=军规从人格消失 (1101 断链形态)
    for name, text in _PERSONAS.items():
        assert "@@" not in text, f"{name} 残留未注入占位符"
        assert "@@" not in director2.DIRECTOR_SYS_STRUCT


def test_camera_law_in_all_personas():
    bullet = law_blocks.CAMERA_LAW_BULLET
    for name, text in _PERSONAS.items():
        assert bullet in text, f"{name} 缺军规条款 (law_blocks 未注入)"
    contract = writer_blocks(None, ["mid"])
    assert all(w in contract for w in law_blocks.MOVE_WORDS)
    assert "6s" in contract


def test_move_words_single_source():
    # h3 渲染词表 / 剥词闸 / law_blocks 三处同源
    assert {w for w, _k in h3._CAM_MOVE_KINDS} == set(law_blocks.MOVE_WORDS)
    assert law_blocks.cam_move_min_span() == 5.95  # 宪法 6.0 - 0.05 容差
    for w in law_blocks.MOVE_WORDS:
        assert w in law_blocks.CAMERA_ENUM
    assert "@@" not in law_blocks.CAMERA_ENUM


def test_enhance_ban_words_from_law():
    # A3: 增强/确定性闸禁词表引 law_blocks — 模块内不得残留内联副本 (词面漂移不可能)
    import inspect
    for mod in (director2,):
        src = inspect.getsource(mod)
        assert '("横移", "跟拍"' not in src, f"{mod.__name__} 残留内联禁词副本"
        assert "law_blocks.MOTION_BAN_ZH" in src and "law_blocks.MOTION_BAN_EN_RE" in src
    assert set(law_blocks.MOTION_BAN_ZH) == {"横移", "跟拍", "摇镜", "平移"}
