# -*- coding: utf-8 -*-
"""视频画幅规格定义."""
from __future__ import annotations

from typing import Any

__all__ = [
    "VIDEO_FORMAT_SPECS",
    "get_video_format_spec",
]

VIDEO_FORMAT_SPECS: dict[str, dict[str, Any]] = {
    "portrait": {
        "width": 1080, "height": 1920,
        "comfyui_w": 576, "comfyui_h": 1024,
        "pexels_orientation": "portrait",
        "label": "竖屏 9:16",
    },
    "landscape": {
        "width": 1920, "height": 1080,
        "comfyui_w": 1024, "comfyui_h": 576,
        "pexels_orientation": "landscape",
        "label": "横屏 16:9",
    },
    "square": {
        "width": 1080, "height": 1080,
        "comfyui_w": 768, "comfyui_h": 768,
        "pexels_orientation": "square",
        "label": "方形 1:1",
    },
}


def get_video_format_spec(video_format: str | None) -> dict[str, Any]:
    """Return spec dict for the given format, defaulting to portrait."""
    return VIDEO_FORMAT_SPECS.get(video_format or "portrait", VIDEO_FORMAT_SPECS["portrait"])
