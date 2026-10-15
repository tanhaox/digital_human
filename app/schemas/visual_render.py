# -*- coding: utf-8 -*-
"""Visual render (HyperFrames) schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "VisualRenderJobCreate",
    "VisualRenderJobOut",
    "TemplateInfo",
    "GenerateVisualResponse",
]


class VisualRenderJobCreate(BaseModel):
    """Body for ``POST /api/visual-render/jobs``.

    ``input_data`` is validated server-side against the template's jsonschema.
    """
    template_id: str = Field(default="news-data-v1", min_length=1, max_length=64)
    input_data: dict[str, Any]


class VisualRenderJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    template_id: str
    template_version: str
    composition_id: str
    status: str
    input_path: str | None = None
    output_path: str | None = None
    manifest_path: str | None = None
    preview_frames: dict[str, str] = Field(default_factory=dict)
    media: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    render_seconds: float | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @computed_field  # type: ignore[misc]
    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")


class TemplateInfo(BaseModel):
    template_id: str
    version: str
    composition_id: str
    duration_sec_range: list[int]
    required_input: list[str]
    json_schema: dict[str, Any]


class GenerateVisualResponse(BaseModel):
    job_id: str
    status: str
    output_path: str | None = None
    manifest_path: str | None = None
    media: dict[str, Any] = Field(default_factory=dict)
    render_seconds: float | None = None
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
