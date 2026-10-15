# -*- coding: utf-8 -*-
"""素材包 (material package) schemas — 聚合/审计/补搜 API 请求响应体."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MaterialItemOut",
    "MaterialPackageBriefOut",
    "MaterialPackageOut",
    "PackageCreateRequest",
    "MaterialItemAddRequest",
    "SupplementSearchRequest",
]


class MaterialItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_type: str
    title: str | None = None
    source_url: str | None = None
    media: str | None = None
    raw_text: str
    fetch_ok: bool
    char_count: int
    search_query: str | None = None
    layer_tags: list[str] | None = None
    # 证据图管线① (2026-09-04): 扫图产物, 前端缩略图行渲染
    images_json: list[dict[str, Any]] | None = None
    created_at: datetime


class MaterialPackageBriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    article_id: str
    title: str | None = None
    status: str
    search_rounds: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    item_count: int = 0


class MaterialPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    article_id: str
    title: str | None = None
    status: str
    audit_json: dict[str, Any] | None = None
    search_rounds: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[MaterialItemOut] = []
    gap_queries: list[str] = []  # 审计缺口搜索词 (前端补搜默认勾选项)


class PackageCreateRequest(BaseModel):
    article_id: str
    urls: list[str] = Field(default_factory=list, max_length=10)
    title: str | None = Field(default=None, max_length=512)


class MaterialItemAddRequest(BaseModel):
    url: str | None = Field(default=None, min_length=5)
    title: str | None = Field(default=None, max_length=512)
    text: str | None = Field(default=None, min_length=1, max_length=15000)


class SupplementSearchRequest(BaseModel):
    # max_length 与补搜池 cap 对齐 (2026-08-16 调研清单并入后 6→10)
    queries: list[str] | None = Field(default=None, max_length=10)
    count: int = Field(default=5, ge=1, le=10)
