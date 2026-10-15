"""SSE 公共工厂 (T5, 2026-09-17) — 五份同构生成器的单一骨架.

差异分析定案 (REFACTOR_PLAN T5 第一步):
- 心跳三族五样是前端可感知行为 → heartbeat 由调用方传 Callable 返回完整帧, 工厂不做格式判断;
- 两条总线 (job_events 64 / director_events 128) 不合并 (T16b) → 订阅在调用方完成,
  工厂只收已订阅的 queue + 零参清理回调, 不直连任何总线;
- request=None 跳过断开轮询 (tagging 连 request 参数都没有);
- 不捕 CancelledError (4/5 端点现状; tagging 的捕获去留另议);
- prefetch 进循环前先发若干帧 (ppt 缓冲回放 / tagging 状态快照);
- data 帧编码 "data: {json.dumps(..., ensure_ascii=False)}\\n\\n" 五份原文一致, 收归工厂。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

from fastapi import Request


async def sse_stream(
    request: Request | None,
    queue: asyncio.Queue,
    *,
    heartbeat: Callable[[], str],
    timeout: float,
    terminal_types: tuple[str, ...],
    on_close: Callable[[], None],
    prefetch: Callable[[], Iterable[Any]] | None = None,
) -> AsyncIterator[str]:
    """SSE 帧生成器: 断开轮询(可选) → get+超时心跳 → 终止断流, finally 清理.

    调用方须先订阅再调用本函数 — 订阅必须发生在生成器首拉之前, 否则端点返回到
    首拉之间到达的事件会丢 (五份原端点均为先订阅后建生成器的时序)。
    """
    try:
        if prefetch is not None:
            for item in prefetch():
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        while True:
            if request is not None and await request.is_disconnected():
                break
            try:
                data = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                yield heartbeat()
                continue
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            if data.get("type") in terminal_types:
                break
    finally:
        on_close()
