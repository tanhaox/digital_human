"""Composition service package — assemble completed DirectorSlot clips into final 9:16 video.

包布局:
    common        — 通用纯工具 (时间戳 / 路径 / 清理 / ffmpeg 薄包装 / 质量层级)
    ffmpeg_steps  — concat / 音频检测 / 响度归一化 (loudnorm)
    audio         — 主音轨混音 + 分段 TTS timeline 构建
    slots         — Step 1: 校验 slot 输出 + 缺失自动补齐 (REPAIR)
    output        — Step 5: 校验输出 + manifest + 写库; Step 6: 清理中间件
    pipeline      — 主编排: compose_director_job

对外唯一导出: compose_director_job (director_routes/compose.py 后台线程、
scripts/verify_retention.py 引用)。
"""
from __future__ import annotations

from app.services.composition_service.pipeline import compose_director_job

__all__ = ["compose_director_job"]
