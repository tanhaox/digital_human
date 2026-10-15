"""Video library service — 素材库/成品库的纯逻辑(文件处理 + 扫描 + 搜索).

从 `app/routers/library.py` 下沉的辅助逻辑, 供 router 端点调用.
DB session 由调用方传入 (SQLAlchemy 延迟导入, 避免顶层依赖).
"""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .file_utils import recycle_file  # noqa: F401 — re-export, routers/library.py 经本模块调用

logger = logging.getLogger(__name__)


def parse_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parse 'bytes=start-end' into (start, end) inclusive."""
    if not range_header.startswith("bytes="):
        return None
    spec = range_header[6:].strip()
    if "-" not in spec:
        return None
    start_str, end_str = spec.split("-", 1)
    try:
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
    except ValueError:
        return None
    if start < 0 or end >= file_size or start > end:
        return None
    return start, end


def _range_response(
    file_path: Path, media_type: str, file_size: int, start: int, end: int
) -> StreamingResponse:
    """Build a 206 StreamingResponse for a byte range (8KB chunks)."""
    length = end - start + 1

    def iter_file() -> Iterator[bytes]:
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(8192, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=media_type,
        status_code=206,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Disposition": f'inline; filename="{file_path.name}"',
        },
    )


def video_file_response(request: Request, file_path: Path, media_type: str) -> FileResponse | StreamingResponse:
    """Return video with optional HTTP Range support for browser seeking."""
    if not file_path.exists():
        raise HTTPException(404, "file missing on disk")
    file_size = file_path.stat().st_size
    inline = FileResponse(
        str(file_path),
        media_type=media_type,
        filename=file_path.name,
        content_disposition_type="inline",
    )
    range_header = request.headers.get("range")
    if not range_header:
        return inline
    rng = parse_range(range_header, file_size)
    if rng is None:
        return inline
    return _range_response(file_path, media_type, file_size, rng[0], rng[1])


def _build_asset_query(db: Any, **filters) -> Any:
    """按筛选条件构建 VideoAsset 查询 (关键词 OR 匹配 + 维度等值 + 逗号分隔标签 OR)."""
    from sqlalchemy import or_, String
    from app.models import VideoAsset

    query = db.query(VideoAsset)
    q = filters.get("q")
    if q:
        like = f"%{q}%"
        cols = (
            VideoAsset.asset_no, VideoAsset.description_en, VideoAsset.description_zh,
            VideoAsset.raw_query, VideoAsset.photographer,
            VideoAsset.tags.cast(String), VideoAsset.scenes.cast(String), VideoAsset.shot_types.cast(String),
        )
        query = query.filter(or_(*[c.ilike(like) for c in cols]))
    for name in ("orientation", "source_type", "location", "people", "preference"):
        value = filters.get(name)
        if value:
            query = query.filter(getattr(VideoAsset, name) == value)
    for key in ("scenes", "shot_types", "tags"):
        value = filters.get(key)
        if value:
            for t in (x.strip() for x in value.split(",") if x.strip()):
                query = query.filter(getattr(VideoAsset, key).cast(String).ilike(f"%{t}%"))
    return query


def search_assets(
    db: Any,
    *,
    q: str | None,
    orientation: str | None,
    source_type: str | None,
    location: str | None,
    people: str | None,
    preference: str | None,
    scenes: str | None,
    shot_types: str | None,
    tags: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """构建素材搜索查询. 返回 {total, items(ORM)}. 喜欢的素材排序置顶."""
    from sqlalchemy import case
    from app.models import VideoAsset

    query = _build_asset_query(
        db, q=q, orientation=orientation, source_type=source_type, location=location,
        people=people, preference=preference, scenes=scenes, shot_types=shot_types, tags=tags,
    )
    total = query.count()
    items = (
        query.order_by(
            case(
                (VideoAsset.preference == "like", 0),
                (VideoAsset.preference == "neutral", 1),
                (VideoAsset.preference == "dislike", 2),
                else_=1,
            ),
            VideoAsset.used_count.desc(),
            VideoAsset.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"total": total, "items": items}


def output_to_dict(o: Any) -> dict[str, Any]:
    """VideoOutput → 纯 dict (created_at 非空才 isoformat)."""
    return {
        "id": o.id,
        "job_id": o.job_id,
        "title": o.title,
        "file_path": o.file_path,
        "orientation": o.orientation,
        "duration_sec": o.duration_sec,
        "video_format": o.video_format,
        "description": o.description,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
