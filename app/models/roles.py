# -*- coding: utf-8 -*-
"""角色/形象与产物模型 — 数字人角色、工作流同步、数字人视频、Persona 绑定."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _new_uuid, _now

__all__ = ["Role", "WorkflowSync", "DigitalHumanVideo", "Persona"]


class Role(Base):
    """数字人角色档案 — 多组多机位形象管理.

    reference_image: 用户上传的主参考图(兼容旧逻辑)
    view_groups: 形象组列表, 每组含 4 个机位图:
        [
          {"name": "演播室正装", "cameras": {"1": "/path/cam1.png", "2": ..., "3": ..., "4": ...}},
          {"name": "户外休闲", "cameras": {...}},
        ]
    机位定义:
        1 = 正面半身  2 = 左侧45度半身  3 = 右侧45度半身  4 = 正面特写
    """
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    reference_image: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    view_groups: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # 兼容旧字段(逐步废弃)
    views: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    workflow_used: Mapped[str] = mapped_column(String(64), default="character_three_view")
    seed: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class WorkflowSync(Base):
    """workflows/ 同步审计 — 每次启动期记一行.

    synced: True=已覆盖 False=未覆盖
    reason: skipped / missing / diff / conflict
    """
    __tablename__ = "workflow_syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    runtime_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(128), default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DigitalHumanVideo(Base):
    """数字人说话视频 — 聚合多段音频 + 分镜图 → ComfyUI LTX23 → MP4.

    状态机: pending → aggregating → running → completed / failed
    输入: audio_source_paths (N 段 wav) + storyboard_paths (1-N 张分镜图)
    产物: output_video_path (mp4) + 验证元数据 (duration_actual/fps_actual/frame_count/has_audio_stream)
    """
    __tablename__ = "digital_human_videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    role_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("roles.id"), default=None
    )
    workflow_name: Mapped[str] = mapped_column(String(64), default="digital_human_video_ltx23")

    # ── Inputs ──
    audio_source_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    aggregated_audio_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    aggregated_duration_sec: Mapped[float | None] = mapped_column(Float, default=None)
    storyboard_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    storyboard_prompts: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_duration_sec: Mapped[float] = mapped_column(Float, default=10.0)
    fps: Mapped[int] = mapped_column(Integer, default=24)
    width: Mapped[int] = mapped_column(Integer, default=576)
    height: Mapped[int] = mapped_column(Integer, default=1024)
    seed: Mapped[int | None] = mapped_column(Integer, default=None)

    # ── Outputs / state ──
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending / aggregating / running / completed / failed
    prompt_id: Mapped[str | None] = mapped_column(String(64), default=None)
    output_video_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    duration_actual: Mapped[float | None] = mapped_column(Float, default=None)
    fps_actual: Mapped[float | None] = mapped_column(Float, default=None)
    has_audio_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    frame_count: Mapped[int | None] = mapped_column(Integer, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    comfy_log: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    role: Mapped["Role | None"] = relationship("Role", lazy="selectin")


class Persona(Base):
    """数字人物关联 — 提示词模板 + 音色 + 形象 + 账号/品牌 绑定.

    2026-08-08 整合: 人物即账号。Persona 吸收品牌字段 (brand_name/stamp_name/brand_tag)
    与开场/结束语 (fixed_opening/fixed_ending), 并显式挂 host_id FK 1:1 绑定 Host —
    修复旧逻辑靠 prompt_template 模糊匹配 Host.persona_key 的弱关联断裂 (生产库
    laochen host + laotan persona 实际是同一数字人). 洗稿页选人物 = 选模板 + 音色 +
    形象 + 品牌上屏 + 开结尾, 流水线品牌注入改从 Persona 取数.
    """
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_template: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )  # config/*.txt 文件名 (不含 .txt)
    voice_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voices.id"), default=None
    )
    role_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("roles.id"), default=None
    )
    # 账号/品牌字段 (2026-08-08): 人物即账号, 品牌/口号/开结尾挂在 Persona.
    # 留空时流水线回退 name 全名 / 前 2 字 / 中性标语.
    brand_name: Mapped[str | None] = mapped_column(String(128), default=None)
    stamp_name: Mapped[str | None] = mapped_column(String(16), default=None)
    brand_tag: Mapped[str | None] = mapped_column(String(64), default=None)
    fixed_opening: Mapped[str | None] = mapped_column(Text, default=None)
    fixed_ending: Mapped[str | None] = mapped_column(Text, default=None)
    # 目标读者画像 (2026-08-20): 账号人设级属性, 书级缺省继承. 静读书=拆书受众.
    target_reader: Mapped[str | None] = mapped_column(Text, default=None)
    # 显式账号绑定: Persona ↔ Host 1:1 (修复弱关联断裂)
    host_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("hosts.id"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    voice: Mapped["Voice | None"] = relationship("Voice", lazy="selectin")
    role: Mapped["Role | None"] = relationship("Role", lazy="selectin")
    host: Mapped["Host | None"] = relationship("Host", lazy="selectin")
