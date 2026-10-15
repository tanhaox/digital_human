"""Pexels utility functions — pure helpers with no instance state."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

API_ID = "id"
API_DURATION = "duration"
API_WIDTH = "width"
API_HEIGHT = "height"
API_FPS = "fps"
API_LINK = "link"
API_VIDEO_FILES = "video_files"
API_USER = "user"
API_USER_NAME = "name"
API_USER_URL = "url"

RESOLUTION_WIDTHS: dict[str, int] = {
    "UHD": 3840, "QHD": 2560, "FHD": 1920, "HD": 1280, "SD": 640,
}


def int_duration(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (ValueError, TypeError):
        return 0


def int_fps(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def resolution_label(width: int) -> str:
    if width >= 3840:
        return "UHD"
    if width >= 2560:
        return "QHD"
    if width >= 1920:
        return "FHD"
    if width >= 1280:
        return "HD"
    return "SD"


def orientation_ok(width: int, height: int, orientation: str) -> bool:
    if orientation == "any" or not width or not height:
        return True
    if orientation == "landscape":
        return width >= height
    if orientation == "portrait":
        return height > width
    if orientation == "square":
        # 近方形: 宽高比落在 0.75 ~ 1.33 之间 (3:4 ~ 4:3), 排除明显竖条/横条
        return 0.75 <= width / height <= 1.33
    return True


def sanitize_query(query: str) -> str:
    return re.sub(r"[^\w\s\-]", "", query).strip()[:128]


def pick_video_file(
    video_files: list[dict[str, Any]],
    widths_sorted: list[tuple[str, int]],
) -> dict[str, Any] | None:
    """Pick best mp4 by resolution priority."""
    mp4_files = [f for f in video_files if isinstance(f, dict) and f.get("file_type") == "video/mp4"]
    if not mp4_files:
        return None
    for _label, target_width in widths_sorted:
        candidates = [
            f for f in mp4_files
            if f.get(API_WIDTH) and abs(f.get(API_WIDTH, 0) - target_width) <= target_width * 0.15
        ]
        if candidates:
            return min(candidates, key=lambda f: abs(f.get(API_WIDTH, 0) - target_width))
    return max(mp4_files, key=lambda f: f.get(API_WIDTH, 0) or 0)


def validate_video(path: Path) -> bool:
    """Check video file has at least one video stream via ffprobe."""
    if not shutil.which("ffprobe"):
        logger.warning("ffprobe not found, skipping validation")
        return True
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if r.returncode != 0:
            return False
        data = json.loads(r.stdout)
        return bool([s for s in data.get("streams", []) if s.get("codec_type") == "video"])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return False
