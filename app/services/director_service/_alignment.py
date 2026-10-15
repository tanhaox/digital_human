"""Director Agent 2.0 — 对齐: TTS 时长快速路径 / Whisper 重转录兜底.

ID-024: TTS pass 每段已产出带时长的 wav。当所有选中 host 段都有音频时长时,
时间线按拼接布局, 完全跳过 faster-whisper (规划从分钟级降到秒级)。
任一段缺时长 → 回退 Whisper 重转录, 保证时间线完整。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services.director_events import publish as _evt

logger = logging.getLogger(__name__)

__all__ = ["_align_fast_or_whisper", "_collect_tts_segment_durations"]


def _collect_tts_segment_durations(
    db: Session,
    script_id: str,
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Map each host segment to its TTS wav duration, or None if incomplete.

    Uses the latest completed AudioJob's audio_files for the script, keyed by
    segment_id. Returns None when any segment is missing a duration (caller
    should fall back to Whisper).
    """
    from app.models import AudioFile, AudioJob, Segment

    job = (
        db.query(AudioJob)
        .filter(AudioJob.script_id == script_id, AudioJob.status == "completed")
        .order_by(AudioJob.completed_at.desc())
        .first()
    )
    if job is None:
        return None

    dur_by_segment: dict[str, float] = {}
    for af in job.audio_files:
        if af.segment_id is not None and af.duration is not None:
            dur_by_segment[af.segment_id] = float(af.duration)

    result: list[dict[str, Any]] = []
    for seg in segments:
        seg_id = str(seg.get("id") or "")
        if seg_id not in dur_by_segment:
            return None  # incomplete → fall back to Whisper
        result.append(
            {
                "id": seg_id,
                "text": seg.get("text") or "",
                "duration": dur_by_segment[seg_id],
            }
        )
    return result if result else None


def _align_fast_or_whisper(
    db: Session,
    audio: Any,
    script_id: str,
    segments: list[dict[str, Any]],
    *,
    job_id: str | None = None,
    language: str = "zh",
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Align script segments to audio — prefer TTS durations, fall back to Whisper.

    ID-024: the TTS pass already produced one wav per segment with a known
    duration in ``audio_files``. When every selected host segment has an audio
    file with a duration, we lay out the timeline as a concatenation and skip
    faster-whisper entirely (planning drops from minutes to seconds). If any
    segment is missing a duration, fall back to Whisper re-transcription so the
    timeline stays complete.
    """
    from app.models import AudioFile, Segment
    from app.services.alignment_service import (
        align_from_tts_durations,
        align_script_segments,
    )

    if job_id:
        _evt(job_id, {"type": "alignment_progress", "msg": "检查 TTS 段落时长…"})

    # 1. Fast path: every host segment has a TTS wav duration.
    tts_segments = _collect_tts_segment_durations(db, script_id, segments)
    if tts_segments is not None:
        logger.info(
            "[director %s] fast path: %d segments aligned from TTS durations",
            job_id, len(tts_segments),
        )
        return align_from_tts_durations(
            tts_segments,
            on_event=lambda data: _evt(job_id, data) if job_id else None,
            is_cancelled=is_cancelled,
        )

    # 2. Fallback: full Whisper re-transcription of the paragraph audio.
    logger.info("[director %s] fast path unavailable, falling back to Whisper", job_id)
    if job_id:
        _evt(job_id, {"type": "alignment_progress", "msg": "段落时长不完整，改用 Whisper 转录…"})
    return align_script_segments(
        audio.file_path,
        segments,
        language=language,
        on_event=lambda data: _evt(job_id, data) if job_id else None,
        is_cancelled=is_cancelled,
    )
