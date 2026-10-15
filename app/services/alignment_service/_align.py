"""对外主入口 — 完整对齐管线 (whisper + 字符占比匹配).

行为逐字迁移自原 alignment_service.py (2026-08-08 包化重构).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from app.config import get_config
from app.services.alignment_service._models import _ensure_model
from app.services.alignment_service._timings import (
    SegmentTiming,
    _match_segments,
    _to_timing_dict,
)
from app.services.alignment_service._transcribe import WordSegment, _transcribe_words
from app.services.director_events import PlanCancelled

logger = __import__("logging").getLogger(__name__)

__all__ = ["align_script_segments"]


def _validate_audio(
    audio_path: Path,
) -> dict[str, Any] | None:
    """音频/ffmpeg 缺失 → 返回错误 dict, 否则 None."""
    if not audio_path.exists():
        return {"ok": False, "error": f"audio not found: {audio_path}", "word_segments": [], "segment_timings": []}
    if not shutil.which("ffmpeg"):
        return {"ok": False, "error": "ffmpeg not on PATH", "word_segments": [], "segment_timings": []}
    return None


def _align_whisper(
    audio_path: Path,
    segments: list[dict[str, Any]],
    size: str,
    dev: str,
    language: str,
    on_event: Callable[[dict[str, Any]], None] | None,
    is_cancelled: Callable[[], bool] | None,
) -> tuple[list[WordSegment], list[SegmentTiming]]:
    """whisper 转录 + 字符占比匹配, 返回 (words, timings)."""
    model = _ensure_model(size, dev, on_event=on_event)
    words = _transcribe_words(
        audio_path, model=model, language=language,
        on_event=on_event, is_cancelled=is_cancelled,
    )
    timings = _match_segments(words, segments, on_event=on_event, is_cancelled=is_cancelled)
    return words, timings


def _success_result(
    words: list[WordSegment],
    timings: list[SegmentTiming],
    total_duration: float,
    model: str,
    device: str,
) -> dict[str, Any]:
    """成功结果 dict — 形状与键序与原实现一致."""
    word_segments = [
        {"text": w.text, "start": w.start, "end": w.end, "probability": w.probability}
        for w in words
    ]
    segment_timings = [_to_timing_dict(t) for t in timings]
    return {
        "ok": True,
        "error": None,
        "word_segments": word_segments,
        "segment_timings": segment_timings,
        "total_duration_sec": total_duration,
        "model": model,
        "device": device,
    }


def _align_error(
    audio_path: Path,
    exc: Exception,
    model: str,
    device: str,
) -> dict[str, Any]:
    """whisper 失败 → 记录日志并返回错误 dict."""
    logger.exception("alignment failed for %s", audio_path)
    return {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "word_segments": [],
        "segment_timings": [],
        "total_duration_sec": None,
        "model": model,
        "device": device,
    }


def align_script_segments(
    audio_path: Path | str,
    segments: list[dict[str, Any]],
    *,
    model_size: str | None = None,
    device: str | None = None,
    language: str = "zh",
    on_event: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Align script segments to audio.

    audio_path: 完整音频路径 (wav/mp3); segments: [{id, text}, ...];
    model_size/device: whisper 覆盖参数; language: 默认 zh;
    on_event/is_cancelled: 进度回调 / 取消探测.
    Returns: {ok, word_segments, segment_timings, total_duration_sec,
              model, device, error}.
    """
    audio_path = Path(audio_path)
    error = _validate_audio(audio_path)
    if error is not None:
        return error

    cfg = get_config().defaults
    size = model_size or cfg.whisper_model_size
    dev = device or cfg.whisper_device

    try:
        words, timings = _align_whisper(
            audio_path, segments, size, dev, language, on_event, is_cancelled,
        )
    except PlanCancelled:
        raise
    except Exception as exc:
        return _align_error(audio_path, exc, size, dev)

    total_duration = round(words[-1].end if words else 0.0, 3)
    return _success_result(words, timings, total_duration, size, dev)
