"""Director SSE 执行进度流端点."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

stream_router = APIRouter(tags=["director"])

__all__ = ["stream_router"]


@stream_router.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """Server-Sent Events: 实时推送 Phase/Slot 执行进度."""
    from app.services.director_events import subscribe, unsubscribe

    queue = subscribe(job_id)

    async def event_stream():
        last_evt_time = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=20.0)
                    last_evt_time = time.monotonic()
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    if data.get("type") in (
                        "exec_done", "exec_cancelled", "plan_done",
                        "plan_cancelled", "plan_error", "compose_done", "compose_error",
                        "force_stopped",
                    ):
                        break
                except asyncio.TimeoutError:
                    # 服务端无事件超过 20s 时，向前端发送可感知的心跳，避免日志面板长时间静默
                    elapsed = int(time.monotonic() - last_evt_time)
                    hb = {"type": "heartbeat", "elapsed_sec": elapsed, "msg": "服务端仍在运行…"}
                    yield f"data: {json.dumps(hb, ensure_ascii=False)}\n\n"
        finally:
            unsubscribe(job_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
