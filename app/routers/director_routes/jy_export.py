"""J 线草稿导出端点 — 导演 job → 剪映草稿 (J1)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.director_routes.common import _job_or_404
from app.services.director_events import publish as _evt
from app.services.jy_draft_service import export_job_draft

logger = logging.getLogger(__name__)

jy_router = APIRouter(tags=["director"])

__all__ = ["jy_router"]


@jy_router.post("/jobs/{job_id}/export-jy-draft")
def export_jy_draft(job_id: str, db: Session = Depends(get_db)):
    """导出当前 job 为剪映草稿 (同步, 纯写盘). 前置: job 存在 (可用性在 service 内校验)."""
    job = _job_or_404(db, job_id)  # 统一 404 语义
    try:
        result = export_job_draft(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 导出成功即视为成片交付 (2026-08-25): reviewing → completed + 常驻草稿名.
    # J 线导出后人工在剪映里审片/调 BGM/导出, 这条链的"合成"发生在剪映侧 —
    # 站在本流水线视角, 交付物(草稿)已产出, 任务应收口, 不再停在"待合成".
    draft_name = result.get("draft_name")
    if draft_name:
        job.jy_draft_name = draft_name
    if job.status in ("reviewing", "executing"):
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        logger.info("[director %s] J-line draft exported -> completed (draft=%s)",
                    job_id, draft_name)
    db.commit()
    _evt(job_id, {"type": "jy_exported", "msg": f"剪映草稿「{draft_name}」已导出",
                  "draft_name": draft_name})
    return result
