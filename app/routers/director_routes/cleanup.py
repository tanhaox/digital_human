"""Director 任务清理端点: 单任务删除 + 批量清理."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DirectorJob
from app.routers.director_routes.common import _cleanup_job_files, _job_or_404

logger = logging.getLogger(__name__)

cleanup_router = APIRouter(tags=["director"])

__all__ = ["cleanup_router"]


@cleanup_router.delete("/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    """删除单个导演任务及其所有 slots + 磁盘文件。执行中的任务不可删除。"""
    job = _job_or_404(db, job_id)
    if job.status == "executing":
        raise HTTPException(status_code=409, detail="任务执行中，不可删除")
    # 删除关联 slots（cascade 已配置，但显式删除更安全）
    for slot in list(job.slots):
        db.delete(slot)
    db.delete(job)
    db.commit()
    _cleanup_job_files(job_id)
    logger.info("[director] deleted job %s (with files)", job_id)
    return {"status": "ok", "deleted": job_id}


@cleanup_router.post("/jobs/cleanup")
def cleanup_jobs(
    only_empty: bool = True,
    db: Session = Depends(get_db),
):
    """批量清理导演任务 + 磁盘文件。

    only_empty=True: 只清理无任何 completed slot 的任务（从未产出视频）
    only_empty=False: 清理所有 failed/planning 状态的任务
    执行中的任务始终跳过。
    """
    candidates = (
        db.query(DirectorJob)
        .filter(DirectorJob.status.in_(["failed", "planning", "reviewing"]))
        .all()
    )
    deleted_ids: list[str] = []
    for job in candidates:
        if only_empty:
            has_completed = any(s.status == "completed" for s in job.slots)
            if has_completed:
                continue
        for slot in list(job.slots):
            db.delete(slot)
        db.delete(job)
        deleted_ids.append(job.id)

    if deleted_ids:
        db.commit()
    for jid in deleted_ids:
        _cleanup_job_files(jid)
    logger.info("[director] cleanup deleted %d job(s) (with files)", len(deleted_ids))
    return {"status": "ok", "deleted_count": len(deleted_ids), "deleted_ids": deleted_ids}
