"""Pexels resolve — 候选视频处理与下载编排。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import MaterialAsset
from app.services.pexels_service._db import (
    increment_quota,
    register_video_asset,
    upsert_asset,
)
from app.services.pexels_service._http import download
from app.services.pexels_service._local import asset_to_item
from app.services.pexels_service.types import (
    PexelsResolveError,
    ResolveItem,
)
from app.services.pexels_utils import (
    API_DURATION, API_HEIGHT, API_ID, API_LINK, API_VIDEO_FILES, API_WIDTH,
    RESOLUTION_WIDTHS, int_duration, orientation_ok, pick_video_file,
)

logger = logging.getLogger(__name__)

__all__ = ["process_candidates"]


def _widths_sorted(prefer_resolution: str) -> list[tuple[str, int]]:
    """按与偏好分辨率宽度差的绝对值排序的 (label, width) 列表."""
    prefer_width = RESOLUTION_WIDTHS[prefer_resolution]
    return sorted(RESOLUTION_WIDTHS.items(), key=lambda x: abs(x[1] - prefer_width))


def _candidate_downloadable(
    video: dict[str, Any],
    min_duration_sec: int,
    orientation: str,
    exclude_pexels_ids: set[int],
) -> bool:
    """候选过滤 (时长/方向/pexels_id/黑名单). 返回是否可处理."""
    if int_duration(video.get(API_DURATION)) < min_duration_sec:
        return False
    width = video.get(API_WIDTH, 0)
    height = video.get(API_HEIGHT, 0)
    if not orientation_ok(width, height, orientation):
        return False
    pexels_id = video.get(API_ID)
    if not pexels_id:
        return False
    if pexels_id in exclude_pexels_ids:
        return False
    return True


def _quality_gate(db: Session, local_path: str | Path) -> bool:
    """下载即质检 (2026-08-16 烂素材治理③闭环).

    模式 (pexels_quality_gate_mode, 生产考量 2026-08-16 用户指出同步打分拖慢成片):
      sync  = 同步打分, ≤门槛换下一候选 (最严, 每片 +4~6 分钟)
      async = 默认: 下载即返回, 后台秒级补打分落库 — 同 job 后续 slot 与
              未来全片受保护, 仅当前 slot 可能带病上岗
      (pexels_download_quality_gate=False 时完全关闭)
    fail-open: 视觉模型不可用/打分失败 → 放行。
    """
    from app.config import get_config

    try:
        cfg = get_config().defaults
        if not getattr(cfg, "pexels_download_quality_gate", True):
            return True
        mode = getattr(cfg, "pexels_quality_gate_mode", "async")
    except Exception:
        mode = "async"
    if mode != "sync":
        from app.services.asset_quality import enqueue_quality_check

        enqueue_quality_check(str(local_path))
        return True
    from app.services.asset_quality import score_video_file

    result = score_video_file(local_path)
    if result is None:
        return True
    # 分数写回素材库 (video_assets 按文件路径定位)
    from app.models import VideoAsset

    asset = db.query(VideoAsset).filter(VideoAsset.file_path == str(local_path)).first()
    if asset is not None:
        asset.quality_score = float(result["score"])
        asset.quality_reason = ("generic;" if result["generic"] else "") + result["verdict"]
        if result["score"] <= 3:
            asset.preference = "dislike"
        db.commit()
    threshold = getattr(get_config().defaults, "local_asset_min_quality", 4)
    ok = result["score"] >= min(threshold, 4)  # 下载门槛: ≤3 一票否决档
    if not ok:
        logger.info("[pexels] 质检不合格(score=%d): %s — 换下一候选",
                    result["score"], result["verdict"])
    return ok


def _handle_candidate(
    svc: Any, db: Session, video: dict[str, Any],
    prefer_resolution: str, min_duration_sec: int, orientation: str,
    remaining_quota: int, tags_str: str, materials_dir: str,
    exclude_pexels_ids: set[int], raw_query: str,
) -> tuple[ResolveItem | None, bool]:
    """处理单条候选: 过滤 → 复用/下载 → 入库.

    Returns: (item, consumed) — item None 表示跳过; consumed=True 表示消耗 1 下载配额."""
    if not _candidate_downloadable(video, min_duration_sec, orientation, exclude_pexels_ids):
        return None, False

    pexels_id = video.get(API_ID)
    existing = db.query(MaterialAsset).filter(MaterialAsset.pexels_id == pexels_id).first()
    if existing and existing.local_path and Path(existing.local_path).exists():
        # 缓存复用也过质检 (未打分的才打; 已出局的素材缓存也跳过)
        from app.models import VideoAsset

        va = db.query(VideoAsset).filter(VideoAsset.file_path == existing.local_path).first()
        if va is not None and va.preference == "dislike":
            return None, False
        if va is not None and va.quality_score is None:
            if not _quality_gate(db, existing.local_path):
                return None, False
        return asset_to_item(existing), False
    if remaining_quota <= 0:
        return None, False

    chosen = pick_video_file(video.get(API_VIDEO_FILES, []), _widths_sorted(prefer_resolution))
    if chosen is None:
        return None, False
    source_url = chosen.get(API_LINK)
    if not source_url:
        return None, False

    try:
        local_path = download(svc, source_url, pexels_id, materials_dir)
    except PexelsResolveError as exc:
        logger.warning("Download failed pexels_id=%s: %s", pexels_id, exc)
        return None, False
    if local_path is None:
        return None, False

    file_size = Path(local_path).stat().st_size
    increment_quota(db, pexels_id, file_size)
    asset = upsert_asset(db, existing, video, chosen, source_url, local_path, tags_str)
    register_video_asset(db, video, chosen, local_path, tags_str, raw_query=raw_query)
    # 下载即质检 (2026-08-16): 不合格 → 素材已标记出局, 本候选作废, 循环换下一个
    if not _quality_gate(db, local_path):
        return None, True
    return asset_to_item(asset), True


def process_candidates(
    svc: Any, db: Session, videos: list[dict[str, Any]], needed: int,
    prefer_resolution: str, min_duration_sec: int, orientation: str,
    remaining_quota: int, tags_str: str, materials_dir: str,
    exclude_pexels_ids: set[int] | None = None,
    raw_query: str = "",
) -> list[ResolveItem]:
    """遍历候选视频, 下载并入库直到凑够 needed."""
    results: list[ResolveItem] = []
    exclude_pexels_ids = exclude_pexels_ids or set()
    if remaining_quota <= 0 or needed <= 0:
        return results

    for video in videos:
        if len(results) >= needed:
            break
        item, consumed = _handle_candidate(
            svc, db, video,
            prefer_resolution, min_duration_sec, orientation,
            remaining_quota, tags_str, materials_dir,
            exclude_pexels_ids, raw_query,
        )
        if item is None:
            continue
        results.append(item)
        if consumed:
            remaining_quota -= 1

    return results
