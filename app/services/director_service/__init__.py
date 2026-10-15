"""Director Agent 2.0 — public API for plan creation, slot replacement, job lifecycle.

Internal logic is split into:
- director_prompt: prompt loading and building
- director_parser: LLM output parsing and rule enforcement
"""
from __future__ import annotations

from app.services.director_service._alignment import (
    _align_fast_or_whisper,
    _collect_tts_segment_durations,
)
from app.services.director_service._fallback import replace_failed_slot
from app.services.director_service._lifecycle import (
    complete_job_if_slots_done,
    mark_job_reviewed,
)
from app.services.director_service._plan import create_director_plan
from app.services.director_service._trace import append_trace, get_trace

__all__ = [
    "append_trace",
    "complete_job_if_slots_done",
    "create_director_plan",
    "get_trace",
    "mark_job_reviewed",
    "replace_failed_slot",
]
