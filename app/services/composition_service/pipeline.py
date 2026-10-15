"""Composition service — 主编排: compose_director_job 全流程。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import DirectorJob
from app.services.composition_service.audio import (
    _build_slot_segments,
    _mix_master_track,
)
from app.services.composition_service.common import (
    _ensure_root,
    _job_root,
    _now,
    _HOST_WF,
)
from app.services.composition_service.ffmpeg_steps import (
    _concat_demuxer_concat,
    _loudnorm,
)
from app.services.composition_service.output import (
    _finalize_cleanup,
    _finalize_output,
)
from app.services.composition_service.slots import (
    _collect_slot_sources,
    _dedupe_completed_slots,
)

logger = logging.getLogger(__name__)

__all__ = ["compose_director_job"]


def _detect_timeline_gaps(completed: list[Any]) -> list[str]:
    """Report gaps/overlaps between consecutive completed slots (>0.5s)."""
    issues: list[str] = []
    for i in range(1, len(completed)):
        prev_end = completed[i - 1].end_sec
        cur_start = completed[i].start_sec
        if abs(cur_start - prev_end) > 0.5:
            issues.append(f"gap between slot {i-1} and {i}: {cur_start - prev_end:.2f}s")
    return issues


def _maybe_idempotent_return(job: Any) -> dict[str, Any] | None:
    """Idempotent guard — return the idempotent result dict, or None to proceed."""
    # ── 幂等重入保护 (2026-08-07) ──────────────────────────────────────
    # 合成成功后 slots/ 素材默认保留 7 天 (slot_retention_days)。job 已
    # completed 且成片存在时直接幂等返回, 不再重跑 (slot 素材由保留策略兜底)。
    # 注意: job.completed_at 是时区感知 UTC, 不能与 naive 时间直接比较,
    #       因此只依据 status + 成片文件存在性判断。
    # 例外: 若自上次合成后有 slot 被重跑 (updated_at > 成片时间), 说明用户
    #       想重制素材后重新合成 → 不允许幂等跳过, 继续走真实合成路径。
    #       此时未重跑的 slot 素材仍在磁盘 (保留策略), 直接复用, 不返工。
    final_candidate = _job_root(job) / f"director_{job.id}.mp4"
    slots_changed_since_compose = False
    if job.status == "completed" and job.completed_at is not None:
        for sl in job.slots:
            if sl.updated_at is not None and sl.updated_at > job.completed_at:
                slots_changed_since_compose = True
                break
    if (
        job.status == "completed"
        and final_candidate.exists()
        and not slots_changed_since_compose
    ):
        logger.info(
            "[compose] job %s already completed with existing output, skip re-compose (idempotent)",
            job.id,
        )
        return {
            "ok": True,
            "output_path": str(final_candidate),
            "duration_sec": None,
            "manifest_path": str(_job_root(job) / "composition_manifest.json"),
            "error": None,
        }
    return None


def _run_build_pipeline(
    job: Any,
    root: Path,
    src_paths: list[Path],
    slot_segments: list[tuple[float, float, bool]] | None,
    *,
    total_duration: float,
    crossfade_sec: float,
    target_lufs: float,
    evt: Any = None,
) -> Path:
    """Step 2-4: concat → mix master audio → loudnorm. Returns final_normalized."""
    # Step 2: Concat — -c copy (各 slot 已在源头输出统一格式)
    if evt:
        evt({"type": "compose_step", "step": "concat", "msg": "拼接视频…"})
    concat_video = _concat_demuxer_concat(root, src_paths, crossfade_sec=crossfade_sec)

    # Step 3: Mix master TTS audio
    with_audio = _mix_master_track(
        job, root, concat_video, slot_segments,
        total_duration=total_duration, evt=evt,
    )

    # Step 4: Loudness normalization on the mixed audio.
    # 字幕体系已砍掉(2026-08-01): 不再烧录 ASS 字幕, 直接响度归一.
    if evt:
        evt({"type": "compose_step", "step": "loudnorm", "msg": "响度归一化…"})
    final_normalized = root / "normalized.mp4"
    _loudnorm(with_audio, final_normalized, target_lufs=target_lufs)
    return final_normalized


def _compose_failed(
    db: Any,
    job: Any,
    exc: Exception,
    evt: Any = None,
) -> dict[str, Any]:
    """Mark job failed, keep old output if present, return failure result dict."""
    logger.exception("Composition failed for job %s", job.id)
    job.status = "failed"
    job.error_message = f"composition failed: {exc}"
    job.completed_at = _now()
    db.commit()
    if evt:
        evt({"type": "compose_error", "msg": f"合成失败: {exc}"})
    # 失败但旧成片仍在: 保留旧成片供用户继续使用/下载 (不覆盖磁盘文件)
    final_candidate = _job_root(job) / f"director_{job.id}.mp4"
    old_output = str(final_candidate) if final_candidate.exists() else None
    return {
        "ok": False,
        "error": str(exc),
        "output_path": old_output,
        "duration_sec": None,
    }


def _prepare_slots(
    job: Any,
    evt: Any = None,
) -> tuple[list[Any], list[str], Path, list[tuple[float, float, bool]]] | None:
    """Dedupe completed slots, detect gaps, prepare root + segment timeline.

    Returns ``(completed, issues, root, slot_segments)``, or ``None`` when
    there are no completed slots to compose.
    """
    completed = [s for s in job.slots if s.status == "completed"]
    completed = _dedupe_completed_slots(completed, evt)
    if not completed:
        return None

    if evt:
        evt({"type": "compose_start", "msg": f"开始合成 {len(completed)} 个片段"})

    issues = _detect_timeline_gaps(completed)
    root = _ensure_root(job)

    # Build per-segment timeline so _mix_master_audio can build the TTS track
    # segment-by-segment: host slots get silence (ComfyUI audio is already in
    # the concat video), non-host slots get atrim'd TTS audio.
    slot_segments = _build_slot_segments(completed, _HOST_WF)
    logger.info("[compose] slot_segments=%s", slot_segments)
    return completed, issues, root, slot_segments


def compose_director_job(
    db: Session,
    job_id: str,
    *,
    crossfade_sec: float = 0.2,
    target_lufs: float = -14.0,
    keep_intermediates: bool = False,
    evt: Any = None,
) -> dict[str, Any]:
    """Compose all completed slots of a DirectorJob into final 9:16 MP4.

    Args:
        keep_intermediates: If True, retain intermediate build files
            (normalized_*.mp4, concat_raw.mp4, etc.) for debugging.
            Default False — only final output + manifest are kept.
        evt: Optional callback ``evt(data: dict)`` for SSE progress events.

    Returns:
        {"ok": bool, "output_path": str|None, "duration_sec": float|None,
         "error": str|None, "manifest_path": str|None}
    """
    job = db.get(DirectorJob, job_id)
    if job is None:
        return {"ok": False, "error": f"DirectorJob {job_id} not found"}

    idempotent = _maybe_idempotent_return(job)
    if idempotent is not None:
        return idempotent

    prepared = _prepare_slots(job, evt)
    if prepared is None:
        return {"ok": False, "error": "no completed slots"}
    completed, issues, root, slot_segments = prepared

    try:
        # Step 1: Validate slot outputs exist + build metadata
        # (不再 re-encode — 各 workflow 已在源头输出统一分辨率/帧率/48kHz 音轨)
        src_paths, slot_meta = _collect_slot_sources(db, completed, evt, root=root)

        # Step 2-4: concat → mix → loudnorm
        total_duration = completed[-1].end_sec if completed else 0.0
        final_normalized = _run_build_pipeline(
            job, root, src_paths, slot_segments,
            total_duration=total_duration,
            crossfade_sec=crossfade_sec, target_lufs=target_lufs, evt=evt,
        )

        # Step 5: Validate
        if evt:
            evt({"type": "compose_step", "step": "validate", "msg": "校验输出…"})
        final_path, manifest_path, duration_actual = _finalize_output(
            db, job, final_normalized,
            slot_meta=slot_meta,
            target_lufs=target_lufs,
            crossfade_sec=crossfade_sec,
            issues=issues,
        )

        if evt:
            evt({"type": "compose_done", "msg": f"合成完成: {duration_actual:.1f}s",
                 "duration_sec": duration_actual, "output_path": str(final_path)})

        # Step 6: Clean up intermediate build artifacts
        _finalize_cleanup(root, keep_intermediates, evt)

        # Step 7: (策略升级 2026-08-07) 不再删除 slots/ — slot 素材保留至
        # job 删除或过期保留扫描 (main._auto_cleanup_stale_jobs)。否则局部重制
        # (retry_by_workflow / retry_slot) 后其余 slot 输出缺失 → 全量 REPAIR 返工。
        return {
            "ok": True,
            "output_path": str(final_path),
            "duration_sec": duration_actual,
            "manifest_path": str(manifest_path),
            "error": None,
        }
    except Exception as exc:
        return _compose_failed(db, job, exc, evt)
