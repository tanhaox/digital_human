# -*- coding: utf-8 -*-
"""PPT 出片产线 router (2026-08-20) — 纯 PPT 视频 MVP.

流程:
  上传 pptx → 解析每页 {文字, 图, 备注台词}
  → 逐页备注台词建 Script (每页台词 = 一个 segment)
  → 复用 TTS (audio 链路) 合成语音
  → 对齐 (TTS 时长快路径) 得每页 start/end
  → 逐页 Chrome+ffmpeg 渲染 mp4
  → concat 拼接 + 主音轨合成 → 成片

2026-09-01 拆包: 编排/任务状态/SSE 事件下沉 app/services/ppt_pipeline.py
(函数体原样搬运零行为变更), 本文件只留 HTTP handler.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse

from app.config import get_config
from app.services.ppt_pipeline import (
    _JOBS,
    _evt,
    _job_workdir,
    _run_ppt_pipeline,
)

router = APIRouter(prefix="/api/ppt", tags=["ppt"])

__all__ = ["router"]


@router.post("/upload")
async def upload_ppt(
    file: UploadFile = File(...),
    book_id: str | None = Form(None),
    ep_index: int | None = Form(None),
    voice_id: str | None = Form(None),
):
    """上传 pptx → 解析 → 返回 {job_id, slides 概要}.

    book_id/ep_index (2026-08-21): 绑定拆书系列, 供系列皮肤包对齐.
    voice_id (2026-08-22): 前端选的音色, 传入产线 TTS (此前固定用书账号音色静姐).
    """
    if not file.filename.lower().endswith(".pptx"):
        raise HTTPException(400, "仅支持 .pptx 文件")

    job_id = str(uuid.uuid4())
    workdir = _job_workdir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "source.pptx"
    content = await file.read()
    src.write_bytes(content)

    from app.services.ppt_service import parse_pptx
    try:
        slides = parse_pptx(src)
    except Exception as exc:
        raise HTTPException(400, f"PPT 解析失败: {exc}")

    _JOBS[job_id] = {
        "status": "uploaded", "slides": len(slides),
        "book_id": book_id, "ep_index": ep_index, "voice_id": voice_id,
        "events": [{"ts": time.strftime("%H:%M:%S"), "level": "ok",
                    "msg": f"解析成功: {len(slides)} 页"}],
    }
    preview = [
        {"index": s.index, "notes_len": len(s.notes), "text_preview": (s.texts[0] if s.texts else "")[:30]}
        for s in slides
    ]
    # 查同书是否已有母本皮肤包
    has_master = False
    master_ep = None
    if book_id:
        from app.services.skin_pack_service import load_skin
        skin = load_skin(book_id)
        if skin:
            has_master = True
            master_ep = skin.master_ep
    return {"job_id": job_id, "slides": len(slides), "preview": preview,
            "book_id": book_id, "ep_index": ep_index,
            "has_master": has_master, "master_ep": master_ep}


@router.post("/{job_id}/mark-master")
def mark_master(job_id: str):
    """把本 job 的 pptx 定为该 book 的母本, 抽系列皮肤包落盘."""
    j = _JOBS.get(job_id)
    if not j:
        raise HTTPException(404, f"任务不存在: {job_id}")
    book_id = j.get("book_id")
    ep_index = j.get("ep_index")
    if not book_id or ep_index is None:
        raise HTTPException(400, "未绑定 book_id/ep_index, 无法定为母本 (从拆书讲书页进产线上传)")
    from app.services.skin_pack_service import extract_master
    try:
        pack = extract_master(_job_workdir(job_id), book_id, ep_index)
    except Exception as exc:
        raise HTTPException(400, f"母本抽取失败: {exc}")
    return {"book_id": book_id, "master_ep": ep_index,
            "color_tokens": pack.color_tokens[:5],
            "fontsize_tokens": pack.fontsize_tokens[:6],
            "background": pack.background_path}


@router.get("/series-skin/{book_id}")
def series_skin(book_id: str):
    """查询书系列是否已有母本皮肤包 (前端展示用)."""
    from app.services.skin_pack_service import load_skin
    skin = load_skin(book_id)
    if not skin:
        return {"has_master": False}
    return {"has_master": True, "master_ep": skin.master_ep,
            "color_tokens": skin.color_tokens[:5]}


@router.post("/{job_id}/render")
def render(job_id: str, render_mode: str = Form("jy2"), voice_id: str | None = Form(None)):
    """触发 PPT 产线后台线程 (TTS → 对齐 → 渲染 → 拼片).

    render_mode (2026-08-21):
      - jy2 默认: 元素级拆解 → 剪映多轨草稿 (逐元素动画/字幕/音效/音频)
      - jy        : 整页静态帧 → 剪映草稿 (旧)
      - auto      : 整页静态帧 → zoompan mp4 → 自动拼片 ppt_final.mp4
      - anim      : 旧路径 Playwright 逐帧捕获 (每页 N 帧, 慢, 短片才值得)
    """
    j = _JOBS.get(job_id)
    if not j:
        raise HTTPException(404, f"任务不存在: {job_id}")
    if j["status"] == "running":
        raise HTTPException(409, "任务运行中")
    # 稿↔页单一致性闸 (0910 用户实锤: 改稿后重渲, 渲染吃旧快照 = 旧稿复辟)。
    # 页单 narration 是规划时切片快照; 集稿后续改动若未同步, 这里拦下提示重新规划。
    if j.get("mode") == "bs1" and j.get("status") in ("confirmed", "done"):
        from app.database import db_session as _dbs2
        from app.services.book_service.slide_render import load_pages as _lp
        from app.services.ppt_pipeline import _job_workdir as _wd2
        _db2 = _dbs2().__enter__()
        try:
            from app.models import Episode
            import re as _re
            _ep = _db2.query(Episode).filter(
                Episode.book_id == j["book_id"], Episode.ep_index == int(j["ep_index"])).first()
            _script = (getattr(_ep, "script_text", "") or "").strip()
            _pages = _lp(_wd2(job_id))
            _narr = chr(10).join((p.get("narration") or "") for p in (_pages or []))
            _norm = lambda s: _re.sub(r"\s+", "", s or "")
            if _script and _pages and _norm(_script) != _norm(_narr):
                # 0910 流程闭环 v2 (用户令: 不要提示要动作): 稿≠快照 → 自动抓新稿重新规划,
                # 规划完成落 planned 交人工确认 — 确认后才能再点生成视频 (人审环不跳)。
                _npj = str(uuid.uuid4())
                _JOBS[_npj] = {
                    "mode": "bs1", "status": "planning", "slides": 0,
                    "book_id": j["book_id"], "ep_index": j["ep_index"],
                    "voice_id": j.get("voice_id"),
                    "events": [{"ts": time.strftime("%H:%M:%S"), "level": "ok",
                                 "msg": "检测到新稿 — 自动重新规划页单 (完成后请逐页检查并确认)"}],
                }
                def _autorun():
                    from app.services.book_service.slide_render import run_bs1_plan
                    run_bs1_plan(_npj, j["book_id"], int(j["ep_index"]))
                threading.Thread(target=_autorun, daemon=True).start()
                return {"job_id": _npj, "status": "planning", "auto_replanned": True,
                        "message": "集稿已更新 — 已自动抓新稿重新规划页单, 完成后请逐页检查并「✔ 确认页单」, 再生成视频"}
        finally:
            _db2.close()
    if j.get("mode") == "bs1" and j.get("status") not in ("confirmed", "done"):
        # done 可重渲 (0910): UI 在 done 态给「重新生成视频」, 旧闸只认 confirmed
        # 自己拦自己; 页单早已确认落库 (bs1_pages_json), 重渲安全
        raise HTTPException(400, "bs1 页单需先确认 (confirm) 再生成视频")
    mode = render_mode if render_mode in ("jy2", "jy", "auto", "anim") else "jy2"
    # bs1 重渲音色时效 (0910): job 的 voice_id 是规划时快照, 换引擎/换音色后重渲
    # 应跟 persona 现役音色 — 每次渲染前按书重新解析 (显式传参优先)
    if j.get("mode") == "bs1" and j.get("book_id"):
        from app.database import db_session as _dbs
        _db = _dbs().__enter__()
        try:
            from app.models import BookProject, Persona
            _b = _db.query(BookProject).filter(BookProject.id == j["book_id"]).first()
            _pid = (_b.input_json or {}).get("persona_id") if isinstance(_b.input_json, dict) else None
            _p = _db.query(Persona).filter(Persona.id == _pid).first() if _pid else None
            if _p and _p.voice_id:
                j["voice_id"] = _p.voice_id
        finally:
            _db.close()
    if voice_id:
        j["voice_id"] = voice_id
    j.update(status="running", done=[], error=None, progress={}, cancel=False,
             render_mode=mode)
    threading.Thread(target=_run_ppt_pipeline, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "status": "running", "render_mode": mode}


@router.post("/{job_id}/cancel")
def cancel(job_id: str):
    """取消运行中产线 (TTS 完成/逐页渲染间检查 cancel 标志)."""
    j = _JOBS.get(job_id)
    if not j:
        raise HTTPException(404, f"任务不存在: {job_id}")
    j["cancel"] = True
    _evt(job_id, "取消请求已接收, 将在本页渲染完成后停止", "warn")
    return {"job_id": job_id, "status": "cancelling"}


# ── bs1 页单产线 (2026-09-08 老谭读书): 稿件→页单→确认→render③④⑤ ──
def _bs1_job(job_id: str) -> dict:
    j = _JOBS.get(job_id)
    if not j or j.get("mode") != "bs1":
        raise HTTPException(404, f"bs1 任务不存在: {job_id}")
    if j.get("status") == "running":
        raise HTTPException(409, "音视频生成中, 不可编辑页单")
    return j


def _bs1_ctx(db, book_id: str, ep_index: int):
    """persona 闸门: 仅老谭读书线 (laotan-book) 可走 bs1 (两线不互通铁律)."""
    from app.models import BookProject, Persona
    book = db.query(BookProject).filter(BookProject.id == book_id).first()
    if not book:
        raise HTTPException(404, "书不存在")
    pid = (book.input_json or {}).get("persona_id") if isinstance(book.input_json, dict) else None
    persona = db.query(Persona).filter(Persona.id == pid).first() if pid else None
    if not persona or "laotan" not in (persona.prompt_template or ""):
        raise HTTPException(400, "bs1 产线仅限老谭读书线 (静读书请走 pptx 上传链路)")
    return book


def _pages_public(job_id: str, pages: list[dict]) -> list[dict]:
    out = []
    for i, pd in enumerate(pages):
        q = dict(pd)
        q["idx"] = i
        q["png_url"] = f"/api/ppt/bs1/{job_id}/pages/{i}/png" if pd.get("png") else ""
        out.append(q)
    return out


@router.post("/bs1/plan")
def bs1_plan(book_id: str = Form(...), ep_index: int = Form(...),
             voice_id: str | None = Form(None)):
    """① PPT生产: 稿件 → A 规划 → Pexels 配图 → 逐页预览 PNG (后台线程, SSE 看进度)."""
    from app.database import db_session
    db = db_session().__enter__()
    try:
        _bs1_ctx(db, book_id, ep_index)
    finally:
        db.close()
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "mode": "bs1", "status": "planning", "slides": 0,
        "book_id": book_id, "ep_index": ep_index, "voice_id": voice_id,
        "events": [{"ts": time.strftime("%H:%M:%S"), "level": "ok", "msg": "bs1 页单规划启动"}],
    }
    def _run():
        from app.services.book_service.slide_render import run_bs1_plan
        run_bs1_plan(job_id, book_id, ep_index)
    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "planning"}


@router.get("/bs1/state")  # 须在 /bs1/{job_id} 之前声明 (FastAPI 顺序匹配)
def bs1_state(book_id: str, ep_index: int):
    """查/恢复书的 bs1 页单任务: 内存 job 优先, 丢了从 episodes.bs1_pages_json 重建."""
    from app.database import db_session
    from app.services.ppt_pipeline import _JOBS, _job_workdir
    from app.services.book_service.slide_render import load_pages
    db = db_session().__enter__()
    try:
        _bs1_ctx(db, book_id, ep_index)  # persona 闸门同 plan
        from app.models import Episode
        ep = db.query(Episode).filter(Episode.book_id == book_id,
                                      Episode.ep_index == ep_index).first()
        if not ep:
            raise HTTPException(404, "集不存在")
        snap = ep.bs1_pages_json if isinstance(ep.bs1_pages_json, dict) else {}
        old_job = snap.get("job_id") or ""
        latest = None  # 多 job 同书同集 (重新规划后旧 job 仍在内存) → 取最新
        for jid, j in _JOBS.items():
            if (j.get("mode") == "bs1" and j.get("book_id") == book_id
                    and j.get("ep_index") == ep_index):
                latest = (jid, j)  # dict 保插入序, 后插 (新规划) 覆盖
        if latest:
            jid, j = latest
            return {"job_id": jid, "status": j.get("status"),
                    "book_id": book_id, "ep_index": ep_index,
                    "has_confirmed": bool(snap),
                    "draft": j.get("draft"),
                    "pages": _pages_public(jid, j.get("pages") or [])}
        if old_job:  # 内存丢: 快照/落盘 job 统一比 workdir mtime 取最新 (重新规划后旧快照不得掩盖新页单)
            from app.services.book_service.slide_render import load_meta
            candidates: list[tuple[float, str, dict, str]] = []  # (mtime, jid, pages, status)
            snap_wd = _job_workdir(old_job)
            if (snap_wd / "pages.json").exists():
                st = "confirmed"
                draft = None
                if (snap_wd / "ppt_manifest.json").exists():
                    st = "done"
                    draft = {"draft_name": "已生成 (重启后名称丢失, 打开剪映草稿箱找最新 PPT_* 草稿)"}
                candidates.append((snap_wd.joinpath("pages.json").stat().st_mtime,
                                   old_job, load_pages(snap_wd) or snap.get("pages") or [], st))
                if draft:
                    _JOBS[old_job] = {"mode": "bs1", "status": st, "draft": draft,
                                      "book_id": book_id, "ep_index": ep_index,
                                      "pages": candidates[-1][2], "voice_id": None, "events": []}
            root = Path(get_config().defaults.ppt_work_root)
            for mf in root.glob("*/bs1_meta.json"):
                try:
                    meta = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if (meta.get("book_id") == book_id and int(meta.get("ep_index") or 0) == int(ep_index)
                        and meta.get("job_id")):
                    jid = str(meta["job_id"])
                    pgs = load_pages(_job_workdir(jid)) or []
                    if pgs:
                        candidates.append((mf.stat().st_mtime, jid, pgs,
                                           meta.get("status") or "planned"))
            if candidates:
                _, jid, pages, st = max(candidates, key=lambda t: t[0])
                _JOBS[jid] = {"mode": "bs1", "status": st,
                              "book_id": book_id, "ep_index": ep_index,
                              "pages": pages, "voice_id": None, "events": []}
                return {"job_id": jid, "status": st,
                        "book_id": book_id, "ep_index": ep_index,
                        "has_confirmed": bool(snap) and jid == old_job,
                        "pages": _pages_public(jid, pages)}
        return {"job_id": None, "status": None, "book_id": book_id,
                "ep_index": ep_index, "has_confirmed": False, "pages": []}
    finally:
        db.close()


@router.get("/bs1/{job_id}")
def bs1_detail(job_id: str):
    j = _bs1_job(job_id)
    return {"job_id": job_id, "status": j.get("status"),
            "book_id": j.get("book_id"), "ep_index": j.get("ep_index"),
            "draft": j.get("draft"), "pages": _pages_public(job_id, j.get("pages") or [])}


@router.get("/bs1/{job_id}/pages/{idx}/png")
def bs1_page_png(job_id: str, idx: int):
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not (0 <= idx < len(pages)) or not pages[idx].get("png"):
        raise HTTPException(404, "页 PNG 不存在")
    from app.services.ppt_pipeline import _job_workdir
    path = (_job_workdir(job_id) / pages[idx]["png"]).resolve()
    if not path.exists():
        raise HTTPException(404, "页 PNG 文件缺失")
    return FileResponse(path, media_type="image/png")


def _bs1_rerender(job_id: str, idx: int, refetch_image: bool,
                  new_query: str = "") -> dict:
    """单页重渲染公共尾巴: (可选)重拉图 → render_bs1_pages(单页) → 落盘+回 job。"""
    from app.services.book_service.slide_render import (
        fetch_page_image, load_pages, render_bs1_pages, _write_pages,
    )
    from app.services.ppt_pipeline import _job_workdir
    j = _bs1_job(job_id)
    pages = j.get("pages") or load_pages(_job_workdir(job_id))
    if not (0 <= idx < len(pages)):
        raise HTTPException(404, f"页不存在: {idx}")
    pd = pages[idx]
    if refetch_image:
        q = new_query or pd.get("img_query") or ""
        avoid = {pd.get("img_pexels_id") or 0} if new_query else set()
        rel, pid = fetch_page_image(q, _job_workdir(job_id), avoid)
        if rel:  # 失败保留原图 (不清空), 仅成功才覆盖
            pd["img_file"], pd["img_pexels_id"] = rel, pid
        if not new_query:
            pd["img_query"] = q
    from app.database import db_session

    from app.services.book_service.slide_render import build_context
    # 换页型/重排会增删 chapter → 章号重编 + 目录重对齐 (目录变了连带重渲染 P2)
    rerender_idx = [idx]
    if _renumber_chapters(pages):
        if idx != 1 and len(pages) > 1 and pages[1]["type"] == "contents":
            rerender_idx.append(1)
    db = db_session().__enter__()
    try:
        ctx = build_context(db, j["book_id"], j["ep_index"])
    finally:
        db.close()
    ok = render_bs1_pages(pages, ctx, _job_workdir(job_id), only=rerender_idx)
    _write_pages(_job_workdir(job_id), pages)
    j["pages"] = pages
    pd2 = pages[idx]
    return {"idx": idx, "page": {**pd2, "idx": idx,
            "png_url": f"/api/ppt/bs1/{job_id}/pages/{idx}/png" if pd2.get("png") else ""},
            "rendered": bool(ok)}


def _renumber_chapters(pages: list[dict]) -> bool:
    """单页操作后重算: chapter 章号连续重编 + contents items 与章题一一对应.

    返回 contents 条目是否变化 (变了须重渲染目录页 PNG)。
    """
    chapters = [p for p in pages if p["type"] == "chapter"]
    for i, ch in enumerate(chapters, 1):
        ch["fields"]["no"] = f"{i:02d}"
    titles = [(p["fields"].get("title") or "")[:20] for p in chapters]
    if not titles:  # chapter 全被换走 → 正文页题兜底
        titles = [(p["fields"].get("title") or "")[:20]
                  for p in pages if p["type"] not in ("cover", "contents", "outro")][:4]
    new_items = "|||".join(f"{i:02d} {t}" for i, t in enumerate(titles, 1))
    changed = False
    for p in pages:
        if p["type"] == "contents" and p["fields"].get("items") != new_items:
            p["fields"]["items"] = new_items
            changed = True
    return changed


@router.put("/bs1/{job_id}/pages/{idx}")
def bs1_page_edit(job_id: str, idx: int, body: dict = Body(...)):
    """字段直改 (页题/金句/条目/img_query/img_cap) → 单页重渲染。narration 不可改。"""
    from app.services.book_service.slide_render import _write_pages
    from app.services.book_service.slide_gen import _FIELDS
    from app.services.ppt_pipeline import _job_workdir
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not (0 <= idx < len(pages)):
        raise HTTPException(404, f"页不存在: {idx}")
    pd = pages[idx]
    allowed = _FIELDS.get(pd["type"], set()) | {"img_query", "img_cap"}
    fields = body.get("fields") or {}
    if "narration" in body or "type" in body:
        raise HTTPException(400, "narration/type 不可直改 (换页型走 replan)")
    changed_q = False
    for k, v in fields.items():
        if k in allowed:
            pd["fields"][k] = v
    if "img_query" in fields and fields["img_query"] != pd.get("img_query"):
        pd["img_query"] = str(fields["img_query"] or "")
        changed_q = True
    if "img_cap" in fields:
        pd["img_cap"] = str(fields["img_cap"] or "")[:15]
    _write_pages(_job_workdir(job_id), pages)
    return _bs1_rerender(job_id, idx, refetch_image=changed_q,
                         new_query=pd.get("img_query") if changed_q else "")


@router.post("/bs1/{job_id}/pages/{idx}/replan")
def bs1_page_replan(job_id: str, idx: int, body: dict = Body(...)):
    """换页型 (new_type) / 同页型重产字段 (hint) — LLM 单页重排, narration 铁律不动."""
    from app.services.book_service.slide_render import (
        page_to_dict, _write_pages,
    )
    from app.services.book_service.slide_gen import replan_page
    from app.services.ppt_pipeline import _job_workdir
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not (0 <= idx < len(pages)):
        raise HTTPException(404, f"页不存在: {idx}")
    pd = pages[idx]
    new_type = str(body.get("new_type") or "")
    if new_type and idx in (0, 1, len(pages) - 1):
        raise HTTPException(400, "cover/contents/outro 是结构位, 不可换页型")
    hint = str(body.get("hint") or "")
    from app.database import db_session
    db = db_session().__enter__()
    try:
        from app.models import BookProject
        book = db.query(BookProject).filter(BookProject.id == j["book_id"]).first()
        book_title = book.book_title if book else ""
    finally:
        db.close()
    np = replan_page(pd.get("narration") or "", new_type=new_type, hint=hint,
                     old_fields=pd.get("fields"), book_title=book_title)
    if np is None:
        raise HTTPException(502, "单页重规划失败 (LLM), 原页保留")
    old_q = pd.get("img_query")
    new = page_to_dict(np)
    new["img_file"], new["img_pexels_id"] = pd.get("img_file", ""), pd.get("img_pexels_id", 0)
    pages[idx] = new
    _write_pages(_job_workdir(job_id), pages)
    j["pages"] = pages
    q_changed = (new.get("img_query") or "") != (old_q or "")
    return _bs1_rerender(job_id, idx, refetch_image=q_changed,
                         new_query=new.get("img_query") if q_changed else "")


@router.post("/bs1/{job_id}/rerender")
def bs1_rerender_all(job_id: str):
    """全量重渲染 (不动字段): 刷 foot/品牌/logo/书名等上下文变更到每一页 PNG."""
    from app.services.book_service.slide_render import _write_pages, build_context, render_bs1_pages
    from app.services.ppt_pipeline import _job_workdir
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not pages:
        raise HTTPException(400, "页单为空")
    from app.database import db_session
    db = db_session().__enter__()
    try:
        ctx = build_context(db, j["book_id"], j["ep_index"])
    finally:
        db.close()
    if not ctx:
        raise HTTPException(404, "书/集不存在")
    ok = render_bs1_pages(pages, ctx, _job_workdir(job_id))
    _write_pages(_job_workdir(job_id), pages, meta={
        "job_id": job_id, "book_id": j["book_id"], "ep_index": j["ep_index"],
        "status": j.get("status") or "planned"})
    j["pages"] = pages
    _evt(job_id, f"全量重渲染 {len(ok)}/{len(pages)} 页 (foot=第{_cn_num_safe(j['ep_index'])}集)", "ok")
    return {"rendered": len(ok), "pages": len(pages)}


def _cn_num_safe(n) -> str:
    from app.services.book_service.slide_gen import _cn_num
    try:
        return _cn_num(int(n))
    except Exception:
        return str(n)


@router.delete("/bs1/{job_id}/pages/{idx}")
def bs1_page_delete(job_id: str, idx: int):
    """删空页: 仅允许零台词正文页 (删除零覆盖率损失; 有台词页删=台词丢, 拒绝)."""
    from app.services.book_service.slide_render import _write_pages
    from app.services.ppt_pipeline import _job_workdir
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not (0 <= idx < len(pages)):
        raise HTTPException(404, f"页不存在: {idx}")
    pd = pages[idx]
    if pd["type"] in ("cover", "contents", "outro"):
        raise HTTPException(400, "结构位 (首二末一) 不可删")
    if (pd.get("narration") or "").strip():
        raise HTTPException(400, "该页有台词, 不可删 (台词会丢; 请用换页型调整)")
    pages.pop(idx)
    _renumber_chapters(pages)
    _write_pages(_job_workdir(job_id), pages)
    j["pages"] = pages
    _evt(job_id, f"删除空页 P{idx+1} (零台词), 现 {len(pages)} 页", "ok")
    return {"pages": len(pages)}


@router.post("/bs1/{job_id}/pages/{idx}/reimage")
def bs1_page_reimage(job_id: str, idx: int, body: dict | None = Body(None)):
    """重配图: 改 img_query 或同词换下一张 (next)."""
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not (0 <= idx < len(pages)):
        raise HTTPException(404, f"页不存在: {idx}")
    pd = pages[idx]
    new_query = str((body or {}).get("img_query") or "").strip()
    if new_query:
        pd["img_query"] = new_query
        return _bs1_rerender(job_id, idx, refetch_image=True, new_query=new_query)
    return _bs1_rerender(job_id, idx, refetch_image=True)


@router.post("/bs1/{job_id}/bridge-audio")
def bs1_bridge_audio(job_id: str):
    """③音频 前置桥: 页单 → Script+segments (页=段, 老谭账号) → 音频加工页人工听审.

    render 的 TTS 缓存 key = 音色+段文本 (不含 script_id) — 音频页合成的成果
    render 直接复用 (cache 命中零重合成), 两页天然一致。
    """
    from app.database import db_session
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not pages:
        raise HTTPException(400, "页单为空")
    # 已有本集 bs1 桥接 Script 且页数一致 → 复用 (页改过则重建保持段文本同步)
    db = db_session().__enter__()
    try:
        from app.models import Article, Script, Segment, Voice
        from app.services.book_service.persona import ensure_laotan_book_account
        host = ensure_laotan_book_account(db)
        from app.models import Persona
        persona = db.query(Persona).filter(Persona.host_id == host.id).first()
        voice = None
        sel = j.get("voice_id")
        if sel:
            voice = db.get(Voice, sel)
        if not voice and persona and persona.voice_id:
            voice = db.get(Voice, persona.voice_id)
        j["voice_id"] = voice.id if voice else None
        article = Article(title="[老谭读书bs1]", raw_text="", track="tech")
        db.add(article); db.flush()
        script = Script(
            article_id=article.id, host_id=host.id,
            script_text="\n".join(p.get("narration") or "" for p in pages if p.get("narration")),
            video_format="landscape",
            prompt_template=persona.prompt_template if persona else "laotan-book",
        )
        db.add(script); db.flush()
        for i, p in enumerate(pages):
            db.add(Segment(
                script_id=script.id, line_index=i,
                text=p.get("narration") or f"(第{i+1}页无台词)",
                segment_type="body", selected_for_host=True, host_order=i,
            ))
        db.commit(); db.refresh(script)
        return {"script_id": script.id, "voice_id": j["voice_id"],
                "voice_name": voice.name if voice else None, "pages": len(pages)}
    finally:
        db.close()


@router.post("/bs1/{job_id}/confirm")
def bs1_confirm(job_id: str):
    """② 确认页单 → 落库 episodes.bs1_pages_json (render ③④⑤ 消费确认版)."""
    from app.services.book_service.slide_render import _write_pages
    from app.services.ppt_pipeline import _job_workdir
    from app.database import db_session
    j = _bs1_job(job_id)
    pages = j.get("pages") or []
    if not pages:
        raise HTTPException(400, "页单为空, 无法确认")
    db = db_session().__enter__()
    try:
        from app.models import Episode
        ep = db.query(Episode).filter(Episode.book_id == j["book_id"],
                                      Episode.ep_index == int(j["ep_index"])).first()
        if not ep:
            raise HTTPException(404, "集不存在")
        ep.bs1_pages_json = {"job_id": job_id, "pages": pages}  # 带 job_id 供重启恢复
        db.commit()
    finally:
        db.close()
    _write_pages(_job_workdir(job_id), pages)
    j["status"] = "confirmed"
    _evt(job_id, f"页单已确认 ({len(pages)} 页) — 可生成视频", "ok", type="bs1_confirmed")
    return {"job_id": job_id, "status": "confirmed", "pages": len(pages)}





@router.get("/{job_id}/status")
def status(job_id: str):
    j = _JOBS.get(job_id)
    if not j:
        raise HTTPException(404, f"任务不存在: {job_id}")
    return j


@router.get("/{job_id}/events")
async def events(job_id: str, request: Request):
    """SSE: PPT 产线进度推送."""
    import asyncio as _aio
    import json as _json
    from fastapi.responses import StreamingResponse
    from app.services.director_events import subscribe, unsubscribe

    queue = subscribe(job_id)
    async def stream():
        try:
            # 0909: 先回放已缓冲事件 (SSE 晚连会错过早期"启动/规划中", 导致日志空白)
            for past in list(_JOBS.get(job_id, {}).get("events", [])):
                yield f"data: {_json.dumps(past, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await _aio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"
                    if data.get("type") in ("ppt_done", "ppt_error"):
                        break
                except _aio.TimeoutError:
                    yield "data: {\"level\":\"info\",\"msg\":\"…\"}\n\n"
        finally:
            unsubscribe(job_id, queue)
    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{job_id}/download")
def download(job_id: str):
    """下载成片."""
    j = _JOBS.get(job_id)
    if not j or not j.get("output"):
        raise HTTPException(404, "成片不存在")
    path = Path(j["output"])
    if not path.exists():
        raise HTTPException(404, "成片文件缺失")
    return FileResponse(path, media_type="video/mp4",
                        filename=path.name)


@router.post("/{job_id}/export-jy-draft")
def export_jy(job_id: str):
    """PPT 出片 → 剪映草稿 (2026-08-20): 复用 J 线, 打开剪映审片/调BGM/导出."""
    from app.services.jy_draft_service import export_ppt_draft
    try:
        result = export_ppt_draft(job_id, get_config().defaults.ppt_work_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result
