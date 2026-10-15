"""行解析器 — 新旧 prompt 行 → DirectorSlotPlan.

行为逐字迁移自原 director_parser.py (2026-08-08 包化重构).
"""
from __future__ import annotations

import re
from typing import Any

from app.schemas.director import DirectorSlotPlan

__all__ = [
    "_VALID_WORKFLOWS",
    "map_visual_type_to_workflow",
    "map_intensity",
    "map_emotion",
    "is_new_schema",
    "parse_legacy_row",
    "parse_new_row",
]

# hf_opening/hf_quote 执行层已支持 (slot_executor 路由齐全), 此前漏在解析白名单外:
# LLM 输出 hf_quote 会被 map_visual_type_to_workflow 洗成 hf_chart/broll_pexels,
# 而 render_config 里的 quote 数据只有 _execute_hf_quote 认识 → 渲染空卡 (2025-08-25).
# evidence_image (2026-09-04): 证据图管线③新 workflow, 白名单缺位同样会被洗掉。
# hf_identity/hf_follow (2026-09-05): 身份卡/收尾互动卡 (杂志风 06/07, 财经线专用)。
_VALID_WORKFLOWS = {
    "host", "broll_pexels", "broll_local", "hf_chart", "hf_title",
    "mixed_host_broll", "hf_opening", "hf_quote", "evidence_image",
    "hf_identity", "hf_follow",
}


def _quote_workflow(material_source: dict[str, Any], params: dict[str, Any] | None = None) -> bool:
    """render_config 是否为引用卡数据形态 (quote/hot/name/role) → 该走 hf_quote."""
    rc = (material_source or {}).get("render_config") or (params or {}).get("render_config") or {}
    return isinstance(rc, dict) and bool(rc.get("quote")) and not rc.get("chart")


def map_visual_type_to_workflow(visual_type: str, material_source: dict[str, Any]) -> str:
    """Legacy bridge for old prompt output format."""
    if visual_type in _VALID_WORKFLOWS:
        return visual_type
    if visual_type == "出镜":
        return "host"
    if visual_type == "混合":
        return "mixed_host_broll"
    if visual_type in ("evidence", "证据图", "证据"):
        return "evidence_image"
    mtype = (material_source or {}).get("type")
    category = (material_source or {}).get("category") or ""
    if mtype == "dynamic":
        if "标题" in category or "title" in category.lower():
            return "hf_title"
        # 引用卡数据 (quote 键) → hf_quote, 而非默认 hf_chart (2026-08-25)
        if _quote_workflow(material_source):
            return "hf_quote"
        return "hf_chart"
    if (material_source or {}).get("file"):
        return "broll_local"
    return "broll_pexels"


def map_intensity(value: str) -> str:
    return value if value in {"low", "medium", "high"} else "medium"


def map_emotion(value: str) -> str:
    return value if value in {"opening", "rising", "climax", "falling", "closing"} else "rising"


def _static_params(material_source: dict[str, Any]) -> dict[str, Any]:
    """静态素材 → file/fallback_file."""
    return {
        "file": material_source.get("file"),
        "fallback_file": material_source.get("fallback_file"),
    }


def _dynamic_params(material_source: dict[str, Any]) -> dict[str, Any]:
    """动态素材 → render_config."""
    return {"render_config": material_source.get("render_config") or {}}


def parse_legacy_row(row: dict[str, Any], line_id_to_timing: dict[int, dict[str, Any]],
                     total_duration: float) -> DirectorSlotPlan | None:
    """Convert a legacy v2 prompt row (line_id + material_source) into a slot."""
    line_id = int(row.get("line_id", 0))
    timing = line_id_to_timing.get(line_id)
    if timing is None:
        return None
    start = max(0.0, float(timing.get("start", 0.0)))
    end = min(float(total_duration), float(timing.get("end", total_duration)))
    if end <= start:
        end = start + 0.5
    material_source = row.get("material_source") or {}
    visual_type_raw = row.get("visual_type", "出镜")
    workflow = map_visual_type_to_workflow(visual_type_raw, material_source)

    params: dict[str, Any] = {
        "intensity": map_intensity(row.get("intensity", "medium")),
        "emotion": map_emotion(row.get("emotion", "rising")),
        "effect": row.get("effect") or "无",
        "sound": row.get("sound") or "无",
        "category": material_source.get("category"),
    }
    if material_source.get("type") == "static":
        params.update(_static_params(material_source))
    elif material_source.get("type") == "dynamic":
        params.update(_dynamic_params(material_source))

    return DirectorSlotPlan(
        slot_index=line_id - 1,
        start_sec=round(start, 3),
        end_sec=round(end, 3),
        text_context=row.get("text"),
        segment_id=timing.get("segment_id"),
        visual_type=workflow,  # type: ignore[arg-type]
        workflow=workflow,  # type: ignore[arg-type]
        params=params,
    )


