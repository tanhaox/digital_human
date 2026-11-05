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


# ── ② 立意 (V2) ─────────────────────────────────────────────────────────

class ArcSaveBody(BaseModel):
    arcs: list[dict[str, Any]] = []


@router.get("/ep/{book_id}/{ep}/concept")
def v3_concept_get(book_id: str, ep: int):
    """立意数据源: v3.arcs + hints/issues + 按弧分组的稿行 + 闸态。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        doc = _load_v3_doc(book_title, ep)
    except FileNotFoundError:
        return {"arcs": [], "hints": [], "issues": [], "gates": {}, "ready": False}
    v3 = doc.get("v3") or {}
    arcs = v3.get("arcs") or []
    lines = v3.get("lines") or []
    by_arc: dict[str, list[dict]] = {}
    for a in arcs:
        by_arc[a["arc_id"]] = [
            {"no": ln["no"], "text": ln["text"], "mode": ln["mode"]}
            for ln in lines if a["no_start"] <= ln["no"] <= a["no_end"]
            and str(ln.get("text") or "").strip()]
    return {"arcs": arcs, "hints": v3.get("arc_hints") or [],
            "issues": v3.get("arc_issues") or [],
            "lines_of_arc": by_arc,
            "gates": v3_pipeline.gates_view(doc),
            "ready": bool(arcs)}


class ShotSaveBody(BaseModel):
    shots: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] | None = None


@router.get("/ep/{book_id}/{ep}/shots")
def v3_shots_get(book_id: str, ep: int):
    """分镜文字数据源: v3.shots + cards + issues + 按镜分组的句 + 闸态。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        doc = _load_v3_doc(book_title, ep)
    except FileNotFoundError:
        return {"shots": [], "cards": [], "issues": {}, "gates": {}, "ready": False}
    v3 = doc.get("v3") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    shots = v3.get("shots") or []
    for s in shots:
        s["sents"] = [{"no": no, "text": str(lines.get(no, {}).get("text") or "")}
                      for no in s.get("line_refs") or [] if no in lines]
    cards = v3.get("cards") or []
    return {"shots": shots, "cards": cards, "issues": v3.get("shot_issues") or {},
            "warnings": v3.get("shot_warnings") or [],
            "gates": v3_pipeline.gates_view(doc), "ready": bool(shots)}


