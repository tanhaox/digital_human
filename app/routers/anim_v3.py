# -*- coding: utf-8 -*-
"""v3 八页线路由 (/api/anim/v3) — 1105 双轨 V0 骨架.

选线闸: 书级 pipeline_line() != "v3" → 409 指路 (默认 v2, 存量零扰动)。
job/同集互斥/取消/SSE/台账全复用 anim_service.start_phase (零新机制)。
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Any

from app.services import anim_service
from app.services.anim_pipeline import v3_pipeline
from app.services.anim_pipeline.config import pipeline_line

router = APIRouter(prefix="/api/anim/v3", tags=["anim-v3"])

_V3_PHASES = ("v3_triage", "v3_arcs", "v3_shots", "v3_direct", "v3_k2", "v3_redo", "v3_h3", "v3_stamp")


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
    # 1106 用户令 (⑤TTS→⑥K2): K2 前置 = 按槽音频包就位 (块数==未屏弃槽数)
    audio_ready = False
    if line == "v3" and gates.get("direct"):
        try:
            from app.services.anim_draft import fetch_episode_audio
            _aud = fetch_episode_audio(book_id, ep)
            _n_slots = sum(1 for s in (doc_line.get("shots") or [])
                           if not s.get("audio_muted"))
            audio_ready = len(_aud.get("files") or []) == _n_slots
        except Exception:  # noqa: BLE001 — 无音频=未就位, 不炸
            audio_ready = False
    running = None
    with anim_service._JOBS_LOCK:
        rj = next((j for j in anim_service._JOBS.values()
                   if j["status"] == "running" and j["book_id"] == book_id
                   and j["ep"] == ep), None)
        if rj:
            running = anim_service._job_view(rj)
    v3d = doc_line or {}
    counts = {"lines": len(v3d.get("lines") or []),
              "arcs": len(v3d.get("arcs") or []),
              "shots": len(v3d.get("shots") or []),
              "cards": len(v3d.get("cards") or []),
              "shot_issues": sum(len(v) for k, v in (v3d.get("shot_issues") or {}).items()
                                 if k != "弧级")}
    return {"book_id": book_id, "book_title": book_title, "ep": ep,
            "line": line, "gates": gates,
            "lines_done": bool(v3d.get("lines")),
            "frozen": bool(v3d.get("frozen_at")),
            "audioReady": audio_ready,
            "counts": counts,
            "running_job": running}


class PhaseStartBody(BaseModel):
    only: list[str] = []            # 单镜重掷 (v3_k2/v3_h3) / 列队重做 (v3_redo)
    to_image: bool = False          # v3_redo: 到生图停 (默认自动过审)
    to_video: bool = False          # v3_redo: ⑧页列队重做 — 重设计→生图→生视频全链
    note: str = ""                  # v3_redo: 重做原因 (带进重设计提示)
    retry_failed: bool = False      # v3_h3: 连败镜重试 (换 h3_seed)


@router.post("/ep/{book_id}/{ep}/{phase}")
def v3_start(book_id: str, ep: int, phase: str, body: PhaseStartBody | None = None):
    """启动 v3 相位 job (复用 start_phase: 同集闸/台账/SSE 全继承)。"""
    if phase not in _V3_PHASES:
        raise HTTPException(status_code=422, detail=f"未知 v3 相位: {phase} (可选 {_V3_PHASES})")
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    only = set(body.only) if (body and body.only) else None
    if only and phase not in ("v3_k2", "v3_redo", "v3_h3"):
        raise HTTPException(status_code=422, detail="only 仅支持 v3_k2/v3_redo")
    kw = {"only": only}
    if phase == "v3_h3":
        kw["retry_failed"] = bool(body and body.retry_failed)
    if phase == "v3_redo":
        kw["to_image"] = bool(body and body.to_image)
        kw["to_video"] = bool(body and body.to_video)
        if body and body.note:
            kw["note"] = str(body.note)[:200]
    try:
        return anim_service.start_phase(book_id, ep, phase, **kw)
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


@router.post("/ep/{book_id}/{ep}/concept/fill-worlds")
def v3_concept_fill_worlds(book_id: str, ep: int):
    """系统自查自补: 叙事/书证/人设弧缺「视觉世界」→ LLM 定点补全 (用户令: 不甩给人)。"""
    from app.services.anim_pipeline.v3_pipeline.arcs import (fill_missing_worlds,
                                                             missing_worlds)
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        arcs = v3.get("arcs") or []
        n = fill_missing_worlds(arcs, v3.get("lines") or [], book_title, ep)
        if n:
            from app.services.anim_pipeline import shots as shots_mod
            shots_mod.save(doc)
    return {"filled": n, "remaining": len(missing_worlds(arcs))}


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
        return {"shots": [], "cards": [], "arcs": [], "issues": {}, "gates": {},
                "ready": False}
    v3 = doc.get("v3") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    shots = v3.get("shots") or []
    for s in shots:
        s["sents"] = [{"no": no, "text": str(lines.get(no, {}).get("text") or "")}
                      for no in s.get("line_refs") or [] if no in lines]
    cards = v3.get("cards") or []
    # 弧元信息随包下发: ③页按弧分块要显示 型/行号段/视觉世界 (缺失=写手无世界可继承, 要显形)
    arcs = [{k: a.get(k) for k in ("arc_id", "type", "mode", "no_start", "no_end",
                                   "line_count", "est_s", "world", "metaphor")}
            for a in (v3.get("arcs") or [])]
    # ④ 导演: 已填数据 + 景别预填 + 词表 (单一事实源 → UI 与 H3 壳同表)
    from app.services.anim_pipeline.v3_pipeline.shots import suggest_size
    from app.services.anim_pipeline.v3_pipeline import direct as v3_direct
    direct = v3.get("direct") or {}
    for s in shots:
        d = direct.get(str(s["sid"]))
        s["direct"] = d
        s["size_guess"] = suggest_size(s.get("keyframe_zh"))
    return {"shots": shots, "cards": cards, "arcs": arcs,
            "cam_defs": v3_direct.CAM_DEFS,
            "sizes": v3_direct.SIZES, "no_move": list(v3_direct.NO_MOVE),
            "min_move_slot_s": v3_direct.MIN_MOVE_SLOT_S,
            "issues": v3.get("shot_issues") or {},
            "direct_issues": v3.get("direct_issues") or {},
            "warnings": v3.get("shot_warnings") or [],
            "gates": v3_pipeline.gates_view(doc), "ready": bool(shots)}


class CardRenderBody(BaseModel):
    cid: str
    size: str = "wide"


@router.get("/ep/{book_id}/{ep}/shots/card/{name}")
def v3_card_image(book_id: str, ep: int, name: str):
    """配图预览 (卡片图在 data/l0/{书名}/cards/, 不在 ep 目录 → 专用端点)。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    from app.services.jy_draft_service.quote_card import cards_dir
    safe = name.replace("/", "").replace("\\", "").replace("..", "")
    p = cards_dir(book_title) / f"{safe}.png"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"图不存在: {safe}")
    return FileResponse(str(p))