def _camera_angle(row: dict[str, Any], workflow: str) -> int:
    """机位: LLM 输出 camera 字段, 仅 host/mixed 有效, 钳制 [1,4]."""
    cam = row.get("camera") or row.get("camera_angle")
    try:
        camera_angle = max(1, min(4, int(cam))) if cam else 1
    except (TypeError, ValueError):
        camera_angle = 1
    if workflow not in ("host", "mixed_host_broll"):
        camera_angle = 1
    return camera_angle


def _refs_to_context(
    row: dict[str, Any],
    timings: list[dict[str, Any]],
    start: float,
    end: float,
) -> tuple[str | None, str | None, list[str]]:
    """segment_refs (S 编号) → (text_context, segment_id, 全部 segment_ids).

    零复写协议 (2026-08-25): LLM slot 只引用句编号, 代码按 timings 拼回口播文本。
    三级兜底: refs → 旧版 text_context → 按 start/end 时间夹逼覆盖的段。
    """
    # 1) refs: S001 → timings[0]
    refs = row.get("segment_refs") or []
    if isinstance(refs, str):
        refs = [refs]
    picked: list[dict[str, Any]] = []
    if refs:
        for r in refs:
            m = re.match(r"[Ss](\d+)", str(r).strip())
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(timings):
                    picked.append(timings[idx])
    # 2) 旧版直给 text_context → 原样用
    if not picked:
        tc = row.get("text_context") or row.get("text")
        if tc:
            return tc, row.get("segment_id"), []
    if not picked:
        # 3) 时间夹逼: 取与 [start, end] 相交(中点落在区间内)的段, 无则最近一段
        for t in timings:
            ts, te = float(t.get("start", 0)), float(t.get("end", 0))
            mid = (ts + te) / 2
            if start - 0.01 <= mid <= end + 0.01:
                picked.append(t)
        if not picked and timings:
            picked = [min(timings, key=lambda t: abs(float(t.get("start", 0)) - start))]
    if not picked:
        return None, None, []
    ids = [str(t.get("segment_id")) for t in picked if t.get("segment_id")]
    return "||".join(str(t.get("text", "")) for t in picked), ids[0] if ids else None, ids


def parse_new_row(row: dict[str, Any], total_duration: float,
                  timings: list[dict[str, Any]] | None = None) -> DirectorSlotPlan | None:
    """Convert a new v2 prompt row (direct slot fields) into a slot."""
    workflow = row.get("workflow") or row.get("visual_type") or "host"
    if workflow not in _VALID_WORKFLOWS:
        workflow = map_visual_type_to_workflow(workflow, row.get("material_source") or {})

    try:
        slot_index = int(row.get("slot_index", 0))
        start = float(row.get("start_sec", 0))
        end = float(row.get("end_sec", start + 0.5))
    except (TypeError, ValueError):
        return None

    start = max(0.0, min(start, total_duration))
    end = max(start + 0.1, min(end, total_duration))

    params = dict(row.get("params") or {})
    params.setdefault("intensity", map_intensity(row.get("intensity", "medium")))
    params.setdefault("emotion", map_emotion(row.get("emotion", "rising")))

    # 数据感知校正 (2026-08-25): LLM 按提示词第 193 行格式填了 quote/hot/name/role,
    # 但 workflow 误写成 broll/hf_title (提示词 200 行强化仍偶发不遵守) → 强制 hf_quote,
    # 否则 quote 数据没有消费者: broll 拿整句话当搜索词必失败, hf_chart 渲染空卡.
    if workflow in ("broll_pexels", "broll_local", "hf_title", "hf_chart") and \
            _quote_workflow(row.get("material_source") or {}, params):
        workflow = "hf_quote"

    # 零复写协议 (2026-08-25): refs → text_context/segment_id 代码拼回;
    # timings 未传(旧调用方) 或 refs/text 皆无时退化原逻辑。
    if timings:
        tc, sid, sids = _refs_to_context(row, timings, start, end)
        if tc is None and not (row.get("text_context") or row.get("text")):
            return None  # 引用与文本皆无 — 无法定位口播, 弃 slot
        if sids:
            params["segment_ids"] = sids
    else:
        tc, sid = row.get("text_context") or row.get("text"), row.get("segment_id")

    return DirectorSlotPlan(
        slot_index=slot_index,
        start_sec=round(start, 3),
        end_sec=round(end, 3),
        text_context=tc,
        segment_id=sid,
        visual_type=workflow,  # type: ignore[arg-type]
        workflow=workflow,  # type: ignore[arg-type]
        params=params,
        camera_angle=_camera_angle(row, workflow),
    )


def is_new_schema(rows: list[dict[str, Any]]) -> bool:
    """Heuristic: new prompt uses workflow/start_sec/end_sec directly."""
    if not rows:
        return False
    sample = rows[0]
    return "workflow" in sample or "start_sec" in sample
