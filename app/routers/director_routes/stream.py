"""Director SSE 执行进度流端点."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .._sse import sse_stream

logger = logging.getLogger(__name__)

stream_router = APIRouter(tags=["director"])

__all__ = ["stream_router"]


class _HeartbeatClock:
    """心跳 elapsed_sec 语义是"距最后一个真实事件" (前端可感知) — SSE 工厂的零参
    心跳 Callable 感知不到事件到达时机, 包一层 get() 钩子在每次事件落地时刷新
    时间戳; 事件帧本身仍由工厂编码发送, 工厂只调 queue.get()."""

    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        self.last_evt = time.monotonic()

    async def get(self):
        data = await self._queue.get()
        self.last_evt = time.monotonic()
        return data


@stream_router.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """Server-Sent Events: 实时推送 Phase/Slot 执行进度."""
    from app.services.director_events import subscribe, unsubscribe

    queue = subscribe(job_id)
    clock = _HeartbeatClock(queue)

    def _heartbeat() -> str:
        # 服务端无事件超过 20s 时，向前端发送可感知的心跳，避免日志面板长时间静默
        hb = {"type": "heartbeat", "elapsed_sec": int(time.monotonic() - clock.last_evt),
              "msg": "服务端仍在运行…"}
        return f"data: {json.dumps(hb, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse_stream(
            request, clock,
            heartbeat=_heartbeat,
            timeout=20.0,
            terminal_types=(
                "exec_done", "exec_cancelled", "plan_done",
                "plan_cancelled", "plan_error", "compose_done", "compose_error",
                "force_stopped",
            ),
            on_close=lambda: unsubscribe(job_id, queue),
        ),
        media_type="text/event-stream",
    )
