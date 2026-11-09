# -*- coding: utf-8 -*-
"""1109 装配头部 5.5s 丢失根修: _axis_aligned 收 v3 盖章戳。

ep7 实锤 — 全新 v3 集无 v2 残留标记 (planner_flow=None / 无 t_start_orig)
→ aligned=False → 开场链 (打字卡/书封墙/主书封) 整段跳过, 成片无头部。
ep5 当时能过是靠 planner_flow='director2…' 残留标记 (v2 时代产物)。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_draft import _axis_aligned


def test_v1_realigned_marker_passes():
    assert _axis_aligned({"planner_flow": None}, [{"t_start_orig": 5.5}]) is True


def test_director2_native_axis_passes():
    # ep5 1107-1108 实弹走的路 (残留标记)
    assert _axis_aligned({"planner_flow": "director2 模块总线 (场=六拍模块, 系统定界)"},
                         [{}]) is True


def test_v3_stamp_axis_passes():
    # 1109 修: v3 盖章轴 (0 起, 开场链靠 t_shift 让位) 也算对齐
    assert _axis_aligned({"planner_flow": None, "v3": {"stamp": True}}, [{}]) is True


def test_fresh_unaligned_stays_false():
    # v2 未对齐规划轴: 无标记不硬加头部 (标题卡只随对齐出现 — 旧语义保留)
    assert _axis_aligned({"planner_flow": None}, [{"t_start": 0.0}]) is False


def test_wiring_build_draft_consumes_helper():
    """源级断言: build_draft 消费 _axis_aligned (漂移当场崩)。"""
    src = open("app/services/anim_draft.py", encoding="utf-8").read()
    assert "aligned = _axis_aligned(doc, shots)" in src
    # v3 判据本体在 helper 内 (不在调用点散拼)
    assert 'or bool((doc.get("v3") or {}).get("stamp")))' in src