class CardDiagramBody(BaseModel):
    cid: str


@router.post("/ep/{book_id}/{ep}/shots/card/diagram")
def v3_card_diagram(book_id: str, ep: int, body: CardDiagramBody):
    """图示补全: 按本条弧整段原文重画结构 (跨行清单漏项是机制问题, 系统自己补)。"""
    from app.services.anim_pipeline.v3_pipeline.shots import complete_diagram
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        card = next((c for c in (v3.get("cards") or [])
                     if str(c.get("cid")) == body.cid), None)
        if not card:
            raise HTTPException(status_code=404, detail=f"card 不存在: {body.cid}")
        ok = complete_diagram(card, v3.get("lines") or [], v3.get("arcs") or [],
                              book_title, ep)
        if ok:
            from app.services.anim_pipeline import shots as shots_mod
            shots_mod.save(doc)
    return {"ok": ok, "cid": body.cid, "diagram": card.get("diagram")}


@router.post("/ep/{book_id}/{ep}/shots/card/render")
def v3_card_render(book_id: str, ep: int, body: CardRenderBody):
    """配图渲染: HTML 模板 + Playwright 截图 (复用封面视觉体系, 零 GPU)。

    用户令: 配图不走 ComfyUI (AI 画不了精确中文排版) — 产物落
    data/l0/{书名}/cards/, 装配钉图时按图名取 (anim_draft quote_img 轨)。
    """
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        cards = v3.get("cards") or []
        card = next((c for c in cards if c.get("cid") == body.cid), None)
        if not card:
            raise HTTPException(status_code=404, detail=f"卡不存在: {body.cid}")
        # 清单类: 把卡文字按标点拆成条目 (最多 5 条)
        import re as _re
        _KIND_OF_STYLE = {"quote": "金句", "event": "结论", "list": "总结",
                          "title": "标题", "img": "提示"}
        kind = str(card.get("kind") or _KIND_OF_STYLE.get(str(card.get("style")), "总结"))
        text = str(card.get("text") or "").strip()
        if kind in ("提示", "总结", "清单"):
            parts = [p.strip() for p in _re.split(r"[；;。\n]", text) if p.strip()]
            lines = parts[:5] or [text]
            title = parts[0][:14] if parts else text[:14]
            if len(parts) > 1:
                lines = parts[1:5] or parts[:1]
        else:
            title, lines = text[:20], [text]
        from app.services.anim_pipeline.v3_pipeline.card_render import (render_card,
                                                                       render_diagram)
        from app.services.anim_pipeline.v3_pipeline.shots import sanitize_diagram
        from app.services.jy_draft_service.quote_card import cards_dir
        # 文件名用 ASCII (实锤: 中文名在 URL 里需编码, 预览/跨端不稳)
        safe = f"v3_{body.cid}"
        out = cards_dir(book_title) / f"{safe}.png"
        _dg = sanitize_diagram(card.get("diagram") or {}, budget=[14], max_children=14)
        try:
            if _dg and _dg.get("children"):
                render_diagram(_dg, out, size=body.size)   # 脑图/流程图
            else:
                render_card(kind, title, lines, out, size=body.size)   # 文字版式
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"渲染失败: {exc}")
        card["image"] = safe          # 装配钉图按此名取 data/l0/{书名}/cards/{safe}.png
        card["image_file"] = out.name
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"cid": body.cid, "image": safe, "path": str(out), "rendered": True,
            "layout": "diagram" if (_dg and _dg.get("children")) else "text"}


