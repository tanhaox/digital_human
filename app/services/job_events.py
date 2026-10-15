"""Job event bus — in-memory SSE channels (2026-09-17 T4 自 app/routers/jobs.py 逐字平移).

与 director_events.py 的边界: 本总线供工作线程直接 put_nowait (订阅队列容量 64
由 jobs.py SSE 端点创建); director_events 是 set_event_loop + call_soon_threadsafe
跨线程投递总线 — 两者语义不同, 合并须先出行为差异清单 (REFACTOR_PLAN T16b)。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

# In-memory channels: job_id -> list of asyncio.Queue
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
