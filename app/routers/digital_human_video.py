"""DigitalHumanVideo router — LTX 2.3 音频+分镜图 → MP4.

8 端点:
  GET  /api/dhv/eligible-audio          列已生成 wav 候选
  GET  /api/dhv/videos                  列任务
  POST /api/dhv/videos                  创建任务 (status=pending, 仅校验参数)
  GET  /api/dhv/videos/{id}             详情
  POST /api/dhv/videos/{id}/upload-storyboard  上传分镜图 → 行内 storyboard_paths
  POST /api/dhv/videos/{id}/generate    同步: 聚合音频 → 构造 workflow → ComfyUI → ffprobe 校验 → 落盘
  GET  /api/dhv/videos/{id}/download   下载产物 mp4 (FileResponse)
  DELETE /api/dhv/videos/{id}           删除任务

设计要点:
  - 与 IndexTTS2 一致: 同步生成 + 后台 SSE 模式备选 (本任务选同步, 5-10s 音频对应 1-3min 出视频)
  - 视频落盘到 E:/数字人计划/dhv/<video_id>/<video_id>.mp4
  - 生成 6 步编排下沉到 app/services/dhv_service.py (行为不变)
  - ffprobe 验证三件套 (用户禁忌: 不要跳过音频流/时长/帧率)
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import get_db
from app.models import AudioFile, DigitalHumanVideo, Role
from app.schemas import (
    DigitalHumanVideoCreate,
    DigitalHumanVideoOut,
    EligibleAudioItem,
    EligibleAudioResponse,
    GenerateVideoResponse,
    StoryboardUploadResponse,
)
from app.services import dhv_service
from app.services.file_utils import safe_trash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dhv", tags=["digital_human_video"])


# ---------------------------------------------------------------------------
# 1. 列候选音频
# ---------------------------------------------------------------------------
@router.get("/eligible-audio", response_model=EligibleAudioResponse)
def eligible_audio(limit: int = 50, db: Session = Depends(get_db)):
    """扫 AudioFile 中 duration ≥ 0.5s 且 file_path 存在的 wav."""
    rows = (
        db.query(AudioFile)
        .order_by(AudioFile.created_at.desc())
        .limit(limit * 3)  # 多取一些, 再过滤
        .all()
    )
    items: list[EligibleAudioItem] = []
    total = 0.0
    for af in rows:
        p = Path(af.file_path)
        if not p.exists():
            continue
        if af.duration is not None and af.duration < 0.5:
            continue
        items.append(
            EligibleAudioItem(
                label=f"{af.filename} ({af.duration:.1f}s)"
                if af.duration
                else af.filename,
                path=str(p),
                duration_sec=af.duration,
                sample_rate=af.sample_rate,
                source_job_id=af.audio_job_id,
                source_segment_id=af.segment_id,
            )
        )
        total += af.duration or 0.0
        if len(items) >= limit:
            break
    return EligibleAudioResponse(items=items, total_duration_sec=round(total, 2))


# ---------------------------------------------------------------------------
# 2. 列表
# ---------------------------------------------------------------------------
@router.get("/videos", response_model=list[DigitalHumanVideoOut])
def list_videos(limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(DigitalHumanVideo)
        .order_by(DigitalHumanVideo.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# 3. 创建
# ---------------------------------------------------------------------------
@router.post("/videos", response_model=DigitalHumanVideoOut, status_code=201)
def create_video(body: DigitalHumanVideoCreate, db: Session = Depends(get_db)):
    if not body.audio_source_paths:
        raise HTTPException(status_code=400, detail="audio_source_paths 不能为空")
    if body.role_id:
        role = db.query(Role).filter(Role.id == body.role_id).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
    # audio path 实际校验留给 generate 时 (先给 404 太严格)
    vid = DigitalHumanVideo(
        role_id=body.role_id,
        workflow_name="digital_human_video_ltx23",
        audio_source_paths=list(body.audio_source_paths),
        storyboard_prompts=list(body.storyboard_prompts),
        target_duration_sec=body.target_duration_sec,
        fps=body.fps,
        width=body.width,
        height=body.height,
        seed=body.seed,
        status="pending",
    )
    db.add(vid)
    db.commit()
    db.refresh(vid)
    return vid


# ---------------------------------------------------------------------------
# 4. 详情
# ---------------------------------------------------------------------------
@router.get("/videos/{video_id}", response_model=DigitalHumanVideoOut)
def get_video(video_id: str, db: Session = Depends(get_db)):
    v = db.query(DigitalHumanVideo).filter(DigitalHumanVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="DigitalHumanVideo not found")
    return v


# ---------------------------------------------------------------------------
# 5. 上传分镜图
# ---------------------------------------------------------------------------
@router.post("/videos/{video_id}/upload-storyboard", response_model=StoryboardUploadResponse)
async def upload_storyboard(
    video_id: str,
    files: list[UploadFile] = File(...),
    prompts: str = Form(default=""),
    db: Session = Depends(get_db),
):
    v = db.query(DigitalHumanVideo).filter(DigitalHumanVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="DigitalHumanVideo not found")
    if v.status not in ("pending",):
        raise HTTPException(status_code=409, detail=f"video status={v.status} 不允许上传分镜")

    storyboard_dir = dhv_service.dhv_root() / video_id / "storyboard"
    storyboard_dir.mkdir(parents=True, exist_ok=True)

    uploaded: list[str] = []
    for f in files:
        suffix = Path(f.filename or "img.png").suffix or ".png"
        dest = storyboard_dir / f"{uuid.uuid4().hex[:8]}{suffix}"
        dest.write_bytes(await f.read())
        uploaded.append(str(dest))

    prompt_list = [p.strip() for p in prompts.split("\n") if p.strip()] if prompts else []
    v.storyboard_paths = list(v.storyboard_paths) + uploaded
    v.storyboard_prompts = list(v.storyboard_prompts) + prompt_list
    db.commit()
    db.refresh(v)
    return StoryboardUploadResponse(
        video_id=v.id,
        uploaded_paths=uploaded,
        storyboard_prompts=list(v.storyboard_prompts),
    )


# ---------------------------------------------------------------------------
# 6. 生成 (核心) — 6 步编排在 dhv_service.generate_dhv_video
# ---------------------------------------------------------------------------
@router.post("/videos/{video_id}/generate", response_model=GenerateVideoResponse)
async def generate_video(video_id: str, db: Session = Depends(get_db)):
    """同步: 聚合音频 → 构造 workflow → ComfyUI /prompt → 轮询 → 落盘 → ffprobe 校验."""
    v = db.query(DigitalHumanVideo).filter(DigitalHumanVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="DigitalHumanVideo not found")
    if v.status in ("completed", "running"):
        raise HTTPException(status_code=409, detail=f"video status={v.status}")

    if not v.storyboard_paths:
        raise HTTPException(status_code=400, detail="storyboard_paths 为空, 请先 upload-storyboard")

    v.status = "running"
    v.error_message = None
    db.commit()
    db.refresh(v)

    cfg = get_config()
    result = await dhv_service.generate_dhv_video(db, v, cfg)
    return GenerateVideoResponse(**result)


# ---------------------------------------------------------------------------
# 7. 下载
# ---------------------------------------------------------------------------
@router.get("/videos/{video_id}/download")
def download_video(video_id: str, db: Session = Depends(get_db)):
    v = db.query(DigitalHumanVideo).filter(DigitalHumanVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="DigitalHumanVideo not found")
    if not v.output_video_path:
        raise HTTPException(status_code=409, detail="video 未生成产物")
    p = Path(v.output_video_path)
    if not p.exists():
        raise HTTPException(status_code=410, detail="video 文件丢失")
    return FileResponse(str(p), media_type="video/mp4", filename=p.name)


# ---------------------------------------------------------------------------
# 8. 删除
# ---------------------------------------------------------------------------
@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: str, db: Session = Depends(get_db)):
    v = db.query(DigitalHumanVideo).filter(DigitalHumanVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="DigitalHumanVideo not found")
    # 删除产物目录 — 优先走回收站 (CLAUDE.md 铁律)
    out_dir = dhv_service.dhv_root() / video_id
    if out_dir.exists():
        safe_trash(out_dir)
    db.delete(v)
    db.commit()