@router.post("/ep/{book_id}/{ep}/shots/autocards")
def v3_shots_autocards(book_id: str, ep: int):
    """一键建卡: 把 ① 分诊给出的建议 (金句/清单/配图/书证/事件) 全部转成大字卡。

    等同在 ① 页逐条点「采纳」— 让 ③ 页零卡时不必回 ① 手工点。
    """
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    from app.services.anim_pipeline.v3_pipeline.shots import CARD_OF_SUGG, build_cards
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        n = 0
        for ln in v3.get("lines") or []:
            for sug in ln.get("suggs") or []:
                k = str(sug.get("kind") or "")
                if k not in CARD_OF_SUGG:
                    continue
                ad = ln.setdefault("adopted", [])
                if k not in ad:
                    ad.append(k)
                    n += 1
        cards = build_cards(v3, v3.get("shots") or [])
        v3["cards"] = cards
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"added": n, "cards": len(cards)}


@router.post("/ep/{book_id}/{ep}/shots/fix")
def v3_shots_fix(book_id: str, ep: int):
    """一键合规: 文字载体 → 就地写成「空白XX」(确定性改写)。

    1105 用户令: v3 停用图内字通道 (上屏字走剪映卡/图示 PNG), 所以不再申报
    in_image_text — 载体只能写成空白或换陈设。量词/的 后面才敢插「空白」
    (几张便签→几张空白便签); 「手机屏幕」这类不出量词的返回待人工。
    终态词属语义改写, 不自动动刀。
    """
    from app.services.anim_pipeline.v3_pipeline.shots import (blankify_carriers,
                                                              has_blocking_issues,
                                                              strip_quoted_labels,
                                                              text_layer_issues)
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        shots = list(v3.get("shots") or [])
        cur_issues = v3.get("shot_issues") or {}
        fixed, manual = blankify_carriers(shots, cur_issues)
        f2, m2 = strip_quoted_labels(shots, cur_issues)   # 引号标签/申报注释就地删
        fixed += [x for x in f2 if x not in fixed]
        manual += m2
        issues = text_layer_issues(shots)
        v3["shot_issues"] = issues
        v3.setdefault("gates", {})["shots"] = not has_blocking_issues(issues)
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"fixed": len(fixed), "fixed_sids": fixed, "manual": manual,
            "issues": issues, "gate": not has_blocking_issues(issues)}


class DirectSaveBody(BaseModel):
    direct: dict[str, dict[str, Any]] = {}      # {sid: {size, cam{kind,dir,amp,speed}, motion, light}}


