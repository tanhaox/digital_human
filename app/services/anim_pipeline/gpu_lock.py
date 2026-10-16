# -*- coding: utf-8 -*-
"""GPU 相位互斥锁 (B07, 2026-10-16) — anim_pipeline 与 anim_service 共引的单实例.

原定义在 app/services/anim_service.py (模块级 threading.Lock), 管线内 6 处反向
延迟 import 它造成环#1 侵蚀 (character_sheets×2 + style_board×4)。抽到本模块后
两侧同引一把锁: Python 模块缓存保证同进程内无论从哪条路径 import, 拿到的都是
同一个 Lock 对象 — anim_service 侧以 ``GPU_JOB_LOCK as _GPU_JOB_LOCK`` alias
保持历史名, 管线内直接用新名。

互斥语义不变: 同一时刻至多一个 GPU 批 (k2/h3/redo/brand 相位);
anim_service 入口 acquire(blocking=False) 试探, 拿不到 → AnimBusyError(409);
管线内各相位 ``with GPU_JOB_LOCK:`` 阻塞排队。锁对象勿重建勿复制。
"""
from __future__ import annotations

import threading

GPU_JOB_LOCK = threading.Lock()  # 占位锁: GPU 批进行中 → 新 GPU 请求 409
