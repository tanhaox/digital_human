# -*- coding: utf-8 -*-
"""首帧去 hf 卡回归 (2026-09-04 用户裁决: Slot#0 恒 hf 线太吃亏, 移除强制).

覆盖四层:
1. _rules.py: _force_opening_card 已删 — C线禁用时 slot0 不再被改成 hf_opening
2. _rules.py: C线禁用降级链 — slot0 host 违规时降级优先 broll_pexels (不落 hf_title 静态卡)
3. _strip_host.py: 无出镜版规则表 — 首帧 broll 指引在位, 无 hf_opening 残留
4. _prompt.py 管线限制块: C禁用替代指引 — 开场指 broll_pexels, 不再指 hf_title
"""
from __future__ import annotations

from app.schemas.director import DirectorSlotPlan
from app.services.director_parser._rules import enforce_host_rules
from app.services.director_prompt._constants import _NO_HOST_DEMO
from app.services.director_prompt._prompt import _build_pipeline_constraint_block
from app.services.director_prompt._strip_host import _RULE2_TABLE, strip_host_mode


def _slot(i: int, wf: str, start: float = 0.0, end: float = 6.0) -> DirectorSlotPlan:
    return DirectorSlotPlan(
        slot_index=i, start_sec=start, end_sec=end,
        visual_type=wf, workflow=wf, params={},
    )


class TestNoForcedOpeningCard:
    def test_c_disabled_first_slot_broll_untouched(self):
        # C线关 + 首帧已是 broll → 不再被强制改成 hf_opening
        slots = [_slot(0, "broll_pexels"), _slot(1, "hf_chart", 6, 12), _slot(2, "broll_pexels", 12, 18)]
        out = enforce_host_rules(slots, 18.0, enabled_pipelines={"p", "h"})
        assert out[0].workflow == "broll_pexels"
        assert "forced_opening_card" not in out[0].params

    def test_c_disabled_no_hf_opening_anywhere(self):
        # 无人出镜全片禁 host: 不产生任何 hf_opening slot
        slots = [
            _slot(0, "broll_pexels"),
            _slot(1, "broll_pexels", 6, 12),
            _slot(2, "hf_chart", 12, 18),
            _slot(3, "broll_local", 18, 24),
        ]
        out = enforce_host_rules(slots, 24.0, enabled_pipelines={"p", "h"})
        assert all(s.workflow != "hf_opening" for s in out)

    def test_host_violation_slot0_downgrades_to_broll_not_hf(self):
        # LLM 违规仍输出 host 开场: 降级链 slot0 优先实拍, 不落 hf_title 静态卡
        slots = [
            _slot(0, "host"),
            _slot(1, "broll_pexels", 6, 12),
            _slot(2, "hf_chart", 12, 18),
        ]
        out = enforce_host_rules(slots, 18.0, enabled_pipelines={"p", "h"})
        assert out[0].workflow == "broll_pexels"
        assert out[0].params.get("fallback_reason") == "c_pipeline_disabled"

    def test_host_violation_mid_slot_still_hf_chain(self):
        # 非 slot0 的 host 降级保持原链 (host→hf_title), 只首帧特殊
        slots = [
            _slot(0, "broll_pexels"),
            _slot(1, "host", 6, 12),
            _slot(2, "broll_pexels", 12, 18),
        ]
        out = enforce_host_rules(slots, 18.0, enabled_pipelines={"p", "h"})
        assert out[1].workflow == "hf_title"


class TestPromptNoHfOpening:
    def test_no_host_demo_first_slot_is_broll(self):
        # 无出镜示例 JSON: 首 slot = broll_pexels (LLM few-shot 跟随)
        assert '"slot_index": 0' in _NO_HOST_DEMO
        first = _NO_HOST_DEMO.split('"slot_index": 1')[0]
        assert '"workflow": "broll_pexels"' in first
        assert "hf_opening" not in _NO_HOST_DEMO

    def test_rule2_table_opening_row_broll_first(self):
        assert "禁 hf 字幕卡/空镜开场" in _RULE2_TABLE
        assert "hf_opening" not in _RULE2_TABLE

    def test_strip_host_mode_no_hf_opening(self):
        stripped = strip_host_mode("## 规则1:画面类型判断(visual_type)\n\n| 条件 |\n\n## 规则2：节奏控制（硬性约束）\n\n| 规则 |\n\n## 规则2.5：机位分配\n\nx\n\n## 规则3:信息密度匹配\n\n| 信息层级 |\n\n## 规则4:素材选择策略\n\n## 规则5:全局画面一致性\n\n")
        assert "hf_opening" not in stripped

    def test_pipeline_constraint_block_points_to_broll(self):
        # C禁用 + P/H 开: 开场指引 = broll_pexels, 不再是 "开场/收尾用 hf_title 替代"
        block = _build_pipeline_constraint_block({"p", "h"})
        assert "broll_pexels" in block
        assert "开场/收尾用 hf_title 替代" not in block