@router.post("/ep/{book_id}/{ep}/direct/save")
def v3_direct_save(book_id: str, ep: int, body: DirectSaveBody):
    """保存 ④ 导演参数 (UI 手改或生成结果): 固化执行参数 + 复检 + 闸态。"""
    from app.services.anim_pipeline.v3_pipeline import direct as v3_direct
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        shots = {s["sid"]: s for s in (v3.get("shots") or [])}
        merged = dict(v3.get("direct") or {})
        for sid, d in body.direct.items():
            if sid in shots and d:
                old_en = str((merged.get(sid) or {}).get("motion_en") or "")
                old_light = str((merged.get(sid) or {}).get("light_en") or "")
                c = d.get("cam") or {}
                merged[sid] = {"size": str(d.get("size") or "中景"),
                               "cam": v3_direct.law_cam({
                                   "kind": str(c.get("kind") or "固定"),
                                   "dir": str(c.get("dir") or ""),
                                   "amp": str(c.get("amp") or ""),
                                   "speed": str(c.get("speed") or "")}),
                               "motion": str(d.get("motion") or "")[:120],
                               "light": str(d.get("light") or "")[:80]}
                if old_en:      # 已英文化的镜: 手改只动 cam/中文域, 英译不丢
                    merged[sid]["motion_en"] = old_en
                if old_light:   # 批4 光影槽同款保真 (手改不清 light_en)
                    merged[sid]["light_en"] = old_light
        # 执行参数随镜固化 (步数/工位/英文句 — 程序独占, LLM 无感)
        for sid, d in merged.items():
            sh = shots.get(sid)
            if sh and d:
                d["steps"] = v3_direct.steps_for(sh)
                d["station"] = v3_direct.station_for(d.get("cam"))
                d["clause"] = v3_direct.cam_clause(d.get("cam"),
                                                   str(d.get("motion_en") or ""))
        issues = v3_direct.validate(merged, list(shots.values()))
        v3["direct"] = merged
        v3["direct_issues"] = issues
        blocking = any(k != "弧级" for k in issues)
        v3.setdefault("gates", {})["direct"] = not blocking and len(merged) >= len(shots)
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"saved": len(merged), "issues": issues,
            "gate": v3["gates"]["direct"]}


class DirectRerollBody(BaseModel):
    sid: str


@router.post("/ep/{book_id}/{ep}/direct/reroll")
def v3_direct_reroll(book_id: str, ep: int, body: DirectRerollBody):
    """④ 单镜重掷: 只重生成该镜四元组 (一镜一批, 便宜), 固化执行参数后返回。"""
    from app.services.anim_pipeline.v3_pipeline import direct as v3_direct
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        shots = v3.get("shots") or []
        d = v3_direct.reroll_one(shots, v3.get("arcs") or [], body.sid)
        if not d:
            raise HTTPException(status_code=422, detail=f"镜不存在或未产出: {body.sid}")
        for k in ("steps", "station", "clause"):
            d.pop(k, None)
        merged = dict(v3.get("direct") or {})
        merged[body.sid] = d
        v3["direct"] = merged
        # 批5: 重掷后单镜重译 — 新四元组的 motion/light 需要新 motion_en/light_en
        # (旧态覆盖 entry 丢 motion_en 且无恢复通道, 批4 empty_motion 闸拦死)
        from app.services.anim_pipeline.v3_pipeline.redo import retranslate
        retranslate(doc, [body.sid])
        d["steps"] = v3_direct.steps_for(next(s for s in shots if s["sid"] == body.sid))
        d["station"] = v3_direct.station_for(d.get("cam"))
        d["clause"] = v3_direct.cam_clause(d.get("cam"),
                                           str(d.get("motion_en") or ""))
        v3["direct_issues"] = v3_direct.validate(merged, shots)
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"sid": body.sid, "direct": d, "issues": v3["direct_issues"]}


class CamClauseItem(BaseModel):
    sid: str = ""
    cam: dict[str, Any] = {}
    motion_en: str = ""


class CamClauseBody(BaseModel):
    items: list[CamClauseItem]


@router.post("/cam_clause")
def v3_cam_clause(body: CamClauseBody):
    """④ 页运镜句预览单源 (1107 批1): 四要素装配只在 direct.cam_clause 一家,
    前端平行实现退役 — 与 ⑥ 拼合/⑧ 注入同句同源。"""
    from app.services.anim_pipeline.v3_pipeline import direct as v3_direct
    return {"clauses": {it.sid or str(i): v3_direct.cam_clause(it.cam, it.motion_en)
                        for i, it in enumerate(body.items)}}


