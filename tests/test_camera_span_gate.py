# -*- coding: utf-8 -*-
"""1101 运镜断链根修: 短槽剥词闸 + writer/struct 提示注入静态断言 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import director2
from app.services.anim_pipeline.scene_writer import WRITER_SYS, writer_blocks


def _doc(rows):
    return {"shots": [
        {"shot_id": sid, "t_start": t0, "t_end": t1, "camera": cam}
        for sid, t0, t1, cam in rows
    ]}


def test_strip_short_slot_move_word():
    doc = _doc([("s1", 0.0, 4.9, "拉远")])
    assert director2.enforce_camera_span(doc) == 1
    assert doc["shots"][0]["camera"] == "固定"


def test_strip_keeps_framing_words():
    doc = _doc([("s2", 0.0, 5.2, "低角度|拉近"),
                ("s2b", 10.0, 14.0, "平视，缓慢拉近")])
    assert director2.enforce_camera_span(doc) == 2
    assert doc["shots"][0]["camera"] == "低角度"
    assert doc["shots"][1]["camera"] == "平视"


def test_long_slot_untouched():
    doc = _doc([("s3", 0.0, 7.0, "平视，拉远")])
    assert director2.enforce_camera_span(doc) == 0
    assert doc["shots"][0]["camera"] == "平视，拉远"


def test_non_move_words_untouched():
    doc = _doc([("s4", 0.0, 3.0, "缓推"), ("s5", 0.0, 3.0, "固定机位，平视中景")])
    assert director2.enforce_camera_span(doc) == 0


def test_writer_prompt_has_move_words():
    # 1101 断链根修防再断: writer 主路提示必须含六词与 ≥6s 门槛
    for w in ("左摇", "右摇", "上摇", "下摇", "拉近", "拉远"):
        assert w in WRITER_SYS, f"WRITER_SYS 缺运镜词 {w}"
        assert w in director2.DIRECTOR_SYS_STRUCT, f"STRUCT 缺运镜词 {w}"
    assert "6s" in WRITER_SYS
    assert "运镜词纪律" in director2.DIRECTOR_SYS_STRUCT
    assert "至少 1 镜" in director2.DIRECTOR_SYS  # 双向配额 (1017 白名单行)
    contract = writer_blocks(None, ["mid"])
    assert "拉远" in contract and "6s" in contract
