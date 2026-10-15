# -*- coding: utf-8 -*-
"""素材摄入产线模型 (2026-08-30): MaterialIngestJob — yt 链接/实体批 → 五段流水."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _new_uuid, _now


class MaterialIngestJob(Base):
    """一次摄入任务 (单视频或实体批).

    五段流水: download → split → ocr → tag → register
    VPN 门控: download 需代理 ON; tag (GPU) 需代理 OFF — 不满足进 waiting_*,
    检测到满足自动续跑; 超时 30min 进 paused_* (resume 按钮续).
    断点: stage 落库, resume 从当前 stage 重入 (各段幂等: 已处理的 clip 跳过)。
    """
    __tablename__ = "material_ingest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    mode: Mapped[str] = mapped_column(String(16))  # url / entity
    entity: Mapped[str | None] = mapped_column(String(64), default=None)
    source_url: Mapped[str | None] = mapped_column(String(512), default=None)
    video_id: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    title: Mapped[str | None] = mapped_column(String(255), default=None)
    # queued/downloading/splitting/ocr/tagging/registering/done/failed/
    # waiting_vpn_on/waiting_vpn_off/paused_vpn_on/paused_vpn_off
    stage: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    stats_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    parent_id: Mapped[str | None] = mapped_column(String(36), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
