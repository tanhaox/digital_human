"""Director execution event bus — thread-safe pub/sub for SSE streaming.

Usage:
    from app.services.director_events import publish, subscribe, unsubscribe

    # In executor (any thread):
    publish(job_id, {"phase": 1, "step": "comfyui_start", "msg": "ComfyUI 启动中…"})

    # In SSE endpoint (async):
    queue = subscribe(job_id)
    data = await queue.get()
    unsubscribe(job_id, queue)
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any


class PlanCancelled(Exception):
    """Shared sentinel — user cancelled a planning job mid-flight.

    Raised across module boundaries (alignment_service -> director_service ->
    director router) so a user cancel during planning propagates cleanly
    instead of being swallowed by generic error handlers.
    """


# job_id -> list of asyncio.Queue
_channels: dict[str, list[asyncio.Queue]] = {}
_lock = threading.Lock()
# 保存 event loop 引用，用于跨线程安全调度
_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once at startup to capture the running event loop."""
    global _loop
    _loop = loop


def publish(job_id: str, data: dict[str, Any]) -> None:
    """Publish an event to all SSE subscribers of *job_id* (thread-safe)."""
    with _lock:
        queues = list(_channels.get(job_id, []))
    if not queues:
        return
    if _loop is not None and _loop.is_running():
        # 跨线程安全: 通过 call_soon_threadsafe 调度到 event loop
        for q in queues:
            _loop.call_soon_threadsafe(_safe_put, q, data)
    else:
        # fallback: 直接 put (同线程场景)
        for q in queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass


def _safe_put(q: asyncio.Queue, data: dict[str, Any]) -> None:
    try:
        q.put_nowait(data)
    except asyncio.QueueFull:
        pass


def subscribe(job_id: str) -> asyncio.Queue:
    """Create and register a new subscriber queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    with _lock:
        _channels.setdefault(job_id, []).append(q)
    return q


def unsubscribe(job_id: str, q: asyncio.Queue) -> None:
    """Remove a subscriber queue."""
    with _lock:
        queues = _channels.get(job_id, [])
        if q in queues:
            queues.remove(q)
