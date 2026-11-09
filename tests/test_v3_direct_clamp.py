# -*- coding: utf-8 -*-
"""1109 运镜槽>10s 确定性降固定 (⑤ 不亮两轮实锤):
prompt 规则早有 (direct.py L304) 但 LLM 不稳定遵守 — 教义: prompt 只是减量,
出口确定性闸才是主防线。clamp_move_slots 三调用点 (run/save/reroll) 同守。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import direct as v3d

SHOTS = [{"sid": "s1", "est_s": 12.67, "keyframe_zh": "x"},
         {"sid": "s2", "est_s": 10.89, "keyframe_zh": "x"},
         {"sid": "s3", "est_s": 8.0, "keyframe_zh": "x"},
         {"sid": "s4", "est_s": 14.0, "keyframe_zh": "x"}]


def _direct():
    return {
        "s1": {"cam": {"kind": "推", "dir": "无", "amp": "小", "speed": "慢"}},
        "s2": {"cam": {"kind": "摇", "dir": "左", "amp": "大", "speed": "慢"}},
        "s3": {"cam": {"kind": "推", "dir": "无", "amp": "小", "speed": "慢"}},
        "s4": {"cam": {"kind": "固定", "dir": "", "amp": "", "speed": ""}},
    }


def test_clamp_downgrades_over_10s_move_shots():
    d = _direct()
    fixed = v3d.clamp_move_slots(d, SHOTS)
    assert sorted(fixed) == ["s1(推→固定, 槽12.7s)", "s2(摇→固定, 槽10.9s)"]
    assert d["s1"]["cam"]["kind"] == "固定" and d["s1"]["cam"]["amp"] == ""
    assert d["s2"]["cam"]["kind"] == "固定"
    # ≤10s 运镜不动; 原本固定不动
    assert d["s3"]["cam"]["kind"] == "推"
    assert d["s4"]["cam"]["kind"] == "固定"


def test_clamped_passes_validate_no_slot_issue():
    d = _direct()
    v3d.clamp_move_slots(d, SHOTS)
    iss = v3d.validate(d, SHOTS)
    assert not any("超 h3 工位 10s" in t for lst in iss.values() for t in lst)


def test_wiring_three_call_sites():
    """源级断言: run / save 端点 / reroll 端点 三处都必须过 clamp (漂移当场崩)。"""
    src = open("app/services/anim_pipeline/v3_pipeline/direct.py", encoding="utf-8").read()
    assert "clamp_move_slots(direct, shots)" in src          # run(): LLM 产出后
    router = open("app/routers/anim_v3.py", encoding="utf-8").read()
    assert router.count("clamp_move_slots(merged") >= 2      # save + reroll


def test_h3_status_projection_single_source():
    """1109 (⑧ 全审后按钮黑实锤): project_h3_status = 批任务与 /h3 GET 单源。
    过审写 v3 k2 槽, 盖章快照 (v2 status) 滞后 — /h3 必须只读投影同规则。"""
    from app.services.anim_pipeline.v3_pipeline.h3 import project_h3_status as p
    assert p("img_done", "approved", False) == "approved"   # 本实锤场景
    assert p("planned", "img_done", False) == "img_done"
    assert p("anim_done", "approved", False) == "anim_done" # 只升不降 (1106 四修)
    assert p("approved", "approved", True) == "planned"     # 屏弃槽
    assert p("planned", "", False) == "planned"
    # 双消费端源级断言 (漂移当场崩)
    router = open("app/routers/anim_v3.py", encoding="utf-8").read()
    assert "project_h3_status(" in router
    h3src = open("app/services/anim_pipeline/v3_pipeline/h3.py", encoding="utf-8").read()
    assert h3src.count("project_h3_status(") >= 2           # def + 批任务调用
