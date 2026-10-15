"""Director Agent 2.0 — 主规划流水线 (create_director_plan).

阶段: 校验输入 → 对齐 → job 创建/更新 → LLM 工序单 (委托 _llm 模块) →
clamp / references 卡 / 落库 (委托 _postprocess)。编排函数仅编排, 每阶段独立 helper。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import DirectorJob
from app.services.director_events import PlanCancelled, publish as _evt
from app.services.director_service._alignment import _align_fast_or_whisper
from app.services.director_service._llm import _llm_plan_phase
from app.services.director_service._postprocess import (
    _append_source_slot,
    _clamp_slot_durations,
    _persist_plan,
)
from app.services.director_service._trace import append_trace

logger = logging.getLogger(__name__)

__all__ = ["create_director_plan"]


def _resolve_inputs(
    db: Session,
    script_id: str,
    audio_file_id: str,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    """校验 Script/AudioFile 存在, 构建对齐输入 segments."""
    from app.models import AudioFile, Script

    audio = db.get(AudioFile, audio_file_id)
    if audio is None:
        raise ValueError(f"AudioFile {audio_file_id} not found")
    script = db.get(Script, script_id)
    if script is None:
        raise ValueError(f"Script {script_id} not found")

    # Build alignment input from script segments
    segments = [
        {"id": seg.id, "text": seg.text}
        for seg in sorted(script.segments, key=lambda s: s.line_index)
        if seg.selected_for_host
    ]
    if not segments:
        segments = [{"id": None, "text": script.script_text}]
    return audio, script, segments


def _record_alignment_done(
    db: Session,
    job_id: str | None,
    alignment: dict[str, Any],
    segments: list[dict[str, Any]],
) -> None:
    """alignment 完成 → 环节轨迹 + 前端事件."""
    if job_id:
        _j = db.get(DirectorJob, job_id)
        if _j:
            ok_detail = (
                f"对齐完成 {alignment.get('total_duration_sec', 0):.1f}s / {len(segments)} 段"
                if alignment.get("ok")
                else f"对齐失败: {alignment.get('error')}"
            )
            append_trace(db, _j, "alignment", "done" if alignment.get("ok") else "error", ok_detail)

    if job_id:
        _evt(job_id, {"type": "alignment_done", "msg": f"对齐完成: {alignment.get('total_duration_sec', 0):.1f}s", "ok": alignment.get("ok")})


def _mark_plan_failed(
    db: Session,
    job_id: str | None,
    script: Any,
    script_id: str,
    audio_file_id: str,
    alignment: dict[str, Any],
) -> tuple[DirectorJob, dict[str, Any]]:
    """对齐失败: 标记既有 job failed 或新建 failed job."""
    if job_id:
        job = db.get(DirectorJob, job_id)
        if job:
            job.status = "failed"
            job.error_message = f"alignment failed: {alignment.get('error')}"
            db.commit()
            return job, alignment
    job = DirectorJob(
        script_id=script_id,
        audio_file_id=audio_file_id,
        video_format=script.video_format or "portrait",
        status="failed",
        error_message=f"alignment failed: {alignment.get('error')}",
    )
    db.add(job)
    db.commit()
    return job, alignment


def _ensure_director_job(
    db: Session,
    job_id: str | None,
    script: Any,
    script_id: str,
    audio_file_id: str,
    total_duration: float,
) -> tuple[DirectorJob, str | None]:
    """更新既有 job (有 job_id) 或新建 planning job (无 job_id/未命中)."""
    if job_id:
        job = db.get(DirectorJob, job_id)
        if job:
            job.total_duration_sec = total_duration
            job.video_format = script.video_format or "portrait"
            db.commit()
        else:
            job = DirectorJob(
                id=job_id,
                script_id=script_id,
                audio_file_id=audio_file_id,
                video_format=script.video_format or "portrait",
                status="planning",
                total_duration_sec=total_duration,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
    else:
        job = DirectorJob(
            script_id=script_id,
            audio_file_id=audio_file_id,
            video_format=script.video_format or "portrait",
            status="planning",
            total_duration_sec=total_duration,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    return job, job_id


def create_director_plan(
    db: Session,
    script_id: str,
    audio_file_id: str,
    *,
    job_id: str | None = None,
    material_catalog: dict[str, Any] | None = None,
    language: str = "zh",
    is_cancelled: Callable[[], bool] | None = None,
    enabled_pipelines: set[str] | None = None,
) -> tuple[DirectorJob, dict[str, Any]]:
    """Run Whisper alignment + LLM slot plan on an existing or new DirectorJob."""
    audio, script, segments = _resolve_inputs(db, script_id, audio_file_id)

    if job_id:
        _evt(job_id, {"type": "alignment_progress", "msg": f"对齐 {len(segments)} 段…"})

    if is_cancelled and is_cancelled():
        raise PlanCancelled

    alignment = _align_fast_or_whisper(
        db,
        audio,
        script_id,
        segments,
        job_id=job_id,
        language=language,
        is_cancelled=is_cancelled,
    )
    _record_alignment_done(db, job_id, alignment, segments)

    if is_cancelled and is_cancelled():
        raise PlanCancelled

    if not alignment.get("ok"):
        return _mark_plan_failed(db, job_id, script, script_id, audio_file_id, alignment)

    total_duration = float(alignment.get("total_duration_sec") or 0.0)
    job, job_id = _ensure_director_job(
        db, job_id, script, script_id, audio_file_id, total_duration,
    )

    if job_id:
        _evt(job_id, {"type": "llm_start", "msg": "LLM 生成工序单中…"})

    if is_cancelled and is_cancelled():
        raise PlanCancelled

    script_title = script.article.title if script.article else None
    plan = _llm_plan_phase(
        db, job, job_id, alignment, script, script_title,
        material_catalog, total_duration, enabled_pipelines, is_cancelled,
    )
    if plan is None:
        return job, alignment

    if job_id:
        _evt(job_id, {"type": "llm_done", "msg": f"LLM 工序单完成: {len(plan.slots)} slots"})

    if is_cancelled and is_cancelled():
        raise PlanCancelled

    _clamp_slot_durations(plan, total_duration)
    _append_source_slot(db, plan, script, total_duration)
    _persist_plan(db, job, plan, script_title)
    return job, alignment
