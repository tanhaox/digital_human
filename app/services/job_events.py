"""Job event bus — in-memory SSE channels (2026-09-17 T4 自 app/routers/jobs.py 逐字平移).

与 director_events.py 的边界: 本总线供工作线程直接 put_nowait (订阅队列容量 64
由 jobs.py SSE 端点创建); director_events 是 set_event_loop + call_soon_threadsafe
跨线程投递总线 — 两者语义不同, 合并须先出行为差异清单 (REFACTOR_PLAN T16b)。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

# 终止型事件类型 (SSE 流收到即断流): *_done/*_error 成对 + 两个取消类.
# B10 (2026-10-16): 自 jobs.py 内联硬编码清单抽出 — 新增 job 类型只改这里,
# 全仓唯一一份 (前端各页对单类型的 UI 分支不算清单副本).
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({
    "rewrite_done", "rewrite_error",
    "tts_done", "tts_error", "tts_cancelled",
    "carnival_done", "carnival_error",
    "correct_done", "correct_error",
    "material_done", "material_error",
    "anim_done", "anim_error", "anim_cancelled",
    "bible_done", "bible_error",
    "samples_done", "samples_error",
    "board_done", "board_error",
    "retune_done", "retune_error",
    "chars_done", "chars_error",
})


def is_terminal_event(event_type: str) -> bool:
    """该事件后 SSE 流应断开 (done/error/取消 三类收口)."""
    return event_type in TERMINAL_EVENT_TYPES


# In-memory channels: job_id -> list[asyncio.Queue
_job_channels: dict[str, list[asyncio.Queue]] = {}
_lock = threading.Lock()


def _publish(job_id: str, data: dict[str, Any]) -> None:
    """Publish event to all subscribers of a job channel (thread-safe)."""
    with _lock:
        queues = list(_job_channels.get(job_id, []))
    for q in queues:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass
