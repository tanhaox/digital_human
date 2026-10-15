# -*- coding: utf-8 -*-
"""导演台任务模型 — 视觉导演 job/slot 与 HF 视觉渲染 job."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _new_uuid, _now

__all__ = ["DirectorJob", "DirectorSlot", "VisualRenderJob"]


class DirectorJob(Base):
    """视觉导演 2.0 任务 — 整段 TTS 音频生成后触发,输出 slot 工序单.

    状态机: planning → executing → reviewing → completed / failed
    """

    __tablename__ = "director_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    script_id: Mapped[str] = mapped_column(String(36), ForeignKey("scripts.id"), nullable=False)
    audio_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audio_files.id"), default=None
    )

    video_format: Mapped[str] = mapped_column(String(16), default="portrait")
    # 启用的管线约束 (规范化字符串: "c,p,h" 升序; None=全启用).
    # create/execute/retry 缺省时回退到此列, 避免重启/重试丢约束 → 全启用.
    pipelines: Mapped[str | None] = mapped_column(String(32), default=None)
    title: Mapped[str | None] = mapped_column(String(256), default=None)
    view_group_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(32), default="planning"
    )  # planning / executing / reviewing / completed / failed
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 剪映草稿名 (2026-08-25): J 线导出成功后记录, 详情页常驻显示, 剪映里按名找草稿
    jy_draft_name: Mapped[str | None] = mapped_column(String(256), default=None)
    total_duration_sec: Mapped[float | None] = mapped_column(Float, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    script: Mapped["Script"] = relationship("Script", lazy="selectin")
    audio_file: Mapped["AudioFile | None"] = relationship("AudioFile", lazy="selectin")
    slots: Mapped[list["DirectorSlot"]] = relationship(
        "DirectorSlot",
        back_populates="director_job",
        cascade="all, delete-orphan",
        order_by="DirectorSlot.slot_index",
        lazy="selectin",
    )


class DirectorSlot(Base):
    """视觉导演 2.0 时间槽位 — 每个 slot 由下游工作流独立执行.

    状态机: queued → running → completed / failed / replaced
    """

    __tablename__ = "director_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    director_job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("director_jobs.id"), nullable=False
    )
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)

    start_sec: Mapped[float] = mapped_column(Float, nullable=False)
    end_sec: Mapped[float] = mapped_column(Float, nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    text_context: Mapped[str | None] = mapped_column(Text, default=None)
    segment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("segments.id"), default=None
    )

    visual_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # host / broll_pexels / broll_local / hf_chart / hf_title / mixed_host_broll / hf_opening / hf_quote / evidence_image
    workflow: Mapped[str] = mapped_column(String(32), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    camera_angle: Mapped[int] = mapped_column(Integer, default=1)  # 1-4 机位
    view_group_index: Mapped[int] = mapped_column(Integer, default=0)  # 形象组索引

    status: Mapped[str] = mapped_column(
        String(32), default="queued"
    )  # queued / running / completed / failed / replaced / skipped
    output_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    director_job: Mapped["DirectorJob"] = relationship("DirectorJob", back_populates="slots")
    segment: Mapped["Segment | None"] = relationship("Segment", lazy="selectin")


class VisualRenderJob(Base):
    """HF 视觉渲染任务 — 标准 JSON 入参 → HF npx 渲染 → MP4 + 关键帧 + manifest.

    独立管线:不接 ComfyUI / 不占 GPU / 与 DigitalHumanVideo 状态机解耦.
    状态机: queued → preparing → rendering → validating → completed / failed / cancelled.
    产物: output_path (mp4) + preview_frames (first/middle/final png) + manifest_path.
    """
    __tablename__ = "visual_render_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    template_id: Mapped[str] = mapped_column(String(64), default="news-data-v1")
    template_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    composition_id: Mapped[str] = mapped_column(String(64), default="news_main")

    # ── Inputs ──
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_path: Mapped[str | None] = mapped_column(String(1024), default=None)

    # ── State ──
    status: Mapped[str] = mapped_column(String(32), default="queued")
    # queued / preparing / rendering / validating / completed / failed / cancelled
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # ── Outputs ──
    output_dir: Mapped[str | None] = mapped_column(String(1024), default=None)
    output_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    manifest_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    preview_frames: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    media: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    render_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
