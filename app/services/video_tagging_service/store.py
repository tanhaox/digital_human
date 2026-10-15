"""视频打标 — 标签写库 + 打标后重建关键词词表包 (ID-034)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.database import db_session
from app.models import VideoAsset

logger = logging.getLogger(__name__)

__all__ = ["_write_asset_tags", "_rebuild_vocabulary_pack"]


def _write_asset_tags(asset_id: str, tags: dict, model_name: str) -> bool:
    """将标签写入数据库 (独立 session)."""
    with db_session() as session:
        try:
            asset = session.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
            if asset is None:
                logger.error("Asset not found: %s", asset_id)
                return False

            asset.scenes = tags.get("scenes", [])
            asset.shot_types = tags.get("shot_types", [])
            asset.source_type = tags.get("source_type", "footage")
            asset.location = tags.get("location", "foreign")
            asset.people = tags.get("people", "none")
            asset.description_zh = tags.get("description_zh", None)

            asset.ai_tagged_at = datetime.now(timezone.utc)
            asset.ai_tag_model = model_name
            asset.ai_confidence = tags.get("_ai_confidence")
            asset.ai_tags_extra = tags.get("_ai_extra")

            session.commit()
            return True
        except Exception:
            logger.exception("Failed to write tags for asset %s", asset_id)
            session.rollback()
            return False


def _rebuild_vocabulary_pack() -> None:
    """打标完成后重建关键词词表包 (ID-034).

    lazy import 避免与 director_prompt 的模块级 import 冲突;
    失败仅记日志, 不影响打标主流程 (包缺失时导演侧会回退全量清单)。
    """
    try:
        from app.services.director_prompt import rebuild_vocabulary_pack

        with db_session() as session:
            rebuild_vocabulary_pack(session)
    except Exception:
        logger.exception("rebuild_vocabulary_pack after tagging failed (non-fatal)")
