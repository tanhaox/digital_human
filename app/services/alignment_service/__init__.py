"""Whisper forced alignment — map script segments to real audio timings.

Pipeline:
1. faster-whisper transcribe the whole audio with word timestamps.
2. Greedily group word-level timestamps into segments by matching segment text.
3. Return word_segments + per-segment start/end/duration.

This is intentionally a backend service (no HTTP client here).

行为逐字迁移自原 alignment_service.py (2026-08-08 包化重构).
"""
from __future__ import annotations

import os

# Force HuggingFace Hub offline — model is cached locally, network is unreliable
os.environ["HF_HUB_OFFLINE"] = "1"

# Auto-patch tokenizer.json before faster_whisper loads it
# (adds 1609 Whisper special tokens to fix token_to_id() returning None).
# 该补丁在 import 时自动执行(幂等), 必须先于 faster_whisper 加载生效.
from app.services.tokenizer_fix import apply_tokenizer_fix  # noqa: E402,F401  (触发副作用)

from app.services.alignment_service._align import align_script_segments
from app.services.alignment_service._timings import (
    SegmentTiming,
    align_from_tts_durations,
)
from app.services.alignment_service._transcribe import WordSegment

__all__ = [
    "align_script_segments",
    "align_from_tts_durations",
    "WordSegment",
    "SegmentTiming",
]
