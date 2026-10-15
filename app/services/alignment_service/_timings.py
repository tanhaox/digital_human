"""时间轴铺排 — 脚本段按字符占比分布到音频, 含 TTS 时长快路径.

行为逐字迁移自原 alignment_service.py (2026-08-08 包化重构).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.alignment_service._transcribe import _normalize
from app.services.director_events import PlanCancelled

__all__ = ["SegmentTiming", "_match_segments", "align_from_tts_durations"]


@dataclass
class SegmentTiming:
    segment_id: str
    text: str
    start: float
    end: float
    duration: float
    words: list[Any]


def _empty_timings(segments: list[dict[str, Any]]) -> list[SegmentTiming]:
    """空输入: 每个脚本段返回全零 timing."""
    timings: list[SegmentTiming] = []
    for seg in segments:
        timings.append(
            SegmentTiming(
                segment_id=str(seg.get("id", "")),
                text=_normalize(seg.get("text") or ""),
                start=0.0, end=0.0, duration=0.0, words=[],
            )
        )
    return timings


def _alloc_segment(
    seg: dict[str, Any],
    total_chars: int,
    total_duration: float,
    total_end: float,
    current_time: float,
) -> tuple[SegmentTiming, float]:
    """按字符占比分配一个脚本段的时长并推进 current_time."""
    seg_text = _normalize(seg.get("text") or "")
    seg_id = str(seg.get("id", ""))
    char_count = len(seg_text)

    if char_count == 0:
        seg_duration = 0.0
    else:
        seg_duration = (char_count / total_chars) * total_duration

    seg_end = min(current_time + seg_duration, total_end)
    duration = max(0.0, round(seg_end - current_time, 3))

    timing = SegmentTiming(
        segment_id=seg_id,
        text=seg_text,
        start=round(current_time, 3),
        end=round(seg_end, 3),
        duration=duration,
        words=[],
    )
    return timing, seg_end


def _emit_match_progress(
    on_event: Callable[[dict[str, Any]], None] | None,
    idx: int,
    total: int,
) -> None:
    """每处理 20 个脚本段回传一次进度."""
    if on_event and (idx + 1) % 20 == 0:
        on_event({
            "type": "alignment_progress",
            "msg": f"文本-音频匹配中… ({idx + 1}/{total})",
            "matched": idx + 1,
            "total": total,
        })


def _layout_bounds(
    words: list[Any],
) -> tuple[float, float, float]:
    """时间轴边界: (total_start, total_end, total_duration>=0.001)."""
    total_start = words[0].start
    total_end = words[-1].end
    total_duration = max(0.001, total_end - total_start)
    return total_start, total_end, total_duration


def _total_chars_of(segments: list[dict[str, Any]]) -> int:
    """全脚本段字符总数; 为 0 时回退为段数 (均分)."""
    total_chars = sum(len(_normalize(s.get("text") or "")) for s in segments)
    return total_chars if total_chars else len(segments)


def _match_segments(
    words: list[Any],
    segments: list[dict[str, Any]],
    on_event: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[SegmentTiming]:
    """Distribute script segments proportionally across the audio timeline.

    word_timestamps 关闭后每个"词"实际是粗粒度 Whisper 段, 改为按字符占比
    分布时间 — 中文 TTS 语速大致与文本长度成正比, 是可靠近似.
    """
    if on_event:
        on_event({"type": "alignment_progress", "msg": f"文本-音频匹配中… ({len(segments)} 个脚本段)"})

    timings: list[SegmentTiming] = []

    if not words or not segments:
        return _empty_timings(segments)

    total_start, total_end, total_duration = _layout_bounds(words)
    total_chars = _total_chars_of(segments)

    current_time = total_start
    for i, seg in enumerate(segments):
        if is_cancelled and is_cancelled():
            raise PlanCancelled
        timing, current_time = _alloc_segment(
            seg, total_chars, total_duration, total_end, current_time,
        )
        timings.append(timing)
        _emit_match_progress(on_event, i, len(segments))

    if on_event:
        on_event({
            "type": "alignment_progress",
            "msg": f"文本-音频匹配完成 ({len(timings)} timings)",
            "matched": len(timings),
            "total": len(segments),
        })
    return timings


def _tts_fast_timings(
    segments: list[dict[str, Any]],
    is_cancelled: Callable[[], bool] | None,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> tuple[list[SegmentTiming], float]:
    """TTS 段落时长 → 简单拼接时间轴, 每 20 段回传一次进度."""
    timings: list[SegmentTiming] = []
    current = 0.0
    for i, seg in enumerate(segments):
        if is_cancelled and is_cancelled():
            raise PlanCancelled
        seg_id = str(seg.get("id") or "")
        seg_text = _normalize(seg.get("text") or "")
        dur = float(seg.get("duration") or 0.0)
        end = current + dur
        timings.append(
            SegmentTiming(
                segment_id=seg_id,
                text=seg_text,
                start=round(current, 3),
                end=round(end, 3),
                duration=round(dur, 3),
                words=[],
            )
        )
        current = end
        # 每处理 20 个脚本段回传一次进度
        if on_event and (i + 1) % 20 == 0:
            on_event({
                "type": "alignment_progress",
                "msg": f"文本-音频匹配中… ({i + 1}/{len(segments)})",
                "matched": i + 1,
                "total": len(segments),
            })
    return timings, round(current, 3)


def _to_timing_dict(t: SegmentTiming) -> dict[str, Any]:
    """SegmentTiming → 返回 dict (键序一致)."""
    return {
        "segment_id": t.segment_id,
        "text": t.text,
        "start": t.start,
        "end": t.end,
        "duration": t.duration,
    }


def align_from_tts_durations(
    segments: list[dict[str, Any]],
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fast path — build segment timings straight from TTS audio durations.

    ID-024: TTS 已按脚本段产出带 duration 的 wav, 无需 Whisper 重转录 — 直接
    按简单拼接铺时间轴 (每段起点=上一段终点). 返回形状同 align_script_segments.
    """
    if on_event:
        on_event({
            "type": "alignment_progress",
            "msg": f"使用 TTS 段落时长建时间轴… ({len(segments)} segments)",
        })

    timings, total_duration = _tts_fast_timings(segments, is_cancelled, on_event)

    if on_event:
        on_event({
            "type": "alignment_progress",
            "msg": f"对齐完成: {total_duration:.1f}s (TTS 时长快路径)",
            "matched": len(timings),
            "total": len(segments),
        })

    return {
        "ok": True,
        "error": None,
        "word_segments": [],
        "segment_timings": [_to_timing_dict(t) for t in timings],
        "total_duration_sec": total_duration,
        "model": "tts-durations",
        "device": "n/a",
    }
