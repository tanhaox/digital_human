# -*- coding: utf-8 -*-
"""Director 2.0 schemas (导演工序单)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "DirectorSlotPlan",
    "DirectorPlan",
    "DirectorJobCreate",
    "DirectorSlotOut",
    "DirectorJobOut",
    "DirectorDirectResponse",
    "RetrySlotResponse",
    "ComposeResponse",
]


class DirectorSlotPlan(BaseModel):
    """导演 Agent 输出的单个 slot 计划(落 plan_json 前 schema 校验)."""

    slot_index: int = Field(..., ge=0)
    start_sec: float = Field(..., ge=0)
    end_sec: float = Field(..., ge=0)
    duration_sec: float | None = None
    text_context: str | None = None
    segment_id: str | None = None
    visual_type: Literal[
        "host",
        "broll_pexels",
        "broll_local",
        "hf_chart",
        "hf_title",
        "mixed_host_broll",
    ]
    workflow: Literal[
        "host",
        "broll_pexels",
        "broll_local",
        "hf_chart",
        "hf_title",
        "mixed_host_broll",
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    camera_angle: int = Field(default=1, ge=1, le=4)
    view_group_index: int = Field(default=0, ge=0)


class DirectorPlan(BaseModel):
    """导演 Agent 输出的完整工序单."""

    slots: list[DirectorSlotPlan]
    title: str | None = None
    reasoning: str | None = None


class DirectorJobCreate(BaseModel):
    """手动创建导演任务(通常由 /scripts/{id}/direct 自动创建)."""

    script_id: str
    audio_file_id: str | None = None
    view_group_index: int = 0
    pipelines: str | None = None  # 逗号分隔启用的管线, e.g. "c,h". 默认全开.
    # 画幅覆盖 (2026-08-24): 洗稿时选错可在导演页改; 与 script 现值不同则同步写回
    # script.video_format, plan 线程继承该值 → 整条产线 (规划/slot 渲染/合成/J线) 跟随。
    video_format: Literal["portrait", "landscape", "square"] | None = None


class DirectorSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    director_job_id: str
    slot_index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    text_context: str | None
    segment_id: str | None
    visual_type: str
    workflow: str
    params_json: dict[str, Any]
    camera_angle: int = 1
    view_group_index: int = 0
    status: str
    output_path: str | None
    error_code: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime


class DirectorJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    script_id: str
    audio_file_id: str | None
    title: str | None = None
    video_format: str = "portrait"
    pipelines: str | None = None
    view_group_index: int = 0
    status: str
    plan_json: dict[str, Any]
    total_duration_sec: float | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    slots: list[DirectorSlotOut] = Field(default_factory=list)

    @computed_field  # type: ignore[misc]
    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed")


class DirectorDirectResponse(BaseModel):
    job_id: str
    status: str
    slot_count: int
    message: str


class RetrySlotResponse(BaseModel):
    slot_id: str
    status: str
    message: str


class ComposeResponse(BaseModel):
    job_id: str
    status: str
    output_path: str | None = None
    duration_sec: float | None = None
    message: str
