# -*- coding: utf-8 -*-
"""v3 八页线路由 (/api/anim/v3) — 1105 双轨 V0 骨架.

选线闸: 书级 pipeline_line() != "v3" → 409 指路 (默认 v2, 存量零扰动)。
job/同集互斥/取消/SSE/台账全复用 anim_service.start_phase (零新机制)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

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


@router.get("/line/{book_id}")
def v3_line(book_id: str):
    """书级选线查询 (讲书页入口分流用, 无需集号)。"""
    try:
        from app.services.anim_pipeline.planner import resolve_book
        from app.services.anim_pipeline.config import load
        _, book_title = resolve_book(load().db_path, book_id)
    except Exception as exc:  # noqa: BLE001 — 解析失败按 v2 (fail-open, 入口不炸)
        raise HTTPException(status_code=404, detail=str(exc))
    return {"line": pipeline_line(book_title), "book_title": book_title}


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
    running = None
    with anim_service._JOBS_LOCK:
        rj = next((j for j in anim_service._JOBS.values()
                   if j["status"] == "running" and j["book_id"] == book_id
                   and j["ep"] == ep), None)
        if rj:
            running = anim_service._job_view(rj)
    return {"book_id": book_id, "book_title": book_title, "ep": ep,
            "line": line, "gates": gates,
            "lines_done": bool((doc_line or {}).get("lines")),
            "running_job": running}


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


# ── ① 文稿分诊 (V1) ────────────────────────────────────────────────────

def _load_v3_doc(book_title: str, ep: int) -> dict:
    from app.services.anim_pipeline import shots as shots_mod
    return shots_mod.load_doc(book_title, ep)


@router.get("/ep/{book_id}/{ep}/triage")
def v3_triage_get(book_id: str, ep: int):
    """分诊数据源: v3.lines + 路由卡 + 闸态。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        doc = _load_v3_doc(book_title, ep)
    except FileNotFoundError:
        return {"lines": [], "cards": [], "gates": {}, "triaged": False}
    v3 = doc.get("v3") or {}
    return {"lines": v3.get("lines") or [], "cards": v3.get("cards") or [],
            "gates": v3_pipeline.gates_view(doc),
            "triaged": bool(v3.get("lines"))}


class TriageSaveBody(BaseModel):
    lines: list[dict[str, Any]] = []


@router.post("/ep/{book_id}/{ep}/triage/save")
def v3_triage_save(book_id: str, ep: int, body: TriageSaveBody):
    """一句一行编辑保存: 文本/语态/处置回写 + 确定性重检 + 分诊闸自动开合。"""
    from app.services.anim_pipeline.v3_pipeline.triage import det_check, route_markers
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        old = {ln["no"]: ln for ln in v3.get("lines") or []}
        n_edit = 0
        for row in body.lines:
            ln = old.get(row.get("no"))
            if not ln:
                continue
            if str(row.get("text", ln["text"])).strip() != ln["text"].strip():
                n_edit += 1
                ln["text"] = str(row.get("text")).strip()
            if row.get("mode") and row["mode"] != ln["mode"]:
                ln["mode"] = row["mode"]
                n_edit += 1
            if row.get("disp") is not None:
                ln["disp"] = row["disp"]
            if row.get("adopted"):
                # 采纳的建议种类 (quote/img/book/event/list) — ③ 分镜页据此建卡/配图/书证
                ln["adopted"] = [str(k) for k in row["adopted"] if k][:6]
            # 编辑后确定性重检 (LLM 标注不重跑 — 病检是执法层)
            mode, routed = route_markers(ln["text"])
            if mode:
                ln["mode"], ln["disp"] = mode, "routed"
            ln["flag"] = det_check(ln["text"])
        v3["lines"] = list(old.values())
        # 闸: 无未处置 block (disp=cleaned/converted 视为处置)
        unresolved = [ln for ln in v3["lines"]
                      if ln.get("flag") and ln["flag"].get("level") == "block"
                      and ln.get("disp") not in ("cleaned", "converted", "routed")]
        v3.setdefault("gates", {})["triage"] = not unresolved
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"saved": len(body.lines), "edited": n_edit,
            "blocks_unresolved": len(unresolved),
            "gate": not unresolved}
