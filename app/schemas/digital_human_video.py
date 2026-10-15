# -*- coding: utf-8 -*-
"""DigitalHumanVideo schemas (LTX23 音频→视频)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "StoryboardItem",
    "DigitalHumanVideoCreate",
    "DigitalHumanVideoOut",
    "EligibleAudioItem",
    "EligibleAudioResponse",
    "StoryboardUploadResponse",
    "GenerateVideoResponse",
]


class StoryboardItem(BaseModel):
    """单张分镜图输入 — 与 lt_video_builder.LTXVAddGuide 节点 1:1 映射."""

    filename: str = Field(..., description="ComfyUI 上传后的 image filename")
    frame_idx: int = Field(default=0, ge=0, description="该图锚定的帧下标")
    strength: float = Field(default=0.85, ge=0.0, le=1.0, description="条件强度 0-1")


class DigitalHumanVideoCreate(BaseModel):
    """创建 DigitalHumanVideo 行 (status=pending)."""

    role_id: str | None = Field(default=None, max_length=36)
    audio_source_paths: list[str] = Field(
        default_factory=list,
        description="从 /api/audio 已生成的 wav 路径列表 (绝对路径)",
    )
    storyboard_prompts: list[str] = Field(default_factory=list, max_length=20)
    target_duration_sec: float = Field(default=10.0, ge=5.0, le=60.0)
    fps: int = Field(default=24, ge=12, le=60)
    width: int = Field(default=576, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    seed: int | None = Field(default=None, ge=0, le=2147483647)


class DigitalHumanVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role_id: str | None
    workflow_name: str
    audio_source_paths: list[str]
    aggregated_audio_path: str | None
    aggregated_duration_sec: float | None
    storyboard_paths: list[str]
    storyboard_prompts: list[str]
    target_duration_sec: float
    fps: int
    width: int
    height: int
    seed: int | None
    status: str
    prompt_id: str | None
    output_video_path: str | None
    duration_actual: float | None
    fps_actual: float | None
    has_audio_stream: bool
    frame_count: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class EligibleAudioItem(BaseModel):
    """可聚合的音频候选 — /api/audio 已生成的 wav."""

    label: str
    path: str
    duration_sec: float | None
    sample_rate: int | None
    source_job_id: str | None
    source_segment_id: str | None


class EligibleAudioResponse(BaseModel):
    items: list[EligibleAudioItem]
    total_duration_sec: float


class StoryboardUploadResponse(BaseModel):
    video_id: str
    uploaded_paths: list[str]
    storyboard_prompts: list[str]


class GenerateVideoResponse(BaseModel):
    """生成完成响应 — 含 ffprobe 验证结果."""

    video_id: str
    status: str
    prompt_id: str | None
    output_video_path: str | None
    aggregated_audio_path: str | None
    aggregated_duration_sec: float | None
    duration_actual: float | None
    fps_actual: float | None
    has_audio_stream: bool
    frame_count: int | None
    validation_issues: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    elapsed_sec: float | None = None
    error: str | None = None
