# -*- coding: utf-8 -*-
"""v3 八页线路由 (/api/anim/v3) — 1105 双轨 V0 骨架.

选线闸: 书级 pipeline_line() != "v3" → 409 指路 (默认 v2, 存量零扰动)。
job/同集互斥/取消/SSE/台账全复用 anim_service.start_phase (零新机制)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services import anim_service
from app.services.anim_pipeline import v3_pipeline
from app.services.anim_pipeline.config import pipeline_line

router = APIRouter(prefix="/api/anim/v3", tags=["anim-v3"])

_V3_PHASES = ("v3_triage", "v3_arcs", "v3_shots", "v3_direct", "v3_stamp")


def _line_gate(book_title: str) -> None:
    if pipeline_line(book_title) != "v3":
        raise HTTPException(
            status_code=409,
            detail=f"《{book_title}》 未开通 v3 线 (书级产线绑定缺省 v2) — "
            "写 outputs/动画/_资产/产线绑定_书名.json {\"line\":\"v3\"} 后重试")


@router.get("/ep/{book_id}/{ep}/state")
def v3_state(book_id: str, ep: int):
    """v3 线状态: 选线 + 八页放行闸 (轨/页锁数据源)。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    line = pipeline_line(book_title)
    gates: dict[str, bool] = {}
    doc_line = None
    if line == "v3":
        from app.services.anim_pipeline import shots as shots_mod
        try:
            doc = shots_mod.load_doc(book_title, ep)
            gates = v3_pipeline.gates_view(doc)
            doc_line = doc.get("v3") or {}
        except FileNotFoundError:
            gates = v3_pipeline.gates_view({})
    return {"book_id": book_id, "book_title": book_title, "ep": ep,
            "line": line, "gates": gates,
            "lines_done": bool((doc_line or {}).get("lines"))}


@router.post("/ep/{book_id}/{ep}/{phase}")
def v3_start(book_id: str, ep: int, phase: str):
    """启动 v3 相位 job (复用 start_phase: 同集闸/台账/SSE 全继承)。"""
    if phase not in _V3_PHASES:
        raise HTTPException(status_code=422, detail=f"未知 v3 相位: {phase} (可选 {_V3_PHASES})")
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        return anim_service.start_phase(book_id, ep, phase)
    except anim_service.AnimBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
