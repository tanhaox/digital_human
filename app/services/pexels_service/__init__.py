"""Pexels resolve 服务 (包化重构).

原单文件 app/services/pexels_service.py 拆分为本包, 外部导入零改动。
公共 API 经 __all__ 导出:
  - pexels_service  (模块级单例, 视觉导演 import-only 调用范式)
  - PexelsService   (服务类, 编排 + 私有接口转发)
  - PexelsAuthError / PexelsResolveError / ResolveItem / PEXELS_API_BASE
"""
from __future__ import annotations

from app.services.pexels_service.service import PexelsService, pexels_service
from app.services.pexels_service.types import (
    PEXELS_API_BASE,
    PexelsAuthError,
    PexelsResolveError,
    ResolveItem,
)

__all__ = [
    "PexelsService",
    "pexels_service",
    "PEXELS_API_BASE",
    "PexelsAuthError",
    "PexelsResolveError",
    "ResolveItem",
]
