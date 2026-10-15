"""AI 素材打标 router — 触发 / 状态 / SSE / 取消."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import VideoAsset
from ..schemas import TaggingProgressOut, TaggingRunRequest
from ..services.director_events import publish, subscribe, unsubscribe
from ..services.video_tagging_service import (
    cancel_job,
    get_job_status,
    start_tagging_job,
    tag_single_asset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library/tagging", tags=["tagging"])


def _progress_to_out(job_id: str, status: dict) -> TaggingProgressOut:
    return TaggingProgressOut(
        job_id=job_id,
        status=status.get("status", "unknown"),
        total=status.get("total", 0),
        done=status.get("done", 0),
        failed=status.get("failed", 0),
        current_asset_no=status.get("current_asset_no"),
        error=status.get("error"),
        started_at=status.get("started_at"),
        finished_at=status.get("finished_at"),
    )


@router.post("/run", response_model=TaggingProgressOut)
def run_tagging(req: TaggingRunRequest, db: Session = Depends(get_db)) -> TaggingProgressOut:
    """启动批量 AI 打标任务.

    - 传 asset_ids 则仅打标指定素材
    - 不传则全量打标所有素材
    """
    # 验证素材存在
    if req.asset_ids:
        existing = set(
            row[0] for row in
            db.query(VideoAsset.id).filter(VideoAsset.id.in_(req.asset_ids)).all()
        )
        missing = [aid for aid in req.asset_ids if aid not in existing]
        if missing:
            raise HTTPException(404, f"素材不存在: {', '.join(missing[:5])}")
        if not existing:
            raise HTTPException(400, "指定的素材列表为空")

    try:
        job_id = start_tagging_job(req.asset_ids if req.asset_ids else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    status = get_job_status(job_id) or {}
    return _progress_to_out(job_id, status)


@router.post("/asset/{asset_id}")
def tag_one(asset_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """同步打标单个素材, 返回完整标签.

    用于测试 / 单素材重新打标.
    """
    existing = db.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
    if existing is None:
        raise HTTPException(404, f"素材不存在: {asset_id}")

    result = tag_single_asset(asset_id)
    if not result.get("_success") and result.get("_error"):
        raise HTTPException(500, result["_error"])
    return result


@router.get("/status/{job_id}", response_model=TaggingProgressOut)
def status(job_id: str) -> TaggingProgressOut:
    """轮询任务进度."""
    st = get_job_status(job_id)
    if st is None:
        raise HTTPException(404, f"任务不存在: {job_id}")
    return _progress_to_out(job_id, st)


@router.get("/status/{job_id}/stream")
async def status_stream(job_id: str):
    """SSE 实时进度流."""
    st = get_job_status(job_id)
    if st is None:
        raise HTTPException(404, f"任务不存在: {job_id}")

    queue = subscribe(job_id)

    async def event_generator():
        try:
            # 先发送当前状态
            current = get_job_status(job_id) or {}
            yield f"data: {json.dumps(current, ensure_ascii=False)}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    # 心跳
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                if data.get("type") in ("complete", "cancelled"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/cancel/{job_id}")
def cancel(job_id: str) -> dict[str, str]:
    """取消正在进行的打标任务."""
    ok = cancel_job(job_id)
    if not ok:
        raise HTTPException(400, "任务不存在或已结束")
    return {"status": "cancelled", "job_id": job_id}


@router.get("/count")
def asset_count(db: Session = Depends(get_db)) -> dict[str, int]:
    """获取素材总数及 AI 打标覆盖率."""
    from sqlalchemy import func
    total = db.query(func.count(VideoAsset.id)).scalar() or 0
    tagged = db.query(func.count(VideoAsset.id)).filter(
        VideoAsset.ai_tagged_at.isnot(None)
    ).scalar() or 0
    return {"total": total, "ai_tagged": tagged, "untagged": total - tagged}