@router.post("/ep/{book_id}/{ep}/direct/finalize")
def v3_direct_finalize(book_id: str, ep: int):
    """④ 放行动作: 英文化批 (ID-131) + 冻结戳 (③④ 此后转只读)。幂等。"""
    from app.services.anim_pipeline.v3_pipeline import direct as v3_direct
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        try:
            out = v3_direct.finalize(doc)
        except SystemExit as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return out


# ── ⑤ K2 批生图 (V6 薄壳) ─────────────────────────────────────────────

@router.get("/ep/{book_id}/{ep}/k2")
def v3_k2_get(book_id: str, ep: int):
    """⑤ 数据源: 每镜 k2 状态 (image/status/seed/text_suspect) + 闸态。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        doc = _load_v3_doc(book_title, ep)
    except FileNotFoundError:
        return {"shots": [], "gates": {}, "frozen": False, "ready": False}
    v3 = doc.get("v3") or {}
    direct = v3.get("direct") or {}
    out = []
    for s in (v3.get("shots") or []):
        k2s = s.get("k2") or {}
        d = direct.get(s["sid"]) or {}
        img = k2s.get("image")
        # image_url 由后端算 (实锤: 品牌图在 ep 目录之外 ../../_资产/, /file/ 按
        # 路径安全 404 → 品牌镜显示未出图)
        if img and s.get("brand_card"):
            url = f"/api/anim/v3/ep/{book_id}/{ep}/k2/brand-image"
        elif img:
            url = f"/api/anim/ep/{book_id}/{ep}/file/{img}"
        else:
            url = None
        out.append({"sid": s["sid"], "est_s": s.get("est_s"),
                    "size": d.get("size"), "cam": (d.get("cam") or {}).get("kind"),
                    "brand": bool(s.get("brand_card")),
                    "muted": bool(s.get("audio_muted")),
                    "image": img, "image_url": url,
                    "status": k2s.get("status") or "planned",
                    "seed": k2s.get("seed"), "suspect": bool(k2s.get("text_suspect")),
                    "error": k2s.get("error")})
    return {"shots": out, "gates": v3_pipeline.gates_view(doc),
            "frozen": bool(v3.get("frozen_at")), "ready": bool(out)}


class K2ApproveBody(BaseModel):
    sids: list[str] = []
    all: bool = False


@router.get("/ep/{book_id}/{ep}/k2/brand-image")
def v3_k2_brand_image(book_id: str, ep: int):
    """品牌镜首帧 = 书级品牌 keyframe (在 _资产/ 目录, ep 目录之外, 专用端点取)。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    from app.services.anim_pipeline import brand as brand_mod
    from app.services.anim_pipeline import shots as shots_mod
    from pathlib import Path as _P
    p = shots_mod.ep_dir(book_title, ep) / brand_mod.resolve(book_title, ep, "keyframe")
    if not p.exists():
        raise HTTPException(status_code=404, detail="品牌 keyframe 不存在 — 先跑品牌卡")
    return FileResponse(str(p))


@router.get("/ep/{book_id}/{ep}/h3/brand-video")
def v3_h3_brand_video(book_id: str, ep: int):
    """品牌镜定稿视频 (1106 用户实锤: ⑧ 页品牌卡无播放键) — 资产在 _资产/
    目录外, /file/ 拦穿越, 仿 brand-image 专用端点取。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    from app.services.anim_pipeline import brand as brand_mod
    from app.services.anim_pipeline import shots as shots_mod
    from pathlib import Path as _P
    p = shots_mod.ep_dir(book_title, ep) / brand_mod.resolve(book_title, ep, "video")
    if not p.exists():
        raise HTTPException(status_code=404, detail="品牌定稿视频不存在 — 先跑品牌卡")
    return FileResponse(str(p))


@router.post("/ep/{book_id}/{ep}/k2/approve")
def v3_k2_approve(book_id: str, ep: int, body: K2ApproveBody):
    """⑤ 人检过审: img_done → approved (v2 语义, v3 命名空间)。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        v3 = doc.setdefault("v3", {})
        n = 0
        for s in (v3.get("shots") or []):
            k2s = s.get("k2") or {}
            want = body.all or s["sid"] in body.sids
            if want and k2s.get("status") == "img_done":
                k2s["status"] = "approved"
                n += 1
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"approved": n}


# ── ⑥ TTS (V6 薄壳: 复用音频线, 只读展示 + 试听 + 深链) ───────────────

