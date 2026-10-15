"""Pexels resolve — 降级补齐 (degraded 元数据填充)."""
from __future__ import annotations

from typing import Any

from app.services.pexels_service.types import ResolveItem
from app.services.pexels_utils import (
    API_DURATION, API_FPS, API_HEIGHT, API_ID, API_LINK,
    API_USER, API_USER_NAME, API_USER_URL, API_VIDEO_FILES, API_WIDTH,
    RESOLUTION_WIDTHS, int_duration, int_fps, pick_video_file,
)

__all__ = ["merge_with_degraded", "fill_with_degraded"]


def merge_with_degraded(
    local_items: list[ResolveItem], needed: int, reason: str,
) -> list[ResolveItem]:
    """搜索失败: 全部本地项标记 degraded + reason."""
    for item in local_items:
        item.degraded = True
        item.reason = reason
    return local_items


def fill_with_degraded(
    current: list[ResolveItem], videos: list[dict[str, Any]],
    max_results: int, prefer_resolution: str, min_duration_sec: int,
    exclude_pexels_ids: set[int] | None = None,
) -> list[ResolveItem]:
    """下载不足: 用未下载的候选元数据补齐到 max_results, 标记 degraded."""
    existing_pexels = {item.pexels_id for item in current if item.pexels_id is not None}
    seen_urls = {item.source_url for item in current if item.source_url}
    exclude_pexels_ids = exclude_pexels_ids or set()
    for video in videos:
        if len(current) >= max_results:
            break
        pexels_id = video.get(API_ID)
        if pexels_id and pexels_id in existing_pexels:
            continue
        if pexels_id in exclude_pexels_ids:
            continue
        duration = int_duration(video.get(API_DURATION))
        if duration < min_duration_sec:
            continue
        chosen = pick_video_file(video.get(API_VIDEO_FILES, []), [(prefer_resolution, RESOLUTION_WIDTHS[prefer_resolution])])
        source_url = chosen.get(API_LINK) if chosen else None
        if source_url in seen_urls:
            continue
        photographer = video.get(API_USER, {}).get(API_USER_NAME) or "Unknown Photographer"
        photographer_url = video.get(API_USER, {}).get(API_USER_URL) or "https://www.pexels.com"
        pexels_url = video.get("url") or f"https://www.pexels.com/video/{pexels_id}"
        width = chosen.get(API_WIDTH, video.get(API_WIDTH, 0)) if chosen else video.get(API_WIDTH, 0)
        height = chosen.get(API_HEIGHT, video.get(API_HEIGHT, 0)) if chosen else video.get(API_HEIGHT, 0)
        current.append(ResolveItem(
            duration_sec=duration, width=width, height=height,
            photographer=photographer, photographer_url=photographer_url,
            pexels_url=pexels_url, source_url=source_url,
            degraded=True, reason="quota_exceeded_or_unreachable",
            fps=int_fps(chosen.get(API_FPS) if chosen else video.get(API_FPS)),
        ))
    return current
