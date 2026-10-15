"""Visual render (HF) router — 标准 JSON 入参 → HF 渲染 → MP4 + 关键帧 + manifest.

8 端点 (与数字人视频 router 对齐):
  GET  /api/visual-render/templates                    列可用模板
  GET  /api/visual-render/jobs                         列任务
  POST /api/visual-render/jobs                         创建任务 (status=queued, 仅校验入参)
  GET  /api/visual-render/jobs/{id}                    详情
  POST /api/visual-render/jobs/{id}/generate           同步: 准备 → 渲染 → 校验 → 落盘
  GET  /api/visual-render/jobs/{id}/download           下载产物 mp4
  GET  /api/visual-render/jobs/{id}/frames/{kind}      下载 first/middle/final.png
  DELETE /api/visual-render/jobs/{id}                  删除任务 + 产物目录 (走 send2trash)

设计要点:
  - 独立管线: 不接 ComfyUI / 不占 GPU / 与 DigitalHumanVideo 状态机解耦
  - 同步生成 + 前端 2s 轮询 (与数字人视频页一致)
  - 产物目录: <hf_visual_root>/<job_id>/
  - 删除走 send2trash (CLAUDE.md 铁律), 兜底 shutil.rmtree(ignore_errors=True)
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_config
from ..database import get_db
from ..models import VisualRenderJob
from ..schemas import (
    GenerateVisualResponse,
    TemplateInfo,
    VisualRenderJobCreate,
    VisualRenderJobOut,
)
from ..services.template_library import (
    get_template,
    list_templates,
    validate_input,
)
from ..services.visual_render_service import execute_visual_render_job
from ..services.file_utils import safe_trash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/visual-render", tags=["visual_render"])

ALLOWED_FRAME_KINDS = {"first", "middle", "final"}


def _safe_trash(path: Path) -> bool:
    """Best-effort send2trash; fall back to shutil.rmtree if send2trash missing."""
    return safe_trash(path)


# ---------------------------------------------------------------------------
# 1. 列模板
# ---------------------------------------------------------------------------
@router.get("/templates", response_model=list[TemplateInfo])
def list_available_templates():
    """Return full metadata (incl. json_schema) for every available template."""
    out: list[TemplateInfo] = []
    for t in list_templates():
        meta = get_template(t["template_id"]) or {}
        out.append(
            TemplateInfo(
                template_id=t["template_id"],
                version=t["version"],
                composition_id=t["composition_id"],
                duration_sec_range=t["duration_sec_range"],
                required_input=t["required_input"],
                json_schema=meta.get("json_schema", {}),
            )
        )
    return out


# ---------------------------------------------------------------------------
# 2. 列任务
# ---------------------------------------------------------------------------
@router.get("/jobs", response_model=list[VisualRenderJobOut])
def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(VisualRenderJob).order_by(VisualRenderJob.created_at.desc())
    if status:
        q = q.filter(VisualRenderJob.status == status)
    return q.limit(limit).all()


# ---------------------------------------------------------------------------
# 3. 创建任务
# ---------------------------------------------------------------------------
@router.post("/jobs", response_model=VisualRenderJobOut)
def create_job(payload: VisualRenderJobCreate, db: Session = Depends(get_db)):
    """Validate the input against the template's jsonschema, then persist ``queued``."""
    if get_template(payload.template_id) is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown template_id: {payload.template_id}",
        )
    try:
        validate_input(payload.template_id, payload.input_data)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Input validation failed: {exc}",
        ) from exc

    meta = get_template(payload.template_id) or {}
    job = VisualRenderJob(
        template_id=payload.template_id,
        template_version=meta.get("version", "1.0.0"),
        composition_id=meta.get("composition_id", "news_main"),
        status="queued",
        input_json=payload.input_data,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# 4. 详情
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}", response_model=VisualRenderJobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(VisualRenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


# ---------------------------------------------------------------------------
# 5. 同步生成
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/generate", response_model=GenerateVisualResponse)
def generate_job(job_id: str, db: Session = Depends(get_db)):
    """Drive the orchestrator: preparing → rendering → validating → completed/failed."""
    job = db.get(VisualRenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if job.status in ("rendering", "preparing", "validating"):
        raise HTTPException(
            status_code=409,
            detail=f"job is already {job.status}, cannot start",
        )

    result = execute_visual_render_job(
        db=db,
        job_id=job_id,
        template_id=job.template_id,
        input_data=job.input_json or {},
    )
    return GenerateVisualResponse(
        job_id=job_id,
        status=result.get("status", "failed"),
        output_path=result.get("output_path"),
        manifest_path=result.get("manifest_path"),
        media=result.get("media", {}),
        render_seconds=result.get("render_seconds"),
        warnings=result.get("warnings", []),
        error_code=result.get("error_code"),
        error_message=result.get("error_message"),
    )


# ---------------------------------------------------------------------------
# 6. 下载产物 mp4
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/download")
def download_video(job_id: str, db: Session = Depends(get_db)):
    job = db.get(VisualRenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"job status is {job.status}, cannot download",
        )
    if not job.output_path:
        raise HTTPException(status_code=500, detail="job.completed but output_path is empty")
    p = Path(job.output_path)
    if not p.exists():
        raise HTTPException(status_code=410, detail=f"file missing on disk: {p}")
    return FileResponse(
        path=str(p),
        media_type="video/mp4",
        filename=f"{job_id}.mp4",
    )


# ---------------------------------------------------------------------------
# 7. 下载关键帧 png
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/frames/{kind}")
def download_frame(job_id: str, kind: str, db: Session = Depends(get_db)):
    if kind not in ALLOWED_FRAME_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(ALLOWED_FRAME_KINDS)}",
        )
    job = db.get(VisualRenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    pf = job.preview_frames or {}
    rel = pf.get(kind) or ""
    if not rel:
        raise HTTPException(status_code=404, detail=f"frame '{kind}' not available for this job")
    p = Path(rel)
    if not p.exists():
        raise HTTPException(status_code=410, detail=f"frame file missing: {p}")
    return FileResponse(path=str(p), media_type="image/png", filename=f"{job_id}_{kind}.png")


# ---------------------------------------------------------------------------
# 8. 删除任务 + 产物目录
# ---------------------------------------------------------------------------
@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(VisualRenderJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")

    # Trash the workspace directory if known
    output_dir = job.output_dir
    db.delete(job)
    db.commit()

    if output_dir:
        p = Path(output_dir).parent  # workspace = output_dir.parent
        if p.exists():
            ok = _safe_trash(p)
            logger.info("trash workspace %s = %s", p, ok)

    return None