@router.post("/ep/{book_id}/{ep}/shots/save")
def v3_shots_save(book_id: str, ep: int, body: ShotSaveBody):
    """分镜文字编辑保存: 画面句/卡 回写 + 质检复跑 + 分镜闸开合。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        cur = {s["sid"]: s for s in v3.get("shots") or []}
        n_edit = 0
        for row in body.shots:
            s = cur.get(str(row.get("sid") or ""))
            if not s:
                continue
            for k in ("keyframe_zh",):
                if row.get(k) is not None and str(row[k]).strip() != str(s.get(k) or ""):
                    s[k] = str(row[k]).strip()[:600]
                    n_edit += 1
            if row.get("in_image_text") is not None:
                s["in_image_text"] = [str(x)[:40] for x in row["in_image_text"]][:4]
            if row.get("text_layer") is not None:
                s["text_layer"] = [str(x)[:40] for x in row["text_layer"]][:6]
        if body.cards is not None:
            v3["cards"] = [{"cid": str(c.get("cid") or ""), "style": str(c.get("style") or "list"),
                            "text": str(c.get("text") or "")[:60], "sid": str(c.get("sid") or ""),
                            "line_no": int(c.get("line_no") or 0),
                            "from": str(c.get("from") or "manual")}
                           for c in body.cards]
        from app.services.anim_pipeline.v3_pipeline.shots import text_layer_issues
        issues = text_layer_issues(list(cur.values()))
        v3["shot_issues"] = issues
        v3.setdefault("gates", {})["shots"] = not issues
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"saved": len(body.shots), "edited": n_edit,
            "issues": issues, "gate": not issues}


@router.post("/ep/{book_id}/{ep}/concept/resplit")
def v3_concept_resplit(book_id: str, ep: int):
    """按语态变化点把跨语态的弧拆回多弧 (确定性修复, 保留首段立意文字)。

    实锤: 人闸合并了不同语态的相邻弧 (书证+评论+书证) → 弧界未落模块边界。
    """
    from app.services.anim_pipeline.v3_pipeline.arcs import (boundary_issues,
                                                             est_seconds, TYPE_OF_MODE)
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        lines = v3.get("lines") or []
        line_of = {ln["no"]: ln for ln in lines}
        valid = {ln["no"] for ln in lines
                 if str(ln.get("text") or "").strip()
                 and str(ln.get("mode") or "") not in ("section", "card", "img", "unknown")}
        before = len(v3.get("arcs") or [])
        out: list[dict] = []
        for a in v3.get("arcs") or []:
            seg = [no for no in range(int(a["no_start"]), int(a["no_end"]) + 1) if no in valid]
            if not seg:
                continue
            # 按语态变化点切段
            groups: list[list[int]] = []
            for no in seg:
                mode = str(line_of[no].get("mode") or "")
                if groups and str(line_of[groups[-1][-1]].get("mode") or "") == mode:
                    groups[-1].append(no)
                else:
                    groups.append([no])
            for gi, grp in enumerate(groups):
                mode = str(line_of[grp[0]].get("mode") or "")
                sub = [line_of[n] for n in grp]
                out.append({
                    **{k: v for k, v in a.items() if k not in ("no_start", "no_end", "line_count", "est_s")},
                    "no_start": grp[0], "no_end": grp[-1],
                    "mode": mode, "type": TYPE_OF_MODE.get(mode, "narr"),
                    "line_count": len(sub), "est_s": est_seconds(sub),
                    # 首段保留原立意, 拆出的后续段留空待人填
                    "world": a.get("world", "") if gi == 0 else "",
                    "metaphor": a.get("metaphor", "") if gi == 0 else "",
                    "narrative": a.get("narrative", "") if gi == 0 else "",
                })
        out.sort(key=lambda x: int(x["no_start"]))
        for i, a in enumerate(out, 1):
            a["arc_id"] = f"A{i}"
        v3["arcs"] = out
        issues = boundary_issues(out, lines)
        v3["arc_issues"] = issues
        v3.setdefault("gates", {})["arcs"] = not issues
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"arcs_before": before, "arcs_after": len(out), "issues": issues}


@router.post("/ep/{book_id}/{ep}/concept/save")
def v3_concept_save(book_id: str, ep: int, body: ArcSaveBody):
    """立意编辑保存: world/metaphor/narrative 回写 + 执法复检 + 立意闸开合。"""
    from app.services.anim_pipeline.v3_pipeline.arcs import boundary_issues
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        cur = {a["arc_id"]: a for a in v3.get("arcs") or []}
        lines = v3.get("lines") or []
        line_of = {ln["no"]: ln for ln in lines}
        from app.services.anim_pipeline.v3_pipeline.arcs import est_seconds, TYPE_OF_MODE
        new_list: list[dict] = []
        n_edit = 0
        for row in body.arcs:
            a = cur.get(str(row.get("arc_id") or ""))
            if not a:
                continue
            for k in ("narrative", "world", "metaphor"):
                if row.get(k) is not None and str(row[k]).strip() != str(a.get(k) or ""):
                    a[k] = str(row[k]).strip()[:200]
                    n_edit += 1
            # 结构编辑 (人闸: 合并/改型) — 只认合法值, 其余保留原值
            if row.get("type") in TYPE_OF_MODE.values() and row["type"] != a.get("type"):
                a["type"] = row["type"]
                n_edit += 1
            for k in ("no_start", "no_end"):
                if row.get(k) is not None and int(row[k]) != int(a.get(k) or 0):
                    a[k] = int(row[k])
                    n_edit += 1
            seg = [line_of[no] for no in range(int(a["no_start"]), int(a["no_end"]) + 1)
                   if no in line_of and str(line_of[no].get("text") or "").strip()
                   and line_of[no].get("mode") not in ("section", "card", "img", "unknown")]
            a["line_count"] = len(seg)
            a["est_s"] = est_seconds(seg)
            new_list.append(a)
        # 重编号 (合并后序号连续) + 时间序
        new_list.sort(key=lambda x: int(x["no_start"]))
        for i, a in enumerate(new_list, 1):
            a["arc_id"] = f"A{i}"
        v3["arcs"] = new_list
        issues = boundary_issues(new_list, lines)
        v3["arc_issues"] = issues
        v3.setdefault("gates", {})["arcs"] = not issues
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"saved": len(body.arcs), "edited": n_edit,
            "issues": issues, "gate": not issues}


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
