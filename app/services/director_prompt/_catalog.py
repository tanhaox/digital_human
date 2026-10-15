"""Director prompt — 素材库目录 (真实 DB / mock 兜底).

build_real_material_catalog 供导演 LLM 规划参考; DB 异常/空库时
内部 fallback 到 mock_material_catalog。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import VideoAsset

logger = logging.getLogger(__name__)

__all__ = ["build_real_material_catalog", "mock_material_catalog"]

_MAX_ITEMS_PER_SCENE = 8


def _group_by_scene(
    assets: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    """按主场景分组, 每个条目带 AI 多维标签 + 内部排序字段."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        primary_scene = (asset.scenes or ["未分类"])[0]
        grouped.setdefault(primary_scene, []).append({
            "file": Path(asset.file_path).name if asset.file_path else "",
            "tags": asset.tags or [],
            "description_zh": (asset.description_zh or "")[:40],
            "tone": (asset.ai_tags_extra or {}).get("tone"),
            "motion_level": (asset.ai_tags_extra or {}).get("motion_level"),
            "content_density": (asset.ai_tags_extra or {}).get("content_density"),
            "time_of_day": (asset.ai_tags_extra or {}).get("time_of_day"),
            "orientation": asset.orientation,
            "_has_ai_tags": (asset.ai_tags_extra or {}).get("tone") is not None,
            "_used_count": asset.used_count or 0,
        })
    return grouped


def _trim_scene_items(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """top-N 裁剪: AI 标签优先, 其次 used_count 低; 返回 (素材列表, 默认推荐)."""
    items_sorted = sorted(items, key=lambda i: i["_used_count"])
    ai_tagged = [i for i in items_sorted if i["_has_ai_tags"]]
    others = [i for i in items_sorted if not i["_has_ai_tags"]]
    chosen = (ai_tagged + others)[:_MAX_ITEMS_PER_SCENE]
    for c in chosen:
        c.pop("_has_ai_tags", None)
        c.pop("_used_count", None)
    # 默认推荐: 有 AI 标签的优先，used_count 低的优先
    default_item = (ai_tagged or chosen)[0] if (ai_tagged or chosen) else None
    return chosen, default_item["file"] if default_item else None


def _build_scene_catalog(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """把按场景分组的素材转成 catalog 正文 (top-N 裁剪 + 默认推荐)."""
    catalog: dict[str, Any] = {}
    for scene, items in sorted(grouped.items()):
        chosen, default_item = _trim_scene_items(items)
        catalog[scene] = {
            "素材列表": chosen,
            "默认推荐": default_item,
            "素材数量": len(chosen),
            "素材总数": len(items),  # 提示 LLM 该场景还有更多素材
        }
    return catalog


def build_real_material_catalog(db: Session) -> dict[str, Any]:
    """从 VideoAsset 表构建真实素材库目录，供导演 LLM 规划参考。

    按 scenes[0]（主场景）分组，同组内列出素材文件、tags、AI 多维标签。
    额外添加"数据图表"/"标题卡"动态渲染入口。
    DB 查询失败或素材库为空时 fallback 到 mock。
    """
    try:
        assets = (
            db.query(VideoAsset)
            .filter(VideoAsset.file_path.isnot(None))
            .order_by(VideoAsset.ai_tagged_at.desc().nullslast(), VideoAsset.used_count.asc())
            .all()
        )
    except Exception:
        logger.exception("build_real_material_catalog: DB query failed, using mock")
        return mock_material_catalog()

    if not assets:
        logger.warning("build_real_material_catalog: no VideoAsset found, using mock")
        return mock_material_catalog()

    # 性能守卫 (2026-08-07): 素材库可能上千条, 全量塞进 LLM prompt 会让
    # 单次调用达到数百 KB → 120s 超时 + 重试, 规划变 4 分钟。
    # 每个场景只保留 top-N 条 (AI 标签优先, 其次 used_count 低), 并截断
    # 未使用的辅助字段。LLM 规划实际只用清单里少数条目, 裁剪不损失质量。
    catalog = _build_scene_catalog(_group_by_scene(assets))

    # 始终追加动态渲染入口
    catalog["数据图表"] = {"素材列表": [], "默认推荐": None, "动态渲染": True}
    catalog["标题卡"] = {"素材列表": [], "默认推荐": None, "动态渲染": True}

    logger.info(
        "build_real_material_catalog: %d scenes, %d assets",
        len(catalog) - 2, len(assets),
    )
    return catalog


def mock_material_catalog() -> dict[str, Any]:
    """最小化兜底目录 — 仅在 DB 异常或无素材时使用。"""
    return {
        "港口": {
            "素材列表": [
                {"file": "港口_001.mp4", "tags": ["港口", "集装箱", "货轮", "航拍"]},
            ],
            "默认推荐": "港口_001.mp4",
        },
        "工厂": {
            "素材列表": [
                {"file": "工厂_001.mp4", "tags": ["生产线", "机械臂", "智能制造"]},
            ],
            "默认推荐": "工厂_001.mp4",
        },
        "城市": {
            "素材列表": [
                {"file": "城市_001.mp4", "tags": ["CBD", "商业", "都市"]},
            ],
            "默认推荐": "城市_001.mp4",
        },
        "数据图表": {
            "素材列表": [],
            "默认推荐": None,
            "动态渲染": True,
        },
        "标题卡": {
            "素材列表": [],
            "默认推荐": None,
            "动态渲染": True,
        },
    }
