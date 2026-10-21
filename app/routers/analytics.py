"""数据看板 API — 抖音账号逐日指标 + 粉丝增长摘要 (/web/analytics.html 消费)."""
from __future__ import annotations

from fastapi import APIRouter

from ..services.analytics_service import dashboard_payload

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/daily")
def daily():
    """全量逐日行 (douyin_daily) + 摘要 (异常日/常态基线/周桶/相关性)."""
    return dashboard_payload()
