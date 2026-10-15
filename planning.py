"""Director 创建任务 + 后台规划线程端点.

原 `create_job`(74 行)/`_plan_in_background`(59 行)拆分: 音频解析、
校验、规划主流程、失败收敛各下沉为私有函数, 端点与线程助手均 ≤40 行。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import db_session, get_db
from app.models import AudioFile, DirectorJob, Script
from app.schemas import DirectorDirectResponse, DirectorJobCreate
from app.services.director_service import create_director_plan
from app.services.slot_executor import clear_cancel, is_cancelled
from app.routers.director_routes.common import _encode_pipelines, _decode_pipelines

logger = logging.getLogger(__name__)

planning_router = APIRouter(tags=["director"])

__all__ = ["planning_router", "_resolve_audio_id", "_validate_audio",
           "_plan_steps", "_mark_plan_failed", "_plan_in_background",
           "_spawn_plan_thread"]

_planning_jobs: set[str] = set()
_planning_lock = threading.Lock()


def _resolve_audio_id(script: Script, audio_id: str | None) -> str:
    """Auto-pick latest combined audio file for this script when not provided."""
    if audio_id:
        return audio_id
    latest = None
    for job in script.audio_jobs:
        if job.status != "completed":
            continue
        for af in job.audio_files:
            if af.segment_id is None and af.duration and af.duration >= 1.0:
                if latest is None or af.created_at > latest.created_at:
                    latest = af
    if latest is None:
        raise HTTPException(
            status_code=400,
            detail="audio_file_id 未提供,且找不到该脚本的可用整段音频 (先生成 TTS)",
        )
    return latest.id


def _validate_audio(db: Session, audio_id: str) -> None:
    """Load audio file row and verify its path still exists on disk."""
    audio = db.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"AudioFile {audio_id} not found")
    if not Path(audio.file_path).exists():
        raise HTTPException(status_code=409, detail=f"音频文件丢失: {audio.file_path}")


def _plan_steps(db, job_id: str, script_id: str, audio_id: str,
                enabled_pipelines: set[str] | None) -> None:
    """Alignment + material catalog + LLM planning; emit plan events."""
    from app.services.director_events import publish as _evt
    from app.services.director_prompt import build_real_material_catalog

    _evt(job_id, {"type": "plan_start", "msg": "开始规划: 音频对齐 + LLM 工序单"})
    _evt(job_id, {"type": "alignment_start", "msg": "音频对齐中… (段落时长完整时秒级完成)"})

    # ID-023: 规划阶段可取消 — 线程在创建后可能已被标记取消
    if is_cancelled(job_id):
        from app.services.director_events import PlanCancelled
        raise PlanCancelled()

    # 构建真实素材库目录（带 AI 多维标签），失败时内部 fallback 到 mock
    material_catalog = build_real_material_catalog(db)
    logger.info("[director %s] material_catalog: %d categories", job_id, len(material_catalog))

    job, alignment = create_director_plan(
        db, script_id, audio_id, job_id=job_id,
        material_catalog=material_catalog,
        is_cancelled=lambda: is_cancelled(job_id),
        enabled_pipelines=enabled_pipelines,
    )

    if job.status == "failed":
        _evt(job_id, {"type": "plan_error", "msg": job.error_message or "规划失败"})
    else:
        slot_count = len(job.slots)
        _evt(job_id, {"type": "plan_done", "msg": f"规划完成: {slot_count} slots", "slot_count": slot_count})
    logger.info("[director %s] background plan done, status=%s", job_id, job.status)


def _mark_plan_failed(db, job_id: str, msg: str, statuses=("planning",)) -> None:
    """Best-effort mark job failed inside planning thread on error paths."""
    try:
        j = db.get(DirectorJob, job_id)
        if j and j.status in statuses:
            j.status = "failed"
            j.error_message = msg
            db.commit()
    except Exception:
        pass


def _plan_in_background(job_id: str, script_id: str, audio_id: str,
                        enabled_pipelines: set[str] | None = None) -> None:
    """Run alignment + LLM planning in a dedicated thread."""
    from app.services.director_events import PlanCancelled, publish as _evt

    with db_session() as db:
        try:
            _plan_steps(db, job_id, script_id, audio_id, enabled_pipelines)
        except PlanCancelled:
            logger.info("[director %s] plan cancelled by user", job_id)
            _evt(job_id, {"type": "plan_cancelled", "msg": "用户取消，已停止规划"})
            _mark_plan_failed(db, job_id, "规划被用户取消", ("planning", "reviewing"))
        except Exception:
            logger.exception("[director %s] background plan crashed", job_id)
            _evt(job_id, {"type": "plan_error", "msg": "规划崩溃 (see server log)"})
            _mark_plan_failed(db, job_id, "create_director_plan crashed", ("planning",))
        finally:
            clear_cancel(job_id)
            with _planning_lock:
                _planning_jobs.discard(job_id)


def _spawn_plan_thread(job_id: str, script_id: str, audio_id: str,
                       enabled: set[str] | None) -> None:
    """Register planning job and launch the background planning thread."""
    with _planning_lock:
        _planning_jobs.add(job_id)
    t = threading.Thread(
        target=_plan_in_background,
        args=(job_id, script_id, audio_id, enabled),
        daemon=True,
        name=f"plan-{job_id[:8]}",
    )
    t.start()


@planning_router.post("/jobs", response_model=DirectorDirectResponse, status_code=201)
def create_job(body: DirectorJobCreate, db: Session = Depends(get_db)):
    """创建导演任务: 立即返回 job_id, 后台线程跑 alignment + LLM 规划.

    前端拿到 job_id 后连 SSE /jobs/{id}/events 获取进度.
    """
    script = db.get(Script, body.script_id)
    if script is None:
        raise HTTPException(status_code=404, detail=f"Script {body.script_id} not found")

    audio_id = _resolve_audio_id(script, body.audio_file_id)
    _validate_audio(db, audio_id)

    # 画幅覆盖 (2026-08-24): 显式传入且与现值不同 → 写回 script (单源真理),
    # 后续 plan/slot 渲染/合成/J线全部继承新画幅; 相同则不动。
    if body.video_format and body.video_format != script.video_format:
        logger.info(
            "[director] script %s video_format %s -> %s (create 覆盖)",
            body.script_id, script.video_format, body.video_format,
        )
        script.video_format = body.video_format
        db.commit()

    # 三态语义 (2026-08-07): None=全启用 / ""=全关 / "c,p"=部分启用, 与落库列 job.pipelines 一致。
    enabled: set[str] | None = _decode_pipelines(body.pipelines)

    # Create placeholder job immediately
    job = DirectorJob(
        script_id=body.script_id,
        audio_file_id=audio_id,
        view_group_index=body.view_group_index,
        pipelines=_encode_pipelines(enabled),
        status="planning",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id

    _spawn_plan_thread(job_id, body.script_id, audio_id, enabled)

    logger.info("[director %s] plan dispatched to background thread", job_id)
    return DirectorDirectResponse(
        job_id=job_id,
        status="planning",
        slot_count=0,
        message="规划已提交后台, 请通过 SSE 或轮询查看进度",
    )
