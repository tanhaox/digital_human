"""SSE job event streaming."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.job_events import _job_channels

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """Server-Sent Events endpoint for job progress."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _job_channels.setdefault(job_id, []).append(queue)

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    if data.get("type") in ("rewrite_done", "rewrite_error", "tts_done", "tts_error", "tts_cancelled", "carnival_done", "carnival_error", "correct_done", "correct_error", "material_done", "material_error", "anim_done", "anim_error", "anim_cancelled", "bible_done", "bible_error", "samples_done", "samples_error", "board_done", "board_error", "retune_done", "retune_error", "chars_done", "chars_error"):
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            queues = _job_channels.get(job_id, [])
            if queue in queues:
                queues.remove(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
