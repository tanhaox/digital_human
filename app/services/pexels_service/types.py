"""Pexels resolve — 类型 / 常量 / 异常定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PEXELS_API_BASE = "https://api.pexels.com/v1"

__all__ = [
    "PEXELS_API_BASE",
    "PexelsAuthError",
    "PexelsResolveError",
    "ResolveItem",
]


class PexelsAuthError(RuntimeError):
    """PEXELS_API_KEY 无效或缺失时抛出(不静默)."""


class PexelsResolveError(RuntimeError):
    """可恢复的内部错误,resolve 会 catch 并降级."""


@dataclass
class ResolveItem:
    """单条 resolve 结果 — 与 app.schemas.ResolveItem 字段对齐."""

    duration_sec: int
    width: int
    height: int
    photographer: str
    photographer_url: str
    pexels_url: str
    id: int | None = None
    pexels_id: int | None = None
    local_path: str | None = None
    source_url: str | None = None
    degraded: bool = False
    reason: str | None = None
    fps: int | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "pexels_id": self.pexels_id, "local_path": self.local_path,
            "source_url": self.source_url, "degraded": self.degraded, "reason": self.reason,
            "duration_sec": self.duration_sec, "width": self.width, "height": self.height,
            "fps": self.fps, "photographer": self.photographer,
            "photographer_url": self.photographer_url, "pexels_url": self.pexels_url,
            "tags": self.tags,
        }
