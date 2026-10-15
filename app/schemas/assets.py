# -*- coding: utf-8 -*-
"""素材库多维标签体系 / AI 打标 / Pexels resolve / Persona schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .voices import VoiceOut
from .roles import RoleOut

__all__ = [
    # 素材库多维标签
    "VIDEO_ORIENTATION_CHOICES",
    "VIDEO_SOURCE_TYPE_CHOICES",
    "VIDEO_LOCATION_CHOICES",
    "VIDEO_PEOPLE_CHOICES",
    "VIDEO_PREFERENCE_CHOICES",
    "VIDEO_SCENE_CHOICES",
    "VIDEO_SHOT_TYPE_CHOICES",
    "VideoAssetOut",
    "VideoAssetUpdate",
    "VideoAssetPreferenceRequest",
    # AI 素材打标
    "TaggingRunRequest",
    "TaggingProgressOut",
    # 子文件夹导入
    "ImportFolderRequest",
    # Pexels material resolve (ID-003)
    "ResolveItem",
    "ResolveResponse",
    "ResolveRequest",
    "MaterialAssetOut",
    # Persona
    "PersonaCreate",
    "PersonaUpdate",
    "PersonaOut",
]

VIDEO_ORIENTATION_CHOICES = ("landscape", "portrait")
VIDEO_SOURCE_TYPE_CHOICES = ("footage", "creative")
VIDEO_LOCATION_CHOICES = ("domestic", "foreign")
VIDEO_PEOPLE_CHOICES = ("people", "none")
VIDEO_PREFERENCE_CHOICES = ("like", "neutral", "dislike")

VIDEO_SCENE_CHOICES = (
    "城市", "自然", "商业", "科技", "财经", "生活", "美食", "医疗", "教育", "工业",
)
VIDEO_SHOT_TYPE_CHOICES = (
    "远景", "全景", "中景", "近景", "特写", "人像",
    "航拍", "俯拍", "跟拍", "延时", "交通", "建筑",
)  # 2026-09-02: "空镜"退役 (≈people=none 同义, 使用率畸高根因); 扩充镜头语言


def _coerce_json_list(v):
    """SQLite JSON 列偶被写成 str 形态的 '["a","b"]' (RLR 批量导入 2026-08-28~ 脏格式).

    读侧宽容: JSON str → 解析回 list; 非数组 JSON/普通 str → 单元素列表。
    治本修复在 scripts/fix_json_columns.py (数据回写), 此处保证页面不 500。
    """
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else [parsed]
        except (ValueError, TypeError):
            return [v]
    return v


class VideoAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_no: str
    source: str
    pexels_id: int | None
    file_path: str
    orientation: str
    width: int
    height: int
    duration_sec: float
    description_en: str | None
    description_zh: str | None
    photographer: str | None
    source_url: str | None
    raw_query: str | None
    source_type: str
    location: str
    scenes: list[str]
    shot_types: list[str]
    people: str
    preference: str
    tags: list[str]
    ai_tagged_at: datetime | None = None
    ai_tag_model: str | None = None
    ai_confidence: dict | None = None
    ai_tags_extra: dict | None = None
    used_count: int
    created_at: datetime
    updated_at: datetime

    @field_validator("scenes", "shot_types", "tags", mode="before")
    @classmethod
    def _tolerate_str_json(cls, v):
        return _coerce_json_list(v)


class VideoAssetUpdate(BaseModel):
    """用户可手动修正的字段(asset_no 不可改)."""

    source_type: Literal["footage", "creative"] | None = None
    location: Literal["domestic", "foreign"] | None = None
    scenes: list[str] | None = None
    shot_types: list[str] | None = None
    people: Literal["people", "none"] | None = None
    preference: Literal["like", "neutral", "dislike"] | None = None
    description_en: str | None = Field(default=None, max_length=2000)
    description_zh: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None


class VideoAssetPreferenceRequest(BaseModel):
    preference: Literal["like", "neutral", "dislike"]


class TaggingRunRequest(BaseModel):
    """批量打标请求."""
    asset_ids: list[str] | None = Field(
        default=None, description="指定素材 ID 列表；为空则全量打标"
    )


class ImportFolderRequest(BaseModel):
    """导入指定子文件夹并批量打标.

    folder: materials_dir 下相对路径 (如 "1-国外城市街景视频-竖屏"), 或绝对路径.
    scenes / shot_types: 为该文件夹下**本次新导入**的所有素材统一附加的标签;
      与素材既有标签合并去重. 标签可用库内既有枚举, 也可新建.
    """

    folder: str = Field(..., min_length=1, max_length=1024)
    scenes: list[str] = Field(default_factory=list)
    shot_types: list[str] = Field(default_factory=list)


class TaggingProgressOut(BaseModel):
    job_id: str
    status: str  # pending / running / completed / failed / cancelled
    total: int
    done: int
    failed: int
    current_asset_no: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ResolveItem(BaseModel):
    """Pexels resolve 单条结果 — 本地/远程均可,必须含合规署名."""

    id: int | None = Field(default=None, description="本地 material_assets.id")
    local_path: str | None = Field(default=None, description="本地缓存路径")
    source_url: str | None = Field(default=None, description="Pexels 原片 URL")
    degraded: bool = Field(default=False, description="未下到本地,仅元数据")
    reason: str | None = Field(default=None, description="降级原因")
    duration_sec: int
    width: int
    height: int
    fps: int | None = None
    photographer: str
    photographer_url: str
    pexels_url: str
    tags: list[str] = Field(default_factory=list)


class ResolveResponse(BaseModel):
    items: list[ResolveItem] = Field(default_factory=list)
    degraded: bool = Field(default=False)
    reason: str | None = None


class ResolveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=256)
    max_results: int | None = Field(default=None, ge=1, le=20)
    min_duration_sec: int | None = Field(default=None, ge=1, le=300)
    prefer_resolution: str | None = Field(default=None, pattern=r"^(UHD|FHD|HD|SD)$")
    orientation: str | None = Field(default="any", pattern=r"^(landscape|portrait|any)$")


class MaterialAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pexels_id: int
    source_url: str
    pexels_url: str
    photographer: str
    photographer_url: str
    local_path: str | None
    duration_sec: int
    width: int
    height: int
    fps: int | None
    resolution: str
    tags: str
    created_at: datetime


class PersonaCreate(BaseModel):
    """创建人物 (即账号). 品牌字段可留空, 留空时流水线回退 name/前 2 字/中性标语."""

    name: str = Field(..., min_length=1, max_length=128)
    prompt_template: str = Field(..., min_length=1, max_length=128, description="config/*.txt 文件名(不含 .txt)")
    voice_id: str | None = None
    role_id: str | None = None
    # 账号/品牌字段 (2026-08-08): 人物即账号
    brand_name: str | None = Field(default=None, max_length=128)
    stamp_name: str | None = Field(default=None, max_length=16)
    brand_tag: str | None = Field(default=None, max_length=64)
    fixed_opening: str | None = Field(default=None, max_length=200)
    fixed_ending: str | None = Field(default=None, max_length=200)
    host_id: str | None = Field(default=None, description="绑定 Host ID; 缺省由迁移/后端自动绑定")
    target_reader: str | None = Field(default=None, max_length=500, description="目标读者画像 (账号人设级, 书级缺省继承)")


class PersonaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_template: str | None = Field(default=None, min_length=1, max_length=128)
    voice_id: str | None = None
    role_id: str | None = None
    brand_name: str | None = Field(default=None, max_length=128)
    stamp_name: str | None = Field(default=None, max_length=16)
    brand_tag: str | None = Field(default=None, max_length=64)
    fixed_opening: str | None = Field(default=None, max_length=200)
    fixed_ending: str | None = Field(default=None, max_length=200)
    host_id: str | None = None
    target_reader: str | None = Field(default=None, max_length=500)


class PersonaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prompt_template: str
    voice_id: str | None
    role_id: str | None
    brand_name: str | None = None
    stamp_name: str | None = None
    brand_tag: str | None = None
    fixed_opening: str | None = None
    fixed_ending: str | None = None
    host_id: str | None = None
    target_reader: str | None = None
    voice: VoiceOut | None = None
    role: RoleOut | None = None
    created_at: datetime
    updated_at: datetime


# ── P线在线搜索 (2026-08-25): 素材库页预览式搜索 + 勾选入库 ──
class PexelsOnlineSearchRequest(BaseModel):
    """在线搜索请求 — 只返回元数据预览, 不触发下载."""
    query: str = Field(..., min_length=1, max_length=256)
    orientation: str | None = Field(default="any", pattern=r"^(landscape|portrait|any)$")
    per_page: int = Field(default=40, ge=1, le=80)  # 对齐 Pexels API 单页上限
    page: int = Field(default=1, ge=1)               # 翻页 (2026-08-25)


class PexelsImportItem(BaseModel):
    """待入库候选 — 搜索结果的 video dict 原样回传 (含 video_files 供选流)."""
    video: dict


class PexelsImportRequest(BaseModel):
    """勾选入库请求 — 批量下载选中的 Pexels 候选."""
    query: str = Field(default="", max_length=256)  # 用于打标/溯源 raw_query
    prefer_resolution: str | None = Field(default=None, pattern=r"^(UHD|FHD|HD|SD)$")
    items: list[PexelsImportItem] = Field(..., min_length=1, max_length=40)


class PexelsImageSearchRequest(BaseModel):
    """图片在线搜索请求 (2026-09-06) — 图片反推页参考图, 只返回预览元数据.

    orientation 比 video 搜索多 square: Pexels 图片 API 原生支持三方。
    """
    query: str = Field(..., min_length=1, max_length=256)
    orientation: str | None = Field(default="any", pattern=r"^(landscape|portrait|square|any)$")
    per_page: int = Field(default=24, ge=1, le=80)
    page: int = Field(default=1, ge=1)
