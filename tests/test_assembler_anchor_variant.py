# -*- coding: utf-8 -*-
"""1019 锚变奏测试 — 概念域 fallback 同锚多镜的确定性变奏 (A6 13 镜同锚实锤)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.anim_pipeline.assembler import (
    _anchor_focus, _scene_seq_map, assemble_k2_prompt,
)

ANCHOR = "老谭站在山径岔口，一条被砍断，一条通向有门的光顶"


def _shot(seq: int, register: str = "metaphor") -> dict:
    return {"shot_id": f"s{50+seq}", "arc_id": "A6", "register": register,
            "creative_slots": {"scene_detail": "细节句", "action": "动作句",
                               "atmosphere": "安静"}}


_FA_SHOT = {"size": "MS", "angle": "eye_level", "blocking": {"micro_position": "岔口全貌"}}
_FA_SCENE = {"location": "", "lighting": {"key_direction": "侧光45度", "temp": "4600K",
                                          "fill_ratio": "2:1"}}


def test_first_shot_keeps_pure_anchor():
    """首镜立锚: 无变奏语无聚焦 — 场空间基准镜."""
    p = assemble_k2_prompt(_shot(0), _FA_SHOT, _FA_SCENE, {}, world_phrase=ANCHOR, scene_seq=0)
    assert p.startswith("中景，平视")
    assert "本镜聚焦" not in p and "越肩" not in p


def test_variant_differs_per_seq():
    ps = [assemble_k2_prompt(_shot(i), _FA_SHOT, _FA_SCENE, {}, world_phrase=ANCHOR,
                             scene_seq=i) for i in range(1, 5)]
    assert len(set(ps)) == 4  # 每镜不同
    for p in ps:
        assert ANCHOR in p  # 锚全量保留 (场连续性不破坏)


def test_anchor_focus_rotates_clauses():
    assert _anchor_focus(ANCHOR, 1) == "老谭站在山径岔口"
    assert _anchor_focus(ANCHOR, 2) == "一条被砍断"
    assert _anchor_focus(ANCHOR, 3) == "一条通向有门的光顶"
    assert _anchor_focus(ANCHOR, 4) == "老谭站在山径岔口"  # 轮回
    assert _anchor_focus("单句锚无逗号", 1) == ""


def test_seq_map_ranks_by_t_start():
    doc = {"shots": [
        {"shot_id": "b", "arc_id": "A1", "t_start": 30.0},
        {"shot_id": "a", "arc_id": "A1", "t_start": 10.0},
        {"shot_id": "c", "arc_id": "A1", "t_start": 50.0},
        {"shot_id": "x", "arc_id": "A2", "t_start": 5.0},
    ]}
    m = _scene_seq_map(doc)
    assert m == {"a": 0, "b": 1, "c": 2, "x": 0}


def test_deterministic_same_input_same_output():
    p1 = assemble_k2_prompt(_shot(7), _FA_SHOT, _FA_SCENE, {}, world_phrase=ANCHOR, scene_seq=7)
    p2 = assemble_k2_prompt(_shot(7), _FA_SHOT, _FA_SCENE, {}, world_phrase=ANCHOR, scene_seq=7)
    assert p1 == p2


def test_real_scene_register_untouched():
    """实景域 (case/无寄存器) 不加变奏 — 有 position/action 天然变奏, 守范围."""
    p = assemble_k2_prompt(_shot(3, register=""), _FA_SHOT, _FA_SCENE, {},
                           world_phrase=ANCHOR, scene_seq=3)
    assert "本镜聚焦" not in p


def test_contrast_keeps_split_composition():
    p = assemble_k2_prompt(_shot(2, register="contrast"), _FA_SHOT, _FA_SCENE, {},
                           world_phrase=ANCHOR, scene_seq=2)
    assert "左右同屏对比构图" in p and "本镜聚焦" in p


# ── 1019 B 特写族裁锚: CU/ECU 聚焦句升主体, 全锚降级尾注 ──

def _fa(size: str) -> dict:
    return {"size": size, "angle": "eye_level", "blocking": {"micro_position": "岔口全貌"}}


def test_cu_shot_subject_is_focus_clause():
    p = assemble_k2_prompt(_shot(2), _fa("CU"), _FA_SCENE, {}, world_phrase=ANCHOR, scene_seq=2)
    assert "画面主体: 一条被砍断" in p
    assert "本镜聚焦" not in p          # 特写族不走聚焦句形态
    assert "（场景: 老谭站在山径岔口）" in p  # 锚降级为首句尾注
    assert f"{ANCHOR}·" not in p         # 全锚不再作主体前置


def test_ecu_shot_same_treatment():
    p = assemble_k2_prompt(_shot(3), _fa("ECU"), _FA_SCENE, {}, world_phrase=ANCHOR, scene_seq=3)
    assert "画面主体: 一条通向有门的光顶" in p


def test_medium_shot_keeps_full_anchor():
    """MS/MCU 不在特写族 — 保持全锚+聚焦 (中景本就该有环境)."""
    p = assemble_k2_prompt(_shot(2), _fa("MCU"), _FA_SCENE, {}, world_phrase=ANCHOR, scene_seq=2)
    assert ANCHOR in p and "本镜聚焦" in p and "画面主体" not in p


def test_cu_contrast_keeps_split():
    p = assemble_k2_prompt(_shot(2, register="contrast"), _fa("CU"), _FA_SCENE, {},
                           world_phrase=ANCHOR, scene_seq=2)
    assert "左右同屏对比构图" in p and "画面主体" in p