@router.get("/ep/{book_id}/{ep}/tts")
def v3_tts_get(book_id: str, ep: int):
    """⑥ 数据源: ep 音频包 (验收/钉选优先) — 复用 anim_draft.fetch_episode_audio。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    from app.services.anim_draft import fetch_episode_audio, AnimNotFound as _ANF
    try:
        audio = fetch_episode_audio(book_id, ep)
    except Exception as exc:  # noqa: BLE001 — 无音频不炸页
        return {"has_audio": False, "reason": str(exc)[:200], "blocks": []}
    blocks = [{"i": i, "id": b.get("id"), "seg": bool(b.get("segment_id")),
               "text": str(b.get("text") or "")[:120],
               "dur_s": b.get("dur_s"), "file": str(b.get("file") or "")}
              for i, b in enumerate(audio.get("files") or [])]
    # 完整段落 (full_paragraph.wav): fetch_episode_audio 按时间轴纯度剔除它,
    # 这里单独回传试听信息 (combined 行 = 无 segment_id 的 AudioFile)
    import wave as _w
    from contextlib import closing as _closing
    from app.database import get_session_maker as _gsm
    from app.models import AudioFile as _AF
    combined = None
    try:
        with _closing(_gsm()()) as db:
            row = (db.query(_AF)
                   .filter(_AF.audio_job_id == audio.get("job_id"),
                           _AF.segment_id.is_(None),
                           _AF.filename == "full_paragraph.wav").first())
            if row is not None:
                p = Path(row.file_path)
                if p.exists():
                    dur = row.duration or 0
                    if not dur:
                        with _w.open(str(p), "rb") as _wf:
                            dur = _wf.getnframes() / float(_wf.getframerate() or 1)
                    combined = {"dur_s": round(float(dur), 2)}
    except Exception as _exc:  # noqa: BLE001 — 大段信息缺不炸页 (但要留痕)
        import logging as _lg
        _lg.getLogger(__name__).warning("[v3:tts] combined 元数据失败: %s", _exc)
        combined = None
    # 槽状态自愈: 缺块的槽标 audio_muted (屏一块 → 该槽 K2/H3/盖章全跳过)
    from app.services.anim_pipeline.v3_pipeline.tts_slot import sync_muted_slots
    with anim_service.ep_guard(book_id, ep):
        doc = _load_v3_doc(book_title, ep)
        before = {str(s["sid"]): bool(s.get("audio_muted"))
                  for s in (doc.get("v3") or {}).get("shots") or []}
        muted, drifted = sync_muted_slots(doc, blocks)
        _changed = (muted != before) or any(drifted.values())
        if _changed:
            from app.services.anim_pipeline import shots as shots_mod
            shots_mod.save(doc)
    # 块→槽锚匹配 (展示层: 每块标所属槽)
    from app.services.anim_pipeline.v3_pipeline.tts_slot import _anchor
    v3 = doc.get("v3") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    slot_anchor = {}
    for s in (v3.get("shots") or []):
        refs = s.get("line_refs") or []
        first = lines.get(refs[0], {}) if refs else {}
        slot_anchor[_anchor(first.get("text"))] = str(s["sid"])
    for b in blocks:
        b["sid"] = slot_anchor.get(_anchor(b.get("text")), "")
    return {"has_audio": True, "job_id": audio.get("job_id"),
            "pinned": bool(audio.get("pinned")), "blocks": blocks,
            "combined": combined, "muted_slots": muted, "drifted_slots": drifted,
            "total_s": round(sum(float(b.get("dur_s") or 0) for b in blocks), 1)}


@router.get("/ep/{book_id}/{ep}/tts/combined")
def v3_tts_combined(book_id: str, ep: int):
    """⑥ 完整段落试听: full_paragraph.wav (输出目录直取)。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    from app.services.anim_draft import fetch_episode_audio
    from pathlib import Path as _P
    audio = fetch_episode_audio(book_id, ep)
    p = _P(str(audio.get("output_dir") or "")) / "full_paragraph.wav"
    if not p.exists():
        raise HTTPException(status_code=404, detail="full_paragraph.wav 不存在 — 先重拼/重生成")
    return FileResponse(str(p))


