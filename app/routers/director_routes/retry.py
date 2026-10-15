"""Director slot 重试端点: 单 slot / 批量 / purge replaced.

原 `retry_slot`(87 行)拆分为 `_apply_retry_pipeline_degrade`(管线降级块)
与 `_reset_slot_for_retry`(状态重置), 端点主体 ≤40 行。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DirectorJob, DirectorSlot
from app.schemas import RetrySlotResponse
from app.services.director_parser import _best_fallback_workflow
from app.services.slot_executor import execute_slot
from app.services.director_events import publish as _evt
from app.routers.director_routes.common import (
    _WORKFLOW_PIPELINE, _decode_pipelines, _job_or_404, _slot_or_404,
)
from app.routers.director_routes.execution import _executing_jobs

logger = logging.getLogger(__name__)

retry_router = APIRouter(tags=["director"])

__all__ = ["retry_router", "_original_workflow", "_apply_retry_pipeline_degrade",
           "_reset_slot_for_retry", "_restore_original_workflow", "_execute_slot_retry"]


def _original_workflow(slot: DirectorSlot) -> str:
    """Trace replaced_from chain back to the earliest non-fallback workflow."""
    fallback_only = {"black_placeholder"}
    params = slot.params_json or {}
    seen = {slot.workflow}
    current = params.get("replaced_from")
    while current and current not in fallback_only and current not in seen:
        seen.add(current)
        # replaced_from may itself point to a slot that was replaced earlier.
        # We do not have that slot object here, so we only go one level deep
        # via the params_json chain stored by replace_failed_slot.
        current = params.get("replaced_from")
    # If replaced_from is another fallback (should not happen) or missing,
    # prefer the first entry in the standard fallback chain.
    origin = params.get("replaced_from")
    if origin and origin not in fallback_only:
        return origin
    return "broll_pexels"


def _apply_retry_pipeline_degrade(db: Session, job_id: str,
                                  slot: DirectorSlot, pipelines: str | None) -> None:
    """If slot's workflow pipeline is disabled, degrade to an allowed pipeline.

    pipelines 已在端点解析 (query param 缺省回退 job.pipelines)。
    注意: 用 `is not None` 判断空集合(全关) — 空集合也是有效约束, 需进入降级逻辑。
    """
    enabled_pipelines: set[str] | None = _decode_pipelines(pipelines)
    if enabled_pipelines is None:
        return
    wf_pipeline = _WORKFLOW_PIPELINE.get(slot.workflow)
    if wf_pipeline is None or wf_pipeline in enabled_pipelines:
        return
    original_wf = slot.workflow
    fallback = _best_fallback_workflow(enabled_pipelines, prefer=original_wf)
    if fallback == original_wf:
        raise HTTPException(
            status_code=400,
            detail=f"管线 {wf_pipeline.upper()} 已禁用, 且 {original_wf} 无可用替代管线",
        )
    logger.info(
        "[director %s] retry_slot 管线降级: %s→%s (管线 %s 已禁用)",
        job_id, original_wf, fallback, wf_pipeline.upper(),
    )
    slot.workflow = fallback
    slot.visual_type = fallback
    params = slot.params_json or {}
    params["fallback_reason"] = f"{wf_pipeline}_pipeline_disabled"
    slot.params_json = params
    db.commit()


def _reset_slot_for_retry(slot: DirectorSlot) -> None:
    """Reset slot status on the original row (not creating a new slot)."""
    slot.status = "queued"
    slot.error_code = None
    slot.error_message = None
    slot.output_path = None


def _job_truly_executing(job: DirectorJob) -> bool:
    """僵尸检测: job 是否真正在后台执行中.

    running slot 可能是服务重启/执行中断留下的僵尸状态 (job 已不在
    _executing_jobs 且状态非 executing)。此时允许重试恢复, 而非 409 拒绝。
    """
    return job.status == "executing" and job.id in _executing_jobs


def _restore_original_workflow(job_id: str, slot: DirectorSlot) -> None:
    """ID-025: black_placeholder 重试必须恢复原始 workflow."""
    if slot.workflow != "black_placeholder":
        return
    original = _original_workflow(slot)
    slot.workflow = original
    slot.visual_type = original
    # visual_type was set to next_workflow in replace_failed_slot; restore it too.
    logger.info("[director %s] retry_slot restoring workflow %s for slot %s", job_id, original, slot.id)


def _execute_slot_retry(db: Session, slot: DirectorSlot) -> DirectorSlot:
    """Run execute_slot, holding a comfyui GPU session for host pipelines."""
    if slot.workflow in ("host", "mixed_host_broll"):
        from app.services.gpu_service_manager import get_gpu_service_manager
        with get_gpu_service_manager().session("comfyui"):
            return execute_slot(db, slot)
    return execute_slot(db, slot)


@retry_router.post("/jobs/{job_id}/slots/{slot_id}/retry", response_model=RetrySlotResponse)
def retry_slot(
    job_id: str,
    slot_id: str,
    pipelines: str | None = None,
    force_pexels: bool = False,
    db: Session = Depends(get_db),
):
    """手动重试一个 slot: 重置为 queued 后重新执行, 必要时降级管线/恢复原始 workflow.

    force_pexels=True (2026-08-12): 强制走 Pexels 在线下载新素材 (跳过本地碰撞)。
    用于本地碰撞/替换都走死时"重新生成新素材"。broll_local 槽位会切 broll_pexels。
    """
    job = _job_or_404(db, job_id)
    slot = _slot_or_404(db, job_id, slot_id)

    if slot.status == "running":
        if _job_truly_executing(job):
            raise HTTPException(status_code=409,
                                detail="slot 正在执行中，请等待完成后再重新生成")
        # 僵尸 running: job 不在后台执行 (服务重启/中断遗留), 放行重试
        logger.warning("[director %s] slot %s status=running but job not executing, "
                       "treat as zombie and allow retry",
                       job_id, slot.id)
        slot.status = "queued"
        db.commit()

    # ── 管线降级: 若 slot workflow 管线被禁用则自动找替代 (query 缺省回退 job.pipelines; 三态 None/空/部分) ──
    if pipelines is not None and not pipelines.strip():
        pipelines = job.pipelines
    _apply_retry_pipeline_degrade(db, job_id, slot, pipelines)
    _restore_original_workflow(job_id, slot)

    # ── force_pexels (2026-08-12): 强制走 P 线下载新素材, 跳过本地碰撞 ──
    # 本地碰撞/替换都走死时, 用户点"重新生成"可强制下载新素材。
    # broll_local 槽位切 broll_pexels (否则会先走本地策略)。
    if force_pexels:
        params = dict(slot.params_json or {})
        params["force_pexels"] = True
        slot.params_json = params
        if slot.workflow in ("broll_local", "black_placeholder"):
            slot.workflow = "broll_pexels"
            slot.visual_type = "broll_pexels"
        logger.info("[director %s] retry_slot %s force_pexels=True (强制 P 线下载)",
                    job_id, slot.id)

    # 在原 slot 上重置状态，不创建新 slot
    _reset_slot_for_retry(slot)
    db.commit()
    logger.info("[director %s] retry_slot %s (workflow=%s) reset to queued",
                job_id, slot.id, slot.workflow)
    _evt(job_id, {"type": "slot_start", "slot_index": slot.slot_index,
                  "workflow": slot.workflow, "msg": f"Slot #{slot.slot_index} 重试开始"})

    # force 标记无天然清理点: 每个新执行入口显式清除
    from app.services.slot_executor import clear_force_stopped
    clear_force_stopped(job_id)

    try:
        slot = _execute_slot_retry(db, slot)
    except Exception as exc:
        logger.exception("retry execute_slot failed")
        _evt(job_id, {"type": "slot_fail", "slot_index": slot.slot_index,
                      "workflow": slot.workflow, "error": str(exc)[:200],
                      "msg": f"Slot #{slot.slot_index} 重试失败"})
        raise HTTPException(status_code=500, detail=f"retry failed: {exc}")

    if slot.status == "completed":
        logger.info("[director %s] retry_slot %s done, output=%s",
                    job_id, slot.id, slot.output_path)
        _evt(job_id, {"type": "slot_done", "slot_index": slot.slot_index,
                      "workflow": slot.workflow, "msg": f"Slot #{slot.slot_index} 重试完成"})
    else:
        logger.warning("[director %s] retry_slot %s failed: %s",
                       job_id, slot.id, slot.error_message)
        _evt(job_id, {"type": "slot_fail", "slot_index": slot.slot_index,
                      "workflow": slot.workflow, "error": slot.error_message or "",
                      "msg": f"Slot #{slot.slot_index} 重试失败"})

    return RetrySlotResponse(
        slot_id=slot.id,
        status=slot.status,
        message=(
            f"slot retried (workflow={slot.workflow}), status={slot.status}"
            + (f", output={slot.output_path}" if slot.output_path else "")
            + (f", error={slot.error_message}" if slot.error_message else "")
        ),
    )


@retry_router.post("/jobs/{job_id}/purge-replaced")
def purge_replaced_slots(job_id: str, db: Session = Depends(get_db)):
    """删除所有 status='replaced' 的 slot，只保留每个 slot_index 的最新活跃条目。"""
    job = _job_or_404(db, job_id)
    if job.status == "executing":
        raise HTTPException(status_code=409, detail="任务执行中，不可清理")
    replaced = [s for s in job.slots if s.status == "replaced"]
    # 素材生命周期 (2026-08-07): 连带删除被替换 slot 的磁盘目录, 避免孤儿残留。
    # replaced slot 有独立目录 (composition_output_root/<job_id>/slots/<slot_id>),
    # 与活跃 slot 互不冲突; 保留策略下仅此路径负责回收被替换 slot 的旧素材。
    import shutil
    from app.config import get_config
    cfg = get_config()
    for slot in replaced:
        dir_to_remove = Path(cfg.defaults.composition_output_root) / job_id / "slots" / str(slot.id)
        if dir_to_remove.exists() and dir_to_remove.is_dir():
            try:
                shutil.rmtree(dir_to_remove, ignore_errors=True)
                logger.info("[director %s] purge removed replaced-slot dir %s", job_id, dir_to_remove)
            except Exception as exc:
                logger.warning("[director %s] purge failed to remove %s: %s", job_id, dir_to_remove, exc)
    for slot in replaced:
        db.delete(slot)
    if replaced:
        db.commit()
    logger.info("[director %s] purged %d replaced slots", job_id, len(replaced))
    return {"status": "ok", "purged_count": len(replaced)}


@retry_router.post("/jobs/{job_id}/retry-workflow")
def retry_by_workflow(
    job_id: str,
    workflow: str,
    db: Session = Depends(get_db),
):
    """将指定 workflow 类型的所有 failed/completed/skipped slot 重置为 queued（重新生成）。"""
    job = _job_or_404(db, job_id)
    # 僵尸检测: job 状态 executing 但后台线程已死 (服务重启等)
    if job.status == "executing" and job_id not in _executing_jobs:
        job.status = "reviewing"
        db.commit()
    if job.status == "executing":
        raise HTTPException(status_code=409, detail="任务执行中，请等待完成后再重试")

    reset_count = 0
    for slot in job.slots:
        if slot.workflow == workflow and slot.status in ("failed", "completed", "skipped"):
            _reset_slot_for_retry(slot)
            reset_count += 1

    if reset_count == 0:
        raise HTTPException(status_code=404, detail=f"无 {workflow} 类型的 failed/completed/skipped slot 可重新生成")

    # 如果 job 之前是 reviewing/failed，改回 reviewing 以便再次执行
    if job.status in ("failed", "reviewing", "completed"):
        job.status = "reviewing"
    db.commit()

    # force 标记无天然清理点: 每个新执行入口显式清除
    from app.services.slot_executor import clear_force_stopped
    clear_force_stopped(job_id)

    logger.info("[director %s] retry-workflow %s: reset %d slots", job_id, workflow, reset_count)
    return {"status": "ok", "reset_count": reset_count, "workflow": workflow}


@retry_router.post("/jobs/{job_id}/slots/{slot_id}/replace-material")
def replace_slot_material(
    job_id: str,
    slot_id: str,
    payload: dict,
    db: Session = Depends(get_db),
):
    """替换 slot 素材 (2026-08-12): 填素材库编号 asset_no → 用该素材重渲染.

    流程: 按 asset_no 查 VideoAsset → 取文件名 → 写 slot.params_json["file"]
    + workflow 改 broll_local → _reset_slot_for_retry → 同步 execute_slot 重渲染。
    broll_local 线 `_try_exact_file` 用 params["file"] 精确命中 materials_dir
    下文件 (已验证全部素材 file_path 都在 materials_dir 下)。同步执行保证
    替换即时生效 —— 返回时新素材已渲染, 前端刷新预览即见新内容。

    payload: {"asset_no": "V20260803-0177"}
    """
    from app.models import VideoAsset

    asset_no = (payload or {}).get("asset_no")
    if not asset_no or not isinstance(asset_no, str):
        raise HTTPException(status_code=400, detail="缺少 asset_no (素材库编号)")
    asset_no = asset_no.strip()

    job = _job_or_404(db, job_id)
    slot = _slot_or_404(db, job_id, slot_id)

    # 仅 broll 类 slot 可替换素材 (HF 图表/标题卡/host 出镜无素材可换)
    if slot.workflow not in ("broll_pexels", "broll_local"):
        raise HTTPException(
            status_code=409,
            detail=f"workflow={slot.workflow} 非 broll 类, 不支持素材替换",
        )
    if slot.status == "running":
        raise HTTPException(status_code=409, detail="slot 正在执行中, 请等待完成后再替换")

    asset = db.query(VideoAsset).filter(VideoAsset.asset_no == asset_no).first()
    if asset is None:
        raise HTTPException(status_code=404, detail=f"素材库无编号 {asset_no}")
    fp = Path(asset.file_path)
    if not fp.exists():
        raise HTTPException(status_code=410, detail=f"素材文件缺失: {fp}")

    # ── 替换去重校验 (2026-08-12): 一素材一视频只用一次 ──
    # 遍历同 job 所有 broll 类 slot (排除自身), 检查该素材是否已被用过。
    # 四种标识任一命中即视为重复: pexels_id / local_file / file / replaced_asset_no。
    used_by: DirectorSlot | None = None
    for other in job.slots:
        if other.id == slot.id:
            continue
        op = other.params_json or {}
        if asset.pexels_id is not None and op.get("pexels_id") == asset.pexels_id:
            used_by = other
            break
        if asset.file_path and op.get("local_file") == asset.file_path:
            used_by = other
            break
        if op.get("file") == fp.name:
            used_by = other
            break
        if op.get("replaced_asset_no") == asset_no:
            used_by = other
            break
    if used_by is not None:
        raise HTTPException(
            status_code=409,
            detail=f"素材 {asset_no} 已用于 Slot #{used_by.slot_index} (workflow={used_by.workflow})，"
                   f"一视频一素材，不能重复替换",
        )

    # 写入替换参数 + 切到 broll_local 线 (精确文件名命中)
    params = dict(slot.params_json or {})
    params["file"] = fp.name
    params["replaced_asset_no"] = asset_no
    slot.params_json = params
    slot.workflow = "broll_local"
    slot.visual_type = "broll_local"

    _reset_slot_for_retry(slot)
    db.commit()
    logger.info(
        "[director %s] replace-material slot %s → %s (%s), re-rendering",
        job_id, slot.id, asset_no, fp.name,
    )
    _evt(job_id, {"type": "slot_start", "slot_index": slot.slot_index,
                  "workflow": slot.workflow, "msg": f"Slot #{slot.slot_index} 替换素材 {asset_no} 开始"})

    from app.services.slot_executor import clear_force_stopped
    clear_force_stopped(job_id)

    slot = _execute_slot_retry(db, slot)
    if slot.status == "completed":
        logger.info("[director %s] replace-material slot %s done, output=%s",
                    job_id, slot.id, slot.output_path)
        _evt(job_id, {"type": "slot_done", "slot_index": slot.slot_index,
                      "workflow": slot.workflow, "msg": f"Slot #{slot.slot_index} 替换素材完成"})
    else:
        logger.warning("[director %s] replace-material slot %s failed: %s",
                       job_id, slot.id, slot.error_message)
        _evt(job_id, {"type": "slot_fail", "slot_index": slot.slot_index,
                      "workflow": slot.workflow, "error": slot.error_message or "",
                      "msg": f"Slot #{slot.slot_index} 替换素材失败"})

    return RetrySlotResponse(
        slot_id=slot.id,
        status=slot.status,
        message=(
            f"replaced material {asset_no} (workflow=broll_local), status={slot.status}"
            + (f", output={slot.output_path}" if slot.output_path else "")
            + (f", error={slot.error_message}" if slot.error_message else "")
        ),
    )


@retry_router.post("/jobs/{job_id}/slots/{slot_id}/disable-material")
def disable_slot_material(job_id: str, slot_id: str, db: Session = Depends(get_db)):
    """禁用 slot 当前素材 (2026-08-12): 设 VideoAsset.preference=dislike.

    从 slot 反查素材 (三种途径: pexels_id / local_file / replaced_asset_no)。
    禁用后: Pexels 在线 resolve 与本地碰撞线都会排除该素材 (本地线过滤
    2026-08-12 已补齐)。返回 asset_no + preference 供前端提示。
    """
    from app.models import VideoAsset

    job = _job_or_404(db, job_id)
    slot = _slot_or_404(db, job_id, slot_id)
    params = slot.params_json or {}

    # 反查 VideoAsset: pexels_id → local_file → replaced_asset_no
    asset: VideoAsset | None = None
    pid = params.get("pexels_id")
    if pid is not None:
        asset = db.query(VideoAsset).filter(VideoAsset.pexels_id == pid).first()
    if asset is None and params.get("local_file"):
        asset = (
            db.query(VideoAsset)
            .filter(VideoAsset.file_path == params["local_file"])
            .first()
        )
    if asset is None and params.get("file"):
        from app.config import get_config
        full = Path(get_config().defaults.materials_dir) / params["file"]
        asset = (
            db.query(VideoAsset)
            .filter(VideoAsset.file_path == str(full))
            .first()
        )
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"slot #{slot.slot_index} 素材未入库，无法打禁用标 "
                   f"(params: pexels_id={pid}, file={params.get('file')})",
        )

    asset.preference = "dislike"
    db.commit()
    logger.info("[director %s] disable-material slot %s → %s (dislike)",
                job_id, slot.id, asset.asset_no)
    return {"asset_no": asset.asset_no, "preference": "dislike", "slot_index": slot.slot_index}
