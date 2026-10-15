"""Pexels resolve — PexelsService 主类 (编排 + 私有接口转发).

模块级单例 `pexels_service` 在此定义, 供 broll_pexels 等 import-only 调用。
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_config
from app.services.pexels_service._candidates import process_candidates
from app.services.pexels_service._db import (
    get_dislike_pexels_ids,
    get_quota_used_today,
)
from app.services.pexels_service._degraded import (
    fill_with_degraded,
    merge_with_degraded,
)
from app.services.pexels_service._http import search_pexels
from app.services.pexels_service._local import query_local
from app.services.pexels_service._session import (
    close_session,
    ensure_config,
    ensure_session,
)
from app.services.pexels_service.types import (
    PexelsAuthError,
    ResolveItem,
)
from app.services.pexels_utils import RESOLUTION_WIDTHS

logger = logging.getLogger(__name__)

__all__ = ["PexelsService", "pexels_service"]


class PexelsService:
    """Pexels resolve 服务单例."""

    def __init__(self) -> None:
        self._session: Any = None
        self._api_key: str | None = None
        self._requests_session: Any = None

    # ── 配置 / session 懒加载 (转发, 私有接口对测试零破坏) ──

    def _ensure_config(self) -> None:
        ensure_config(self)

    def _ensure_session(self) -> Any:
        return ensure_session(self)

    def _close_session(self) -> None:
        close_session(self)

    def _cfg(self) -> Any:
        return get_config().defaults

    def _get_quota_used_today(self, db: Any) -> int:
        return get_quota_used_today(db)

    # ── 公开 API ──

    def resolve(
        self, query: str, max_results: int | None = None,
        min_duration_sec: int | None = None, prefer_resolution: str | None = None,
        orientation: str = "any", exclude_pexels_ids: set[int] | None = None,
    ) -> list[ResolveItem]:
        try:
            self._ensure_config()
        except PexelsAuthError:
            raise

        exclude_pexels_ids = exclude_pexels_ids or set()

        cfg = self._cfg()
        max_results = max_results or cfg.pexels_default_max_results
        min_duration_sec = min_duration_sec or cfg.pexels_min_duration_sec
        prefer_resolution = prefer_resolution or cfg.pexels_preferred_resolution
        if prefer_resolution not in RESOLUTION_WIDTHS:
            prefer_resolution = "FHD"

        tags = [t.strip().lower() for t in query.split() if t.strip()]
        tags_str = ",".join(tags)

        try:
            db = self._ensure_session()
            # 用户标记为厌恶的素材永久拉黑, 不再返回也不再下载
            dislike_ids = get_dislike_pexels_ids(db)
            exclude_pexels_ids = exclude_pexels_ids | dislike_ids

            local_items = query_local(
                self, db, tags, max_results, min_duration_sec, orientation,
                exclude_pexels_ids=exclude_pexels_ids,
            )
            if len(local_items) >= max_results:
                return local_items[:max_results]

            needed = max_results - len(local_items)
            quota = cfg.pexels_daily_download_quota
            used_today = self._get_quota_used_today(db)
            remaining_quota = max(0, quota - used_today)

            try:
                videos = search_pexels(self, query, per_page=max(needed * 3, 10), orientation=orientation)
            except PexelsAuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pexels search failed: %s", exc)
                return merge_with_degraded(local_items, needed, reason="search_failed")

            downloaded = process_candidates(
                self, db, videos, needed, prefer_resolution, min_duration_sec,
                orientation, remaining_quota, tags_str, cfg.materials_dir,
                exclude_pexels_ids=exclude_pexels_ids, raw_query=query,
            )
            result = local_items + downloaded
            if len(result) < max_results:
                result = fill_with_degraded(
                    result, videos, max_results, prefer_resolution, min_duration_sec,
                    exclude_pexels_ids=exclude_pexels_ids,
                )

            db.commit()
            return result[:max_results]

        except PexelsAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pexels resolve unexpected error: %s", exc)
            return []
        finally:
            self._close_session()

    def _descending_attempt(
        self, query: str,
        max_results: int | None, min_duration_sec: int | None,
        prefer_resolution: str | None, orientation: str,
        exclude_pexels_ids: set[int] | None,
    ) -> list[ResolveItem]:
        """单次降维搜索尝试; 失败返回空列表."""
        try:
            return self.resolve(
                query, max_results=max_results,
                min_duration_sec=min_duration_sec,
                prefer_resolution=prefer_resolution,
                orientation=orientation,
                exclude_pexels_ids=exclude_pexels_ids,
            )
        except PexelsAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve_descending attempt %r failed: %s", query, exc)
            return []

    def resolve_descending(
        self, keywords: list[str], max_results: int | None = None,
        min_duration_sec: int | None = None, prefer_resolution: str | None = None,
        orientation: str = "any", exclude_pexels_ids: set[int] | None = None,
    ) -> tuple[list[ResolveItem], str | None]:
        """降维搜索 (2026-08-01, 用户指定策略, 见 [[pexels-descending-search]]).

        LLM 给出按重要性排序的关键词数组 (最多 6 个, 第 1 个是全局主体关键词).
        依次用 [全部, 去掉末尾1个, 去掉末尾2个, ...] 搜索, 命中非空即停.
        主关键词永远保留在查询里 → 保证全片画面地域/主题一致性.

        Args:
            keywords: 按重要性排序的检索词, 例如 ["china","street","busy","traffic"].
                      keywords[0] 是全局主体词, 必须出现在每次尝试中.

        Returns:
            (items, used_query): items 为降维后首个非空结果 (可能为空); used_query
            为实际命中的查询串 (供日志/调试), 全部失败时返回 ([], None).
        """
        clean = [k.strip() for k in keywords if k and k.strip()]
        if not clean:
            return [], None
        clean = clean[:6]
        for n in range(len(clean), 0, -1):
            query = " ".join(clean[:n])
            items = self._descending_attempt(
                query, max_results, min_duration_sec,
                prefer_resolution, orientation, exclude_pexels_ids,
            )
            if items:
                logger.info("resolve_descending hit at %d keyword(s): %r", n, query)
                return items, query
        return [], None


# 模块级单例
pexels_service = PexelsService()
