"""Whisper 转录 → 段级"粗词" — 规避 CTranslate2 4.6.0 align() bug.

行为逐字迁移自原 alignment_service.py (2026-08-08 包化重构).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from app.services.alignment_service._models import _ensure_model
from app.services.director_events import PlanCancelled

if TYPE_CHECKING:  # 0919 pyflakes 补漏: 注解用 WhisperModel, 运行时实例化走 _ensure_model
    from faster_whisper import WhisperModel

logger = __import__("logging").getLogger(__name__)

__all__ = ["WordSegment", "_normalize", "_transcribe_words"]


@dataclass
class WordSegment:
    text: str
    start: float
    end: float
    probability: float


def _normalize(text: str) -> str:
    """Strip whitespace and collapse internal spaces."""
    return " ".join(text.split())


def _transcribe_words(
    audio_path: Path,
    model: "WhisperModel" | None = None,
    language: str = "zh",
    vad_filter: bool = True,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[WordSegment]:
    """Transcribe audio, return segment-level timestamps as coarse "words".

    word_timestamps=False 规避 CTranslate2 4.6.0 align() bug; suppress_tokens
    不传 (空列表会崩 generate); 下游 _match_segments 按字符占比分布.
    """
    if model is None:
        model = _ensure_model()

    if on_event:
        on_event({"type": "alignment_progress", "msg": "Whisper 转录开始…"})

    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=False,  # ← CTranslate2 4.6.0 workaround
        vad_filter=vad_filter,
        vad_parameters={
            "threshold": 0.5,
            "min_speech_duration_ms": 250,
            "max_speech_duration_s": 30.0,
        },
    )

    words = _collect_words(segments, is_cancelled, on_event)

    if on_event:
        on_event({
            "type": "alignment_progress",
            "msg": f"Whisper 转录完成: {len(words)} segments",
            "segments_seen": len(words),
        })
    return words


def _collect_words(
    segments: list[Any],
    is_cancelled: Callable[[], bool] | None,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> list[WordSegment]:
    """把 Whisper 段转成粗词, 每 10 段回传一次进度避免长音频全程静默."""
    words: list[WordSegment] = []
    last_emit = 0
    for idx, seg in enumerate(segments):
        if is_cancelled and is_cancelled():
            raise PlanCancelled
        text = _normalize(seg.text)
        if not text:
            continue
        if seg.start is None or seg.end is None:
            continue
        words.append(
            WordSegment(
                text=text,
                start=round(float(seg.start), 3),
                end=round(float(seg.end), 3),
                probability=1.0,  # segment-level, no per-word probability
            )
        )
        # 每收到 10 个 Whisper 段回传一次进度
        if on_event and len(words) - last_emit >= 10:
            on_event({
                "type": "alignment_progress",
                "msg": f"Whisper 转录中… ({len(words)} segments)",
                "segments_seen": len(words),
            })
            last_emit = len(words)
    return words
