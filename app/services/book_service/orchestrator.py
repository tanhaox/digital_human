# -*- coding: utf-8 -*-
"""拆书创作编排 (2026-08-19) — 5 步 pipeline + 级联重跑 + 自检 + 硬校验.

方案: docs/design/拆书项目-实施方案.md §3/§3.1。
5 步: 补全(flash)【确认1】→ 评论层∥素材(flash,轻确认) → 总纲(pro)【确认2】→ 逐集(pro)【确认3】。
防幻觉: 核心四字段仅 L0/人工; L2 须佐证; 金句无逐字出处不挂引号; 总纲追溯自检。

模块布局 (2026-09-01 自单文件 993 行拆包, 函数体原样搬运零行为变更;
routers/books.py `orchestrator as orch` 的属性引用不受影响, 本文件为转发层):
  creation_common.py  常量/灵性金句锚定/六段弹性标签/LLM 工具
  creation_steps.py   Gate0 预评估 + 步骤1-4 (补全/评论层/素材/总纲/L0 自动填充)
  episode_gen.py      步骤5 逐集生成 (创作+编辑两层契约) + 确认 + 级联重跑
"""
from __future__ import annotations

from app.services.book_service.creation_common import (
    elastic_labels,
    source_context,
    validate_script,
)
from app.services.book_service.creation_steps import (
    build_comment_layer,
    build_materials,
    build_roadmap,
    complete_input,
)
from app.services.book_service.episode_gen import (
    confirm_episode,
    generate_episode,
    rerun_cascade,
)

__all__ = [
    "complete_input", "build_comment_layer", "build_materials",
    "build_roadmap", "generate_episode", "confirm_episode", "rerun_cascade",
    "elastic_labels", "validate_script", "source_context",
]
