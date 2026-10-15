"""Pexels resolve — 本地缓存查询 (MaterialAsset)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import MaterialAsset
from app.services.pexels_service.types import ResolveItem

__all__ = ["query_local", "asset_to_item"]


def query_local(
    svc: Any, db: Session, tags: list[str], max_results: int, min_duration_sec: int,
    orientation: str = "any", exclude_pexels_ids: set[int] | None = None,
) -> list[ResolveItem]:
    """按首个标签 + 时长/方向过滤查询本地缓存; 仅保留磁盘文件存在的行."""
    if not tags:
        return []
    exclude_pexels_ids = exclude_pexels_ids or set()
    pattern = f"%{tags[0]}%"
    q = (
        db.query(MaterialAsset)
        .filter(MaterialAsset.tags.like(pattern))
        .filter(MaterialAsset.duration_sec >= min_duration_sec)
        .filter(MaterialAsset.local_path.isnot(None))
    )
    if exclude_pexels_ids:
        q = q.filter(~MaterialAsset.pexels_id.in_(exclude_pexels_ids))
    if orientation == "portrait":
        q = q.filter(MaterialAsset.height > MaterialAsset.width)
    elif orientation == "landscape":
        q = q.filter(MaterialAsset.width >= MaterialAsset.height)
    elif orientation == "square":
        # 近方形: 宽高比 0.75 ~ 1.33 (3:4 ~ 4:3), 与 pexels_utils.orientation_ok 一致 (2026-08-01)
        q = q.filter(
            MaterialAsset.width / MaterialAsset.height >= 0.75,
            MaterialAsset.width / MaterialAsset.height <= 1.33,
        )
    rows = q.order_by(MaterialAsset.created_at.desc()).limit(max_results).all()
    items: list[ResolveItem] = []
    for r in rows:
        if r.local_path and Path(r.local_path).exists():
            items.append(asset_to_item(r))
        else:
            r.local_path = None
    return items


def asset_to_item(asset: MaterialAsset) -> ResolveItem:
    """MaterialAsset 行 → ResolveItem."""
    return ResolveItem(
        id=asset.id, pexels_id=asset.pexels_id, local_path=asset.local_path,
        source_url=asset.source_url,
        degraded=False, duration_sec=asset.duration_sec,
        width=asset.width, height=asset.height, fps=asset.fps,
        photographer=asset.photographer, photographer_url=asset.photographer_url,
        pexels_url=asset.pexels_url, tags=[t for t in asset.tags.split(",") if t],
    )
