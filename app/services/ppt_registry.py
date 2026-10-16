# -*- coding: utf-8 -*-
"""PPT 产线 job 底座 (B11, 2026-10-16) — 内存注册表 + 事件 + 工作目录, 单一实例.

原定义在 app/services/ppt_pipeline.py 顶部, book_service/slide_render (bs1 书线
产线) 反向引用构成环#3。抽到本中立模块后: ppt_pipeline 顶层 re-export (routers/ppt
等既有消费者零改动), slide_render 直引本模块 — 引用方拿到的 _JOBS 永远是同一 dict
(模块缓存单实例), job 不会因两份注册表而丢失。
"""
from __future__ import annotations

import time
from pathlib import Path

from app.config import get_config

_JOBS: dict[str, dict] = {}


def _evt(job_id: str, msg: str, level: str = "info", **extra) -> None:
    from app.services.director_events import publish
    e = {"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg, **extra}
    j = _JOBS.setdefault(job_id, {})
    j.setdefault("events", []).append(e)
    if len(j["events"]) > 500:
        j["events"] = j["events"][-500:]
    publish(job_id, e)


def _job_workdir(job_id: str) -> Path:
    root = Path(get_config().defaults.ppt_work_root)
    return root / job_id
