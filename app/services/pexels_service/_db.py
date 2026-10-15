"""Pexels resolve — DB 写入 (MaterialAsset / VideoAsset / DownloadLog)."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DownloadLog, MaterialAsset, VideoAsset
from app.services.asset_tagging import generate_asset_no, infer_tags
from app.services.pexels_utils import (
    API_DURATION, API_FPS, API_HEIGHT, API_ID, API_LINK, API_USER,
    API_USER_NAME, API_USER_URL, API_WIDTH, int_duration, int_fps,
    resolution_label,
)

__all__ = [
    "upsert_asset",
    "increment_quota",
    "register_video_asset",
    "build_video_asset",
    "get_quota_used_today",
    "get_dislike_pexels_ids",
]


def upsert_asset(
    db: Session, existing: MaterialAsset | None,
    video: dict[str, Any], chosen: dict[str, Any],
    source_url: str, local_path: str, tags_str: str,
) -> MaterialAsset:
    """新建或更新 MaterialAsset 行, 返回持久化后的对象."""
    photographer = video.get(API_USER, {}).get(API_USER_NAME) or "Unknown Photographer"
    photographer_url = video.get(API_USER, {}).get(API_USER_URL) or "https://www.pexels.com"
    pexels_url = video.get("url") or f"https://www.pexels.com/video/{video.get(API_ID)}"
    width = chosen.get(API_WIDTH, video.get(API_WIDTH, 0))
    height = chosen.get(API_HEIGHT, video.get(API_HEIGHT, 0))
    res = resolution_label(width)

    if existing is None:
        asset = MaterialAsset(
            pexels_id=video.get(API_ID), source_url=source_url, pexels_url=pexels_url,
            photographer=photographer, photographer_url=photographer_url,
            local_path=local_path, duration_sec=int_duration(video.get(API_DURATION)),
            width=width, height=height,
            fps=int_fps(chosen.get(API_FPS) or video.get(API_FPS)),
            resolution=res, tags=tags_str,
        )
        db.add(asset)
        db.flush()
    else:
        existing.local_path = local_path
        existing.source_url = source_url
        existing.pexels_url = pexels_url
        existing.photographer = photographer
        existing.photographer_url = photographer_url
        existing.duration_sec = int_duration(video.get(API_DURATION))
        existing.width = width
        existing.height = height
        existing.fps = int_fps(chosen.get(API_FPS) or video.get(API_FPS))
        existing.resolution = res
        existing.tags = tags_str
        asset = existing
    return asset


def increment_quota(db: Session, pexels_id: int, size_bytes: int) -> None:
    """记录一次下载到当日 DownloadLog."""
    db.add(DownloadLog(date=date.today(), pexels_id=pexels_id, bytes=size_bytes))


def build_video_asset(
    db: Session, video: dict[str, Any], chosen: dict[str, Any],
    local_path: str, tags_str: str, raw_query: str = "",
) -> VideoAsset | None:
    """构建 VideoAsset 对象 (未 add). 无 pexels_id 或已存在返回 None."""
    pexels_id = video.get(API_ID)
    if not pexels_id:
        return None
    # 去重: 已存在则跳过
    if db.query(VideoAsset).filter(VideoAsset.pexels_id == pexels_id).first():
        return None
    width = chosen.get(API_WIDTH, video.get(API_WIDTH, 0))
    height = chosen.get(API_HEIGHT, video.get(API_HEIGHT, 0))
    photographer = video.get(API_USER, {}).get(API_USER_NAME) or ""
    pexels_url = video.get("url") or f"https://www.pexels.com/video/{pexels_id}"
    tags = infer_tags(raw_query or tags_str, width, height)
    asset_no = generate_asset_no(db)

    return VideoAsset(
        asset_no=asset_no,
        source="pexels",
        pexels_id=pexels_id,
        file_path=local_path,
        orientation=tags["orientation"],
        width=width,
        height=height,
        duration_sec=int_duration(video.get(API_DURATION)),
        description_en=None,
        description_zh=None,
        photographer=photographer,
        source_url=pexels_url,
        raw_query=(raw_query or tags_str)[:512],
        source_type=tags["source_type"],
        location=tags["location"],
        scenes=tags["scenes"],
        shot_types=tags["shot_types"],
        people=tags["people"],
        preference="neutral",
        tags=[t for t in tags_str.split(",") if t] if tags_str else [],
    )


def register_video_asset(
    db: Session, video: dict[str, Any], chosen: dict[str, Any],
    local_path: str, tags_str: str,
    raw_query: str = "",
) -> None:
    """Register downloaded video into VideoAsset library (dedup by pexels_id)."""
    va = build_video_asset(db, video, chosen, local_path, tags_str, raw_query=raw_query)
    if va is not None:
        db.add(va)


def get_quota_used_today(db: Session) -> int:
    """今日已用下载配额 (DownloadLog 计数)."""
    return db.query(func.count(DownloadLog.id)).filter(DownloadLog.date == date.today()).scalar() or 0


def get_dislike_pexels_ids(db: Session) -> set[int]:
    """加载用户标记为 dislike 的 pexels_id 黑名单."""
    rows = (
        db.query(VideoAsset.pexels_id)
        .filter(VideoAsset.preference == "dislike", VideoAsset.pexels_id.isnot(None))
        .all()
    )
    return {r[0] for r in rows if r[0]}
