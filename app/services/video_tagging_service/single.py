"""视频打标 — 同步单素材打标 (tag_single_asset)."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import get_config
from app.database import db_session
from app.models import VideoAsset
from app.services.asset_tagging import infer_tags
from app.services.local_llm_client import LocalLLMClient
from app.services.video_tagging_service.llama import _ensure_llama_server
from app.services.video_tagging_service.pipeline import _run_pipeline_for_asset
from app.services.video_tagging_service.store import (
    _rebuild_vocabulary_pack,
    _write_asset_tags,
)

logger = logging.getLogger(__name__)

__all__ = ["tag_single_asset"]


def _load_single_asset_context(asset_id: str) -> dict[str, Any] | None:
    """加载单素材上下文; 素材不存在返回 None."""
    with db_session() as session:
        asset = session.query(VideoAsset).filter(VideoAsset.id == asset_id).first()
        if asset is None:
            return None
        return {
            "video_path": asset.file_path,
            "duration": asset.duration_sec or 10.0,
            "asset_no": asset.asset_no,
            "asset_id": asset.id,
            "fallback": infer_tags(
                asset.raw_query or "",
                asset.width or 1920,
                asset.height or 1080,
            ),
        }


def tag_single_asset(asset_id: str) -> dict[str, Any]:
    """同步打标单个素材 (4 通道流水线), 返回完整标签字典.

    Returns:
        tags dict, 含 _success, _fallback, _error 等元信息.
    """
    if not _ensure_llama_server():
        return {"_success": False, "_error": "llama-server 无法启动"}

    cfg = get_config()
    client = LocalLLMClient(cfg.local_llm)
    model_name = cfg.local_llm.model

    ctx = _load_single_asset_context(asset_id)
    if ctx is None:
        return {"_success": False, "_error": f"Asset not found: {asset_id}"}

    with tempfile.TemporaryDirectory(prefix="tagging_single_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        tags = _run_pipeline_for_asset(
            video_path=ctx["video_path"],
            duration=ctx["duration"],
            tmpdir=tmpdir,
            client=client,
            fallback=ctx["fallback"],
            asset_no=ctx["asset_no"],
            asset_id=ctx["asset_id"],
        )

    ok = _write_asset_tags(asset_id, tags, model_name)
    tags["_success"] = ok
    tags["_asset_no"] = ctx["asset_no"]
    if ok and not tags.get("_fallback"):
        # ID-034: 打标成功 → 重建词表包
        _rebuild_vocabulary_pack()
    return tags