@router.post("/ep/{book_id}/{ep}/tts/generate")
def v3_tts_generate(book_id: str, ep: int, background_tasks: BackgroundTasks):
    """⑥ 按槽重生成 TTS (D 刀): 段表按槽重切 → 复用音频线合成 → 钉选新包。

    块[i] ↔ 槽[i] 1:1; 旧包保留 (音频工坊可回钉回滚)。
    """
    from fastapi import BackgroundTasks as _BT  # noqa: F401 — 上面参数已注入
    from contextlib import closing as _closing
    from app.database import get_session_maker as _gsm, db_session as _dbs
    from app.models.book import Episode
    from app.models.content import Script, Voice, Host, AudioJob
    from app.models.roles import Persona
    from app.services.anim_pipeline.v3_pipeline.tts_slot import resegment_by_slots, slot_texts
    from app.routers.audio import _do_tts
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    doc = _load_v3_doc(book_title, ep)
    slots = slot_texts(doc)
    if not slots:
        raise HTTPException(status_code=422, detail="未切镜 — 先跑 ③ 分镜文字")
    with _dbs() as db:
        episode = db.query(Episode).filter(
            Episode.book_id == book_id, Episode.ep_index == ep).first()
        if not episode or not episode.script_id:
            raise HTTPException(status_code=422, detail="本集无产线 script_id")
        script = db.query(Script).filter(Script.id == episode.script_id).first()
        if not script:
            raise HTTPException(status_code=404, detail="script 不存在")
        # 音色解析 (与 audio.generate_audio 同序: persona → host 默认 → host 首条)
        voice = None
        persona = db.query(Persona).filter(Persona.host_id == script.host_id).first()
        if persona and persona.voice_id:
            voice = db.query(Voice).filter(Voice.id == persona.voice_id).first()
        if not voice and script.host_id:
            host = db.query(Host).filter(Host.id == script.host_id).first()
            if host and host.default_voice_id:
                voice = db.query(Voice).filter(Voice.id == host.default_voice_id).first()
        if not voice and script.host_id:
            voice = db.query(Voice).filter(Voice.host_id == script.host_id).first()
        n = resegment_by_slots(db, script.id, slots)
        from pathlib import Path as _P
        import time as _time
        output_dir = (_P(script.project_dir) / "audio" if script.project_dir else
                      _P("E:/数字人计划/outputs/audio") / _time.strftime("%Y-%m-%d")
                      / (voice.name if voice else "default")
                      / f"v3slots_{int(_time.time())}")
        output_dir.mkdir(parents=True, exist_ok=True)
        job = AudioJob(script_id=script.id,
                       voice_id=voice.id if voice else None,
                       output_dir=str(output_dir), status="pending",
                       total_segments=n, completed_segments=0)
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id                      # session 内取值 (关外访问=Detached 500 实锤)
        voice_name = voice.name if voice else None
        episode.audio_job_id = job_id         # 钉选新包 (旧包 fetch 自动退位)
        db.commit()
    background_tasks.add_task(_do_tts, job_id)
    return {"job_id": job_id, "slots": n, "voice": voice_name}


@router.get("/ep/{book_id}/{ep}/tts/file/{idx}")
def v3_tts_file(book_id: str, ep: int, idx: int):
    """⑥ 试听: 按块索引取音频文件 (FileResponse, 范围请求由客户端处理)。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    from app.services.anim_draft import fetch_episode_audio
    from pathlib import Path as _P
    audio = fetch_episode_audio(book_id, ep)
    files = audio.get("files") or []
    if idx < 0 or idx >= len(files):
        raise HTTPException(status_code=404, detail=f"块不存在: {idx}")
    p = _P(str(files[idx].get("file") or ""))
    if not p.exists():
        raise HTTPException(status_code=404, detail="音频文件缺失")
    return FileResponse(str(p))


# ── ⑦ 盖章 (V5: 查表灌秒 + v2 回填) ────────────────────────────────────

@router.get("/ep/{book_id}/{ep}/stamp")
def v3_stamp_get(book_id: str, ep: int):
    """⑦ 数据源: 槽实测时长 (对齐音频) + 卡时窗 + 屏弃 + 盖章态。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        doc = _load_v3_doc(book_title, ep)
    except FileNotFoundError:
        return {"shots": [], "gates": {}, "stamped": False}
    from app.services.anim_pipeline.v3_pipeline.stamp import align_blocks
    from app.services.anim_draft import fetch_episode_audio
    v3 = doc.get("v3") or {}
    shots = v3.get("shots") or []
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    direct = v3.get("direct") or {}
    blocks = []
    try:
        blocks = fetch_episode_audio(book_id, ep).get("files") or []
    except Exception:  # noqa: BLE001 — 无音频页照常, 状态栏提示
        pass
    aligned = align_blocks(shots, lines, blocks) if blocks else {}
    n_live = sum(1 for s in shots if not s.get("audio_muted"))
    out = []
    for s in shots:
        sid = str(s["sid"])
        blk = aligned.get(sid)
        d = direct.get(sid) or {}
        cards = [c for c in (v3.get("cards") or []) if c.get("sid") == sid]
        out.append({"sid": sid, "arc_id": s.get("arc_id"),
                    "arc_type": next((a.get("type") for a in (v3.get("arcs") or [])
                                      if a.get("arc_id") == s.get("arc_id")), ""),
                    "est_s": s.get("est_s"),
                    "real_s": blk.get("dur_s") if blk else None,
                    "muted": bool(s.get("audio_muted")),
                    "size": d.get("size"), "cam": (d.get("cam") or {}).get("kind"),
                    "cards": [{"cid": c.get("cid"), "kind": c.get("kind"),
                               "text": str(c.get("text") or "")[:24]} for c in cards],
                    "image": (s.get("k2") or {}).get("image")})
    st = v3.get("stamp") or {}
    return {"shots": out, "gates": v3_pipeline.gates_view(doc),
            "stamped": bool(st.get("at")), "stamp": st,
            "audio_aligned": len(aligned) == n_live if blocks else False,
            "has_audio": bool(blocks)}


