"""Director LLM output parsing and slot rule enforcement (包化拆分).

原单文件 ``app/services/director_parser.py`` 按职责拆分为本包子模块,
此处汇总公共 API + 私有外部依赖,下游导入零改动。
"""
from __future__ import annotations

from app.services.director_parser._json import extract_json
from app.services.director_parser._parse import parse_llm_plan
from app.services.director_parser._rows import (
    _VALID_WORKFLOWS,
    is_new_schema,
    map_emotion,
    map_intensity,
    map_visual_type_to_workflow,
    parse_legacy_row,
    parse_new_row,
)
from app.services.director_parser._rules import (
    _HOST_FAMILY,
    _best_fallback_workflow,
    cap_host_count,
    enforce_adjacency_rules,
    enforce_host_rules,
)

__all__ = [
    # JSON 提取
    "extract_json",
    # 行解析器
    "_VALID_WORKFLOWS",
    "map_visual_type_to_workflow",
    "map_intensity",
    "map_emotion",
    "parse_legacy_row",
    "parse_new_row",
    "is_new_schema",
    # 编排入口
    "parse_llm_plan",
    # 规则执行 (+ 私有外部依赖: _fallback.py / slot_executor.py / retry.py)
    "_HOST_FAMILY",
    "_best_fallback_workflow",
    "enforce_host_rules",
    "cap_host_count",
    "enforce_adjacency_rules",
]
