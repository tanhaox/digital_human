"""编排入口 — parse_llm_plan: 提取 JSON → 行解析 → 排序重编号 → 规则执行.

行为逐字迁移自原 director_parser.py (2026-08-08 包化重构).
"""
from __future__ import annotations

from typing import Any

from app.schemas.director import DirectorPlan, DirectorSlotPlan
from app.services.director_parser._json import extract_json
from app.services.director_parser._rows import (
    is_new_schema,
    parse_legacy_row,
    parse_new_row,
)
from app.services.director_parser._rules import enforce_host_rules

__all__ = ["parse_llm_plan"]


def _extract_output(raw_output: str) -> tuple[str | None, list[dict[str, Any]]]:
    """从 LLM 输出提取 (title, rows). 兼容 dict 包槽 / 纯数组两种格式."""
    parsed = extract_json(raw_output)

    title: str | None = None
    if isinstance(parsed, dict):
        title = parsed.get("video_title") or parsed.get("title")
        rows = parsed.get("slots") or []
        if not isinstance(rows, list):
            raise ValueError("Director response 'slots' is not a list")
        return title, rows
    if isinstance(parsed, list):
        return None, parsed
    raise ValueError("Director response is not a JSON object or array")


def _build_slots(rows: list[dict[str, Any]],
                 timings_by_index: dict[int, dict[str, Any]],
                 total_duration: float,
                 timings: list[dict[str, Any]] | None = None) -> list[DirectorSlotPlan]:
    """按 schema 分派解析行 → 按 (start_sec, slot_index) 排序 → 重编号.

    timings (2026-08-25 零复写): 透传给 parse_new_row 供 segment_refs 拼装。
    """
    slots: list[DirectorSlotPlan] = []
    if is_new_schema(rows):
        for row in rows:
            slot = parse_new_row(row, total_duration, timings=timings)
            if slot is not None:
                slots.append(slot)
    else:
        for row in rows:
            slot = parse_legacy_row(row, timings_by_index, total_duration)
            if slot is not None:
                slots.append(slot)

    slots.sort(key=lambda s: (s.start_sec, s.slot_index))
    for i, slot in enumerate(slots):
        slot.slot_index = i
    return slots


def parse_llm_plan(
    raw_output: str,
    segment_timings: list[dict[str, Any]],
    total_duration: float,
    enabled_pipelines: set[str] | None = None,
) -> DirectorPlan:
    """Parse director LLM output and align with real segment timings.

    兼容两种 LLM 输出:
    - 新格式: {"video_title": "...", "slots": [...]}
    - 旧格式/兜底: [...] 纯数组

    Args:
        enabled_pipelines: 启用的管线集合. None=全部启用.
    """
    title, rows = _extract_output(raw_output)
    timings_by_index = {i + 1: t for i, t in enumerate(segment_timings)}
    slots = _build_slots(rows, timings_by_index, total_duration, timings=segment_timings)
    slots = enforce_host_rules(slots, total_duration, enabled_pipelines)
    slots = _patch_uncovered(slots, segment_timings)
    slots = _split_oversized(slots, segment_timings)
    slots.sort(key=lambda s: (s.start_sec, s.slot_index))
    for i, s in enumerate(slots):
        s.slot_index = i
    return DirectorPlan(title=title, slots=slots)


def _split_oversized(
    slots: list[DirectorSlotPlan],
    segment_timings: list[dict[str, Any]],
    max_sec: float = 30.0,
) -> list[DirectorSlotPlan]:
    """拆超长 slot (2026-08-26): LLM 合并相邻段产生 >30s 大 slot → broll 素材
    (10~30s) 撑不满 → 草稿层钳制留黑 (尾部画面短缺实测根因)。
    按句贪心 12~25s 重拆 (继承 workflow/params), 尾卡/来源卡不受影响。
    """
    import copy as _copy

    sid_order = {str(t.get("segment_id")): t for t in segment_timings}
    out: list[DirectorSlotPlan] = []
    for s in slots:
        dur = s.end_sec - s.start_sec
        ids = [x for x in (s.params.get("segment_ids") or []) if x in sid_order]
        if dur <= max_sec or len(ids) < 2:
            out.append(s)
            continue
        # 按句贪心分组: 每组 ≥12s 收, 单组不超 max_sec
        groups: list[list[str]] = []
        cur: list[str] = []
        cur_start: float | None = None
        for x in ids:
            t = sid_order[x]
            if cur_start is None:
                cur_start = float(t.get("start", 0))
            cur.append(x)
            if float(t.get("end", 0)) - cur_start >= 12.0:
                groups.append(cur)
                cur, cur_start = [], None
        if cur:
            if groups and len(cur) == 1:
                groups[-1].extend(cur)  # 孤句并入前组
            else:
                groups.append(cur)
        if len(groups) <= 1:
            out.append(s)
            continue
        for gi, grp in enumerate(groups):
            ts = [sid_order[x] for x in grp]
            ns = _copy.deepcopy(s)
            ns.params = dict(s.params)
            ns.params["segment_ids"] = grp
            ns.start_sec = round(float(ts[0].get("start", s.start_sec)), 3)
            ns.end_sec = round(float(ts[-1].get("end", s.end_sec)), 3)
            ns.text_context = "||".join(str(t.get("text", "")) for t in ts) or s.text_context
            ns.segment_id = grp[0]
            out.append(ns)
    return out


def _patch_uncovered(
    slots: list[DirectorSlotPlan],
    segment_timings: list[dict[str, Any]],
) -> list[DirectorSlotPlan]:
    """补洞 (2026-08-26): LLM refs 漏引的句归并到时间最近 slot, 消灭无字幕段。

    段视图协议实测 83 句漏 6 句 — 漏引句既无画面也无字幕 (剪映成片"字幕短缺"
    根因)。确定性归并: 未覆盖句并入其时间中点所在(或最近) slot, 该 slot 的
    segment_ids/text_context 按句序重拼。LLM 漏洞不该让观众看到。
    """
    if not slots or not segment_timings:
        return slots
    covered: set[str] = set()
    for s in slots:
        if s.segment_id:
            covered.add(str(s.segment_id))
        for sid in (s.params.get("segment_ids") or []):
            covered.add(str(sid))
    sid_order = {str(t.get("segment_id")): i for i, t in enumerate(segment_timings)}
    missing = [
        (i, t) for i, t in enumerate(segment_timings)
        if str(t.get("segment_id")) not in covered
    ]
    if not missing:
        return slots
    for _, t in missing:
        mid = (float(t.get("start", 0)) + float(t.get("end", 0))) / 2
        target = min(
            slots,
            key=lambda s: 0 if s.start_sec <= mid <= s.end_sec
            else min(abs(s.start_sec - mid), abs(s.end_sec - mid)),
        )
        ids = set(target.params.get("segment_ids") or ([target.segment_id] if target.segment_id else []))
        ids.add(str(t.get("segment_id")))
        ordered = sorted(ids, key=lambda x: sid_order.get(x, 10**9))
        target.params["segment_ids"] = ordered
        target.segment_id = ordered[0]
        rebuilt = "||".join(
            str(segment_timings[sid_order[x]].get("text", "")) for x in ordered if x in sid_order
        )
        if rebuilt:
            target.text_context = rebuilt
    return slots
