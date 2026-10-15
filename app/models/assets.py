# -*- coding: utf-8 -*-
"""素材与成品模型 — Pexels 素材缓存、下载配额、B-roll 素材库、成品视频库."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _new_uuid, _now

__all__ = ["MaterialAsset", "DownloadLog", "VideoAsset", "VideoOutput", "ImageExtraction"]


class MaterialAsset(Base):
    """Pexels 视频素材本地缓存表.

    视觉导演调用 pexels_service.resolve() 时优先命中本地行,避免重复下载.
    degraded 行 (local_path is None) 用于 quota 超限/下载失败时仍返回元数据.
    """

    __tablename__ = "material_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pexels_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    pexels_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    photographer: Mapped[str] = mapped_column(String(256), nullable=False)
    photographer_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    local_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    fps: Mapped[int | None] = mapped_column(Integer, default=None)
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    tags: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DownloadLog(Base):
    """Pexels 下载配额审计 — 按自然日重置."""

    __tablename__ = "download_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    pexels_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (Index("idx_download_logs_date", "date"),)


class VideoAsset(Base):
    """B-roll 素材库条目 — 给导演 slot 选用的视频片段.

    来源: pexels 下载 / 本地导入 / 手动上传
    支持: 唯一编号、多维标签(方向/来源/画面/场景/镜头/人物/偏好)、使用计数
    """

    __tablename__ = "video_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    asset_no: Mapped[str] = mapped_column(
        String(16), nullable=False, unique=True, index=True
    )  # 自动生成的唯一编号, e.g. V20260802-0001
    source: Mapped[str] = mapped_column(String(16), default="pexels")  # pexels / local / manual
    pexels_id: Mapped[int | None] = mapped_column(Integer, default=None, unique=True)  # Pexels video ID 去重
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # 方向(从宽高比自动推断)
    orientation: Mapped[str] = mapped_column(String(16), default="landscape")  # portrait / landscape
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    # 描述 — 中英对照(不再被台词污染)
    description_en: Mapped[str | None] = mapped_column(Text, default=None)
    description_zh: Mapped[str | None] = mapped_column(Text, default=None)
    # 元数据
    photographer: Mapped[str | None] = mapped_column(String(128), default=None)
    source_url: Mapped[str | None] = mapped_column(String(512), default=None)
    raw_query: Mapped[str | None] = mapped_column(String(512), default=None)  # 入库时的原始搜索 query
    # 多维标签体系
    source_type: Mapped[str] = mapped_column(String(16), default="footage")  # footage 实拍 / creative 创意
    location: Mapped[str] = mapped_column(String(16), default="foreign")  # domestic 国内 / foreign 国外
    scenes: Mapped[list[str]] = mapped_column(JSON, default=list)  # 场景(多选)
    shot_types: Mapped[list[str]] = mapped_column(JSON, default=list)  # 镜头类型(多选)
    people: Mapped[str] = mapped_column(String(16), default="none")  # people 有人 / none 无人
    preference: Mapped[str] = mapped_column(String(16), default="neutral")  # like / neutral / dislike
    # 旧标签字段保留,用于兼容未重新下载的素材;新素材使用多维标签
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # AI 打标追踪字段
    ai_tagged_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    ai_tag_model: Mapped[str | None] = mapped_column(String(64), default=None)
    ai_confidence: Mapped[dict | None] = mapped_column(JSON, default=None)
    # 内容质量分 (2026-08-16 烂素材治理③): VLM 按"专业剪辑师验收标准"打 1-10,
    # 含 generic-stock 惩罚 — 与维度打标不同, 这是对"素材好不好"的直接回答
    quality_score: Mapped[float | None] = mapped_column(Float, default=None)
    quality_reason: Mapped[str | None] = mapped_column(Text, default=None)
    ai_tags_extra: Mapped[dict | None] = mapped_column(JSON, default=None)
    # 使用统计
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    # 冷却调度 (2026-08-28 用户令"每10次才能复用一次"): 最近一次被选用时的全局序号;
    # 当前序号 - last_used_seq < cooldown(10) 的素材在所有检索中被排除。
    last_used_seq: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("idx_video_assets_asset_no", "asset_no"),
        Index("idx_video_assets_orientation", "orientation"),
        Index("idx_video_assets_source_type", "source_type"),
        Index("idx_video_assets_location", "location"),
        Index("idx_video_assets_people", "people"),
        Index("idx_video_assets_preference", "preference"),
    )


class VideoOutput(Base):
    """成品视频登记 — 导演台合成后的最终 MP4.

    简单登记: 标题/日期/画幅/路径/时长
    """
    __tablename__ = "video_outputs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    job_id: Mapped[str | None] = mapped_column(String(36), default=None)  # 关联导演任务
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    orientation: Mapped[str] = mapped_column(String(16), default="portrait")
    duration_sec: Mapped[float | None] = mapped_column(Float, default=None)
    video_format: Mapped[str | None] = mapped_column(String(16), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ImageExtraction(Base):
    """图片反推提取记录 (2026-09-06) — 反推页「提取库」持久层.

    一行 = 一次引擎提取: 图 + 目标类型(人物/道具/场景) + 重点范围 + 项目标签
    + 提取字段 JSON。图本地落盘 image_path, 原始出处记 source_url。
    project 为自由标签 (空串 = 未分项目), 前端按 「全部/未分/各标签」 过滤。
    """
    __tablename__ = "image_extractions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    image_path: Mapped[str] = mapped_column(String(1024), nullable=False)      # 本地落盘路径
    source_type: Mapped[str] = mapped_column(String(16), default="local")      # pexels | local | paste
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)  # Pexels 页面/图 URL
    filename: Mapped[str | None] = mapped_column(String(256), default=None)     # 原文件名/pexels_id
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)        # person | prop | scene
    focus: Mapped[str] = mapped_column(String(128), default="all")              # all | 逗号分隔 key
    project: Mapped[str] = mapped_column(String(128), default="")               # 项目标签, 空=未分
    values_json: Mapped[str] = mapped_column(Text, nullable=False)              # 提取字段 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_image_extractions_target", "target_type"),
        Index("idx_image_extractions_project", "project"),
        Index("idx_image_extractions_created", "created_at"),
    )
