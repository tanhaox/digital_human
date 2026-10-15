# -*- coding: utf-8 -*-
"""基础设施层 — 与业务逻辑解耦的外部系统适配 (ffmpeg / ffprobe / DB session).

分层约束: 本层不得导入 app.services / app.routers / app.models。
"""
from __future__ import annotations

from .ffmpeg import (
    extract_audio_slice,
    extract_audio_slice_wav,
    render_scale_pad,
    replace_video_audio,
    run_ffmpeg,
)

__all__ = [
    "run_ffmpeg",
    "extract_audio_slice",
    "extract_audio_slice_wav",
    "replace_video_audio",
    "render_scale_pad",
]