# ── ⑧ H3 (V6b: 盖章后的 doc.shots 即 v2 形状, h3 直接吃) ────────────────

@router.get("/ep/{book_id}/{ep}/h3")
def v3_h3_get(book_id: str, ep: int):
    """⑧ 数据源: 盖章后的镜 (status/video/camera/dur) + 闸态。"""
    try:
        book_id, book_title = anim_service.resolve_ep(book_id, ep)
    except anim_service.AnimNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _line_gate(book_title)
    try:
        doc = _load_v3_doc(book_title, ep)
    except FileNotFoundError:
        return {"shots": [], "gates": {}, "stamped": False}
    v3 = doc.get("v3") or {}
    direct = v3.get("direct") or {}
    out = []
    for s in (doc.get("shots") or []):
        d = direct.get(str(s.get("shot_id"))) or {}
        vf = s.get("video_file")
        img = s.get("image_file")
        # image_url 后端算 (品牌图在 ep 目录外 ../../_资产/, /file/ 拦穿越 → 404 实锤)
        if img and s.get("brand_card"):
            url = f"/api/anim/v3/ep/{book_id}/{ep}/k2/brand-image"
        elif img:
            url = f"/api/anim/ep/{book_id}/{ep}/file/{img}"
        else:
            url = None
        out.append({"sid": str(s.get("shot_id")),
                    "status": s.get("status"),
                    "brand": bool(s.get("brand_card")),
                    "size": d.get("size"), "cam": (d.get("cam") or {}).get("kind"),
                    "dur_s": (s.get("anim") or {}).get("duration_s"),
                    "image": img, "image_url": url,
                    "video": vf, "error": str(s.get("error") or "")[:200] or None,
                    # 品牌视频走专用端点 (资产在 ep 目录外, /file/ 拦穿越)
                    "video_url": (f"/api/anim/v3/ep/{book_id}/{ep}/h3/brand-video"
                                  if (s.get("brand_card") and vf) else None)})
    return {"shots": out, "gates": v3_pipeline.gates_view(doc),
            "stamped": bool((v3.get("stamp") or {}).get("at"))}


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
        from app.services.anim_pipeline.v3_pipeline.shots import has_blocking_issues
        from app.services.anim_pipeline.v3_pipeline.shots import text_layer_issues
        issues = text_layer_issues(list(cur.values()))
        v3["shot_issues"] = issues
        v3.setdefault("gates", {})["shots"] = not has_blocking_issues(issues)
        from app.services.anim_pipeline import shots as shots_mod
        shots_mod.save(doc)
    return {"saved": len(body.shots), "edited": n_edit,
            "issues": issues, "gate": not has_blocking_issues(issues)}


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
            # 批3 vocab 白名单: 实体词表可编辑 (旧态两头都存不进 — 前端不发/
            # 后端白名单外忽略); 对结构归一与 ② 产出同尺
            if row.get("vocab") is not None:
                from app.services.anim_pipeline.v3_pipeline.arcs import _norm_vocab
                vv = _norm_vocab(row["vocab"])
                if vv != (a.get("vocab") or []):
                    a["vocab"] = vv
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
