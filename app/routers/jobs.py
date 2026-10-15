"""SSE job event streaming."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ._sse import sse_stream
from app.services.job_events import TERMINAL_EVENT_TYPES, _job_channels

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """Server-Sent Events endpoint for job progress."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _job_channels.setdefault(job_id, []).append(queue)

    def _keep_alive() -> str:
        return ": keep-alive\n\n"

    def _on_close() -> None:
        queues = _job_channels.get(job_id, [])
        if queue in queues:
            queues.remove(queue)

    return StreamingResponse(
        sse_stream(
            request, queue,
            heartbeat=_keep_alive,
            timeout=30.0,
            terminal_types=TERMINAL_EVENT_TYPES,
            on_close=_on_close,
        ),
        media_type="text/event-stream",
    )
