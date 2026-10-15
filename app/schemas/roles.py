# -*- coding: utf-8 -*-
"""角色一致性 / ComfyUI workflow schemas (角色多视图定型)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RoleOut",
    "RoleCreate",
    "RoleUpdate",
    "GenerateViewsRequest",
    "GenerateViewsResponse",
    "ApplyRoleResponse",
    "WorkflowSyncOut",
    "ComfyUIWorkflowInfo",
    "ComfyUISubmitRequest",
    "ComfyUISubmitResponse",
]


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    reference_image: str
    description: str | None
    view_groups: list[dict[str, Any]] = []
    views: dict[str, Any]
    workflow_used: str
    seed: int | None
    created_at: datetime
    updated_at: datetime


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    reference_image: str = Field(..., min_length=1, max_length=512, description="参考图绝对路径")
    description: str = Field(default="", max_length=1000)
    workflow_used: str = Field(default="character_three_view", max_length=64)
    seed: int | None = Field(default=None, ge=0, le=2147483647)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    seed: int | None = Field(default=None, ge=0, le=2147483647)


class GenerateViewsRequest(BaseModel):
    """角色多视图生成请求 — 提交 ComfyUI workflow"""

    seed: int | None = Field(default=None, ge=0, le=2147483647)
    description_override: str | None = Field(
        default=None, max_length=1000, description="覆盖角色描述(留空使用角色自身 description)"
    )
    reference_image: str | None = Field(
        default=None, max_length=512, description="覆盖参考图路径(留空使用角色自身 reference_image)"
    )


class GenerateViewsResponse(BaseModel):
    """角色多视图生成响应"""

    role_id: str
    prompt_id: str | None
    status: str  # submitted / running / completed / failed
    views: dict[str, str]
    error: str | None = None


class ApplyRoleResponse(BaseModel):
    """角色应用到主流水线 — 当前仅返回入参结构(留接口位)"""

    applied: bool
    role_id: str
    mainstream_input: dict[str, Any]
    note: str


class WorkflowSyncOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_name: str
    source_sha256: str | None
    runtime_sha256: str | None
    synced: bool
    reason: str
    synced_at: datetime


class ComfyUIWorkflowInfo(BaseModel):
    """manifest.yaml 中的 workflow 元数据"""

    name: str
    source: str
    runtime_path: str
    auto_sync: bool
    description: str | None = None
    output_count: int | None = None
    output_view_order: list[str] | None = None


class ComfyUISubmitRequest(BaseModel):
    """通用 workflow 提交(测试 / 调试用)"""

    workflow_name: str = Field(default="character_three_view", max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict, description="要注入的 inputs 字典")
    role_id: str | None = Field(default=None, description="可选:产物落盘目标角色目录")


class ComfyUISubmitResponse(BaseModel):
    prompt_id: str | None
    status: str
    views: dict[str, str] = Field(default_factory=dict)
    elapsed_sec: float | None = None
    error: str | None = None
