"""Director 产物下载 + 打开产物文件夹端点."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.director_routes.common import _find_composition_mp4, _job_or_404, _slot_or_404

logger = logging.getLogger(__name__)

download_router = APIRouter(tags=["director"])

__all__ = ["download_router"]


@download_router.get("/jobs/{job_id}/slots/{slot_id}/preview")
def preview_slot(job_id: str, slot_id: str, db: Session = Depends(get_db)):
    """单个 slot 的视频预览 (2026-08-12): 返回该 slot 的渲染产物.

    slot.output_path 是本地绝对路径, 前端无法直接访问 → 经此端点以
    FileResponse 服务。文件缺失 → 404。
    """
    job = _job_or_404(db, job_id)
    slot = _slot_or_404(db, job_id, slot_id)
    if not slot.output_path:
        raise HTTPException(status_code=404, detail=f"slot {slot_id} 尚无产物 (status={slot.status})")
    p = Path(slot.output_path)
    if not p.exists():
        raise HTTPException(status_code=410, detail=f"产物文件丢失: {p}")
    media_type = "video/mp4"
    return FileResponse(str(p), media_type=media_type, filename=p.name)


@download_router.get("/jobs/{job_id}/download")
def download_job(job_id: str, db: Session = Depends(get_db)):
    job = _job_or_404(db, job_id)
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"job 状态 {job.status}, 暂未产出 (需 status=completed)",
        )
    # 优先从 composition manifest 文件读取, 其次 plan_json, 最后 fallback 搜索
    out = _find_composition_mp4(job_id)
    if not out:
        raise HTTPException(status_code=404, detail="未找到合成产物路径")
    p = Path(out)
    if not p.exists():
        raise HTTPException(status_code=410, detail=f"产物文件丢失: {p}")
    return FileResponse(str(p), media_type="video/mp4", filename=p.name)


@download_router.post("/jobs/{job_id}/open-folder")
def open_output_folder(job_id: str, db: Session = Depends(get_db)):
    """打开合成产物所在的文件夹（仅 Windows 本地环境）."""
    job = _job_or_404(db, job_id)
    out = _find_composition_mp4(job_id)
    if not out:
        raise HTTPException(status_code=404, detail="未找到合成产物路径")
    folder = str(Path(out).parent)
    if not Path(folder).exists():
        raise HTTPException(status_code=410, detail=f"产物目录不存在: {folder}")
    try:
        os.startfile(folder)
        return {"status": "ok", "folder": folder}
    except Exception as exc:
        logger.exception("open folder failed")
        raise HTTPException(status_code=500, detail=f"打开文件夹失败: {exc}")


@download_router.post("/jobs/{job_id}/export-script-txt")
def export_script_txt(job_id: str, db: Session = Depends(get_db)):
    """独立生成 洗稿.txt (2026-08-19): 合成按钮的后半段稿件功能独立成按钮.

    不跑合成: 直接把 job.script.script_text 写进合成目录 (_job_root, 与合成时
    落点一致) 并打开目录。无稿件文本 → 409。
    """
    job = _job_or_404(db, job_id)
    text = getattr(getattr(job, "script", None), "script_text", None)
    if not text:
        raise HTTPException(status_code=409, detail="该任务无稿件文本")
    from app.services.composition_service.common import _job_root
    from app.services.composition_service.output import _write_script_txt

    root = _job_root(job)
    root.mkdir(parents=True, exist_ok=True)
    _write_script_txt(job, root)
    try:
        os.startfile(str(root))
    except Exception as exc:
        logger.warning("[export-script-txt] open folder failed: %s", exc)
    return {"status": "ok", "path": str(root / "洗稿.txt"), "folder": str(root)}
