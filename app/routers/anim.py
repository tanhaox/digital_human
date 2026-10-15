# -*- coding: utf-8 -*-
"""动画产线路由 (/api/anim) — anim_pipeline 系统化接入 (0914).

链路: 🎬动画按钮 (讲书页) → 工坊页 anim.html:
  GET  /ep/{book_id}/{ep}/status     看板 (镜表+计数+运行 job; shots.json 是持久层)
  POST /ep/{book_id}/{ep}/plan       LLM 规划 → shots.json (job, 零 GPU)
  POST /ep/{book_id}/{ep}/k2         K2 批生图 (job, GPU 托管, 镜间可取消)
  POST /ep/{book_id}/{ep}/approve    人检回填 {ok:[..]|"all", redo:[{sid,note}]} (同步)
  POST /ep/{book_id}/{ep}/h3         H3 批生视频 (job, body: only/retry_failed/reroll)
  POST /ep/{book_id}/{ep}/redo-queue 列队重做 (job, body: sids — 购物篮多镜一条龙)
  POST /ep/{book_id}/{ep}/align      主线口播对齐归真 (同步, body: dry_run)
  POST /ep/{book_id}/{ep}/draft      装配剪映草稿 (同步)
  GET  /ep/{book_id}/{ep}/file/{path}  产物文件 (img/anim/audio/qc_sheet, 防穿越)
  GET  /job/{job_id}                 job 详情 (含日志尾)
  POST /job/{job_id}/cancel          协作取消 (镜间生效)
进度 SSE 复用通用通道: GET /api/jobs/{job_id}/events (anim_* 事件).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services import anim_service
from app.services.anim_service import AnimBusyError, AnimNotFound

router = APIRouter(prefix="/api/anim", tags=["anim"])


def _busy(exc: AnimBusyError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _not_found(exc: AnimNotFound | FileNotFoundError | KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


class K2Body(BaseModel):
    only: list[str] | None = None
    reset: bool = False
    chain_h3: bool = False


class H3Body(BaseModel):
    only: list[str] | None = None
    retry_failed: bool = False
    reroll: list[str] | None = None
    reset: bool = False


class PlanBody(BaseModel):
    force: bool = False
    scorched: bool = False
    concept_only: bool = False  # 0917 立意人闸: 切场+立意即停, 续跑走 /plan-scenes


class ReplanBody(BaseModel):
    arc_id: str


class RedoBody(BaseModel):
    sids: list[str]
    to_image: bool = False  # 0927 购物篮新路线: 只到生图 (K2 停 img_done 走人检, 不跑 H3)


class ApproveBody(BaseModel):
    ok: list[str] | str | None = None
    redo: list[dict] | list[str] | None = None
    note: str = ""


class TextLayerBody(BaseModel):
    entries: list[dict] | None = None


class AnimBody(BaseModel):
    motions: list[str]


class FixTextBody(BaseModel):
    word: str = ""


class AiFixBody(BaseModel):
    note: str = ""


class AlignBody(BaseModel):
    dry_run: bool = False


@router.get("/ep/{book_id}/{ep}/status")
def ep_status(book_id: str, ep: int):
    try:
        return anim_service.status_view(book_id, ep)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/plan")
def ep_plan(book_id: str, ep: int, body: PlanBody | None = None):
    try:
        return anim_service.start_phase(book_id, ep, "plan",
                                        force=(body.force if body else False),
                                        scorched=(body.scorched if body else False),
                                        concept_only=(body.concept_only if body else False))
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/plan-scenes")
def ep_plan_scenes(book_id: str, ep: int):
    """▶️ 分镜续跑 (0917 立意人闸第二段): 从盘上骨架/已完成场续跑至全集."""
    try:
        return anim_service.start_phase(book_id, ep, "plan", continue_scenes=True)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


class ConceptRerollBody(BaseModel):
    arc_id: str
    direction: str = ""


class ConceptSaveBody(BaseModel):
    ep_summary: str | None = None
    scenes: list[dict] = []


@router.post("/ep/{book_id}/{ep}/concept-save")
def ep_concept_save(book_id: str, ep: int, body: ConceptSaveBody):
    """立意编辑保存 (0918 立意模块 v2): 页面编辑 → 回写骨架 arcs + 投影立意.md."""
    from app.services.anim_pipeline import director2
    from app.services.anim_pipeline.config import load as _aload
    from app.services.anim_pipeline.planner import resolve_book
    try:
        _, book_title = resolve_book(_aload().db_path, book_id)
        return director2.save_concept(book_title, ep, body.model_dump())
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.get("/ep/{book_id}/{ep}/concept")
def ep_concept_get(book_id: str, ep: int):
    """立意模块读取 (编辑器数据源): arcs 结构化维度 + ep_summary."""
    from app.services.anim_pipeline.config import load as _aload
    from app.services.anim_pipeline.planner import resolve_book
    from app.services.anim_pipeline import shots as _shots
    try:
        _, book_title = resolve_book(_aload().db_path, book_id)
        doc = _shots.load_doc(book_title, ep)
        from app.services.anim_pipeline import director2 as _d2
        keys = ("narrative", "motion") + tuple(_d2._CONCEPT_KEYS)
        scenes = []
        for a in (doc.get("arcs") or []):
            row = {k: a.get(k) for k in keys}
            row.update(_d2._concept_fields(a))  # 旧立意兼容: visual_metaphor 拆 core
            row["arc_id"] = a.get("arc_id")
            # 0926 立意表格化: 世界/时段列 (只读面板六拍分配表数据源)
            row["visual_world"] = a.get("visual_world") or ""
            row["prop_lock"] = a.get("prop_lock") or ""
            row["t_start"] = a.get("t_start")
            row["t_end"] = a.get("t_end")
            scenes.append(row)
        return {"ep_summary": doc.get("ep_summary", ""), "scenes": scenes,
                "world_library": doc.get("world_library") or [],
                "warnings": doc.get("_concept_warnings") or []}
    except AnimNotFound as exc:
        raise _not_found(exc)



@router.post("/ep/{book_id}/{ep}/concept-reroll")
def ep_concept_reroll(book_id: str, ep: int, body: ConceptRerollBody):
    """单场立意重掷 (0918 人闸闭环): 只重出该场隐喻 (LLM 同步, ~30s), 其余场不动."""
    from app.services.anim_pipeline import director2
    from app.services.anim_pipeline.config import load as _aload
    from app.services.anim_pipeline.planner import resolve_book
    try:
        _, book_title = resolve_book(_aload().db_path, book_id)
        return director2.reroll_concept(book_id, book_title, ep,
                                        body.arc_id, body.direction)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except SystemExit as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/replan")
def ep_replan(book_id: str, ep: int, body: ReplanBody):
    """单场重规划 (逐场磨合): 只重生该场组/镜, 其余场不动 (job, 零 GPU, 备份后落盘)."""
    try:
        return anim_service.start_phase(book_id, ep, "replan", arc_id=body.arc_id)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/enhance")
def ep_enhance(book_id: str, ep: int):
    """手动增强 (1013 P0: 自动增强退役后的修复入口, 🖌 按钮; 零 GPU)."""
    try:
        return anim_service.start_phase(book_id, ep, "enhance")
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/brand")
def ep_brand(book_id: str, ep: int):
    """品牌卡随书生成 (骨架冻结+书级风格+圣经色板; 仪式句走后期字幕)."""
    try:
        return anim_service.start_phase(book_id, ep, "brand")
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/k2")
def ep_k2(book_id: str, ep: int, body: K2Body | None = None):
    try:
        only = set(body.only) if body and body.only else None
        return anim_service.start_phase(book_id, ep, "k2", only=only,
                                        reset=bool(body.reset) if body else False,
                                        chain_h3=bool(body.chain_h3) if body else False)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/h3")
def ep_h3(book_id: str, ep: int, body: H3Body | None = None):
    try:
        only = set(body.only) if body and body.only else None
        reroll = set(body.reroll) if body and body.reroll else None
        return anim_service.start_phase(book_id, ep, "h3", only=only,
                                        retry_failed=bool(body.retry_failed) if body else False,
                                        reroll=reroll,
                                        reset=bool(body.reset) if body else False)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/redo-queue")
def ep_redo_queue(book_id: str, ep: int, body: RedoBody):
    """列队重做 (0921 购物篮): 逐镜重设计 (跳败续跑) → 成功集 K2+H3 一条龙 (job).

    to_image (0927): 重设计→生图后停在 img_done 走标准人检 (✅过审/🔁打回),
    不自动过审不跑 H3 — 视频看过再逐镜/批量生。"""
    if not body.sids:
        raise HTTPException(status_code=422, detail="sids 为空")
    try:
        return anim_service.start_phase(book_id, ep, "redo", sids=set(body.sids),
                                        to_image=bool(body.to_image))
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/approve")
def ep_approve(book_id: str, ep: int, body: ApproveBody):
    try:
        return anim_service.apply_approve(book_id, ep, ok=body.ok, redo=body.redo, note=body.note)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/shot/{sid}/text-layer")
def ep_shot_text_layer(book_id: str, ep: int, sid: str, body: TextLayerBody):
    """逐镜花字编辑: 规划没给的字后期随时补/改/删 (text_layer → 剪映字幕轨)."""
    try:
        return anim_service.update_text_layer(book_id, ep, sid, body.entries)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/shot/{sid}/anim")
def ep_shot_anim(book_id: str, ep: int, sid: str, body: AnimBody):
    """逐镜动画描述编辑 (图不动): 文字结构修好后重roll 即可."""
    try:
        return anim_service.update_shot_anim(book_id, ep, sid, body.motions)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/shot/{sid}/fix-text")
def ep_shot_fix_text(book_id: str, ep: int, sid: str, body: FixTextBody | None = None):
    """一键修字: H3 入画字下沉后期金字 + 换seed (图不动, 重roll生效)."""
    try:
        return anim_service.fix_shot_text(book_id, ep, sid, word=(body.word if body else ""))
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/shot/{sid}/replan")
def ep_shot_replan(book_id: str, ep: int, sid: str):
    """单镜重规划: 只重设计这一镜 (时间槽/口播/兄弟镜不动) → planned 待生图.
    0916: 品牌卡镜也走此路 (先剥品牌标回普通镜, 再 LLM 重设计)."""
    try:
        return anim_service.replan_shot(book_id, ep, sid)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/shot/{sid}/viewed")
def ep_shot_viewed(book_id: str, ep: int, sid: str):
    """1010 R 标清除: 用户点播放看过该镜 → 撤"重做待复查"标。
    R 标在独立文件不吃集锁 — 批量任务进行中也能清。"""
    try:
        return anim_service.mark_viewed(book_id, ep, sid)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/renumber")
def ep_renumber(book_id: str, ep: int):
    """整集镜号重排: 按时间轴 s1..sN (文件同步改名, 自动备份)."""
    try:
        return anim_service.renumber_shots(book_id, ep)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/shot/{sid}/brand-tag")
def ep_shot_brand_tag(book_id: str, ep: int, sid: str, on: bool = True):
    """品牌卡镜手动标/剥 (0916): on=True 补标 (零 GPU 直通), on=False 剥标回生成队列."""
    try:
        return anim_service.brand_tag_shot(book_id, ep, sid, on)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/shot/{sid}/brand-pass")
def ep_shot_brand_pass(book_id: str, ep: int, sid: str):
    """品牌卡单镜一键直通 (0921): 插定稿 keyframe+视频 → anim_done, 零 GPU 秒级."""
    try:
        return anim_service.brand_pass_shot(book_id, ep, sid)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)


@router.post("/ep/{book_id}/{ep}/shot/{sid}/ai-fix-motion")
def ep_shot_ai_fix(book_id: str, ep: int, sid: str, body: AiFixBody | None = None):
    """AI 动画医生: 图/背景动画不动, LLM 按文字纪律重写 motion → 自动换seed (重roll生效)."""
    try:
        return anim_service.ai_fix_shot_motion(book_id, ep, sid, note=(body.note if body else ""))
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/align")
def ep_align(book_id: str, ep: int, body: AlignBody | None = None):
    from app.services import anim_draft
    try:
        return anim_draft.align_episode(book_id, ep, dry_run=(body.dry_run if body else False))
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ep/{book_id}/{ep}/opening")
def ep_opening_asset(book_id: str, ep: int):
    """开场白书级素材生成 (wav=素材: 一次生成全系列复用; 装配只读).
    """
    try:
        return anim_service.produce_opening_asset(book_id, ep)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))

@router.post("/ep/{book_id}/{ep}/draft")
def ep_draft(book_id: str, ep: int):
    from app.services import anim_draft
    try:
        return anim_draft.build_draft(book_id, ep)
    except AnimBusyError as exc:
        raise _busy(exc)
    except AnimNotFound as exc:
        raise _not_found(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/ep/{book_id}/{ep}/file")
@router.get("/ep/{book_id}/{ep}/file/{file_path:path}")
def ep_file(book_id: str, ep: int, file_path: str = "", path: str | None = None):
    # path query 模式: rel 含 ../ (品牌卡 ../../_资产/...) 时, URL 点段会被浏览器规范化
    # 掉 → 必须走 ?path=<urlencode(rel)>; 纯 ep 内路径两种都行
    rel = path if path is not None else file_path
    try:
        _, book_title = anim_service.resolve_ep(book_id, ep)
        p = anim_service.resolve_ep_file(book_title, ep, rel)
    except AnimNotFound as exc:
        raise _not_found(exc)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {rel}")
    return FileResponse(str(p))


@router.get("/jobs/running")
def anim_running_jobs():
    """运行中 job 列表 — scripts/restart_backend.py 的安全重启守卫."""
    return {"jobs": anim_service.running_jobs()}


@router.get("/job/{job_id}")
def anim_job(job_id: str):
    try:
        return anim_service.get_job(job_id)
    except AnimNotFound:
        # 1010 根治: job 注册表在进程内存, 后端重启即清空 — 遗留的轮询者
        # (旧标签页/agent 会话的 until-shell 监视循环)拿 404 会无限重试。
        # 返回墓碑让所有已知消费者收敛: 前端 job.js 判 status!=='running' 即停;
        # shell 监视循环惯例判 status in (done/failed/cancelled) 即退出。
        # status 用 cancelled (重启=进程死=任务被取消, 且无人会对 cancelled 自动重试),
        # phase=archived 标明真实语义, 不与真终态混淆。
        return {"id": job_id, "phase": "archived", "status": "cancelled",
                "book_id": "", "ep": 0, "arc": None, "progress": None,
                "error": None, "result": {"archived": True}, "started_at": None,
                "finished_at": None, "log": [], "log_lines": 0}


@router.post("/job/{job_id}/cancel")
def anim_job_cancel(job_id: str):
    try:
        return anim_service.cancel_job(job_id)
    except AnimNotFound as exc:
        raise _not_found(exc)
