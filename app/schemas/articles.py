# -*- coding: utf-8 -*-
"""文章 / 脚本 / 音频片段 schemas (API 请求响应体)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ArticleCreate",
    "RewriteRequest",
    "CorrectRequest",
    "TtsAdaptRequest",
    "FetchUrlRequest",
    "FetchUrlResponse",
    "ArticleUpdate",
    "ArticleOut",
    "SegmentOut",
    "ArticleBriefOut",
    "ScriptOut",
    "ScriptUpdate",
    "SegmentUpdate",
    "AudioJobOut",
    "AudioFileOut",
    "HostOut",
]


class ArticleCreate(BaseModel):
    title: str | None = None
    source_url: str | None = None
    raw_text: str = Field(..., min_length=1, max_length=15000)
    track: Literal["tech", "geo"] | None = Field(
        default="tech", description="赛道: tech=科技/商业(默认) / geo=地缘/国际 — 驱动评论层与七层审计分支"
    )
    # 证据图管线① (2026-09-04): 抓取 URL 时「搜图」开关抓到的本页候选图
    images: list[dict[str, Any]] | None = Field(
        default=None, description="原文页 <img> 候选图 [{url,alt,w,h}]; 非空=搜图总闸开"
    )


class RewriteRequest(BaseModel):
    model: Literal["flash", "pro"] | None = None
    prompt_template: str | None = None
    video_format: str | None = None  # portrait / landscape / square
    perspective: str | None = Field(default=None, max_length=500, description="洗稿前的补充观点（可选）")
    host_id: str | None = Field(default=None, description="绑定的数字人账号（Host）ID；缺省走 config 默认 host")
    persona_id: str | None = Field(default=None, description="绑定的数字人（Persona）ID；人物即账号，选人物自动带出 host_id 与品牌/开结尾")
    material_package_id: str | None = Field(
        default=None, description="素材包ID；传入时素材包内容按层注入洗稿上下文（2026-08-15）"
    )


class CorrectRequest(BaseModel):
    """洗稿后的修正观点请求."""
    model: Literal["flash", "pro"] | None = None
    perspective: str = Field(..., min_length=1, max_length=500, description="修正观点")


class TtsAdaptRequest(BaseModel):
    """口播适配请求: 编辑区当前全文 (可能含未保存的手改)."""
    text: str = Field(..., min_length=1, max_length=20000, description="待读法适配的口播全文")


class FetchUrlRequest(BaseModel):
    url: str = Field(..., min_length=5)
    # 证据图管线① (2026-09-04): 搜图总闸 — 开时顺手提取本页 <img> 候选图
    with_images: bool = Field(default=False, description="是否提取页面候选证据图")


class FetchUrlResponse(BaseModel):
    ok: bool
    title: str | None = None
    source_url: str | None = None
    raw_text: str | None = None
    error: str | None = None
    images: list[dict[str, Any]] | None = None  # with_images=True 时返回


class ArticleUpdate(BaseModel):
    title: str | None = None
    source_url: str | None = None
    raw_text: str | None = Field(default=None, min_length=1, max_length=15000)


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None
    source_url: str | None
    status: str
    track: str | None = None  # 赛道 tech/geo (2026-08-16)
    perspective_1: str | None = None
    deconstruct_json: dict[str, Any] | None = None  # 评论层 (2026-08-15)
    images_json: list[dict[str, Any]] | None = None  # 证据图管线① (2026-09-04)
    created_at: datetime
    updated_at: datetime


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    line_index: int
    text: str
    control_chars: dict[str, Any] | None
    segment_type: str | None
    selected_for_host: bool
    host_order: int
    estimated_duration: float | None


class ArticleBriefOut(BaseModel):
    """文章精简输出（仅 id + title）."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    article_id: str
    host_id: str | None
    version: int
    script_text: str
    boosted_text: str | None = None  # 爆品改造后最终稿 (2026-08-11), 无则为 None
    project_dir: str | None
    video_format: str = "portrait"
    perspective_2: str | None = None
    deconstruct_json: dict | None = None  # 解构层产物 (reactions/narrative/research) (2026-08-12)
    material_package_id: str | None = None  # 洗稿挂的素材包回溯 (2026-08-15)
    prompt_template: str | None = None  # 音频页 persona 音色锁依赖 (2026-08-15)
    status: str
    created_at: datetime
    updated_at: datetime
    segments: list[SegmentOut]
    article: ArticleBriefOut | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _derive_title(self) -> Self:
        if self.article is not None:
            self.title = self.article.title
        return self


class ScriptUpdate(BaseModel):
    script_text: str | None = Field(default=None, min_length=1)
    boosted_text: str | None = Field(default=None, min_length=1)  # 爆品改造最终稿编辑 (2026-08-11)
    status: str | None = None
    project_dir: str | None = None
    video_format: str | None = None


class SegmentUpdate(BaseModel):
    text: str | None = None
    selected_for_host: bool | None = None
    host_order: int | None = None
    segment_type: str | None = None


class AudioJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    script_id: str
    voice_id: str | None
    output_dir: str
    status: str
    total_segments: int
    completed_segments: int
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class AudioFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    segment_id: str | None
    filename: str
    file_path: str
    duration: float | None
    sample_rate: int | None
    # 0912 逐字稿接线: 音频行携带对应段文本 + 回听嫌疑标记 (人耳复核要有参照物)
    text: str | None = None
    verify_flag: str | None = None


class HostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    persona_key: str
    default_voice_id: str | None
    brand_name: str | None = None
    stamp_name: str | None = None
    brand_tag: str | None = None


class HostCreate(BaseModel):
    """创建数字人账号 (Host). brand 字段可留空, 留空时流水线回退 name/前 2 字."""

    name: str = Field(..., min_length=1, max_length=128)
    persona_key: str = Field(..., min_length=1, max_length=64)
    fixed_opening: str | None = None
    fixed_ending: str | None = None
    brand_name: str | None = Field(default=None, max_length=128)
    stamp_name: str | None = Field(default=None, max_length=16)
    brand_tag: str | None = Field(default=None, max_length=64)
    default_voice_id: str | None = None


class HostUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    persona_key: str | None = Field(default=None, max_length=64)
    fixed_opening: str | None = None
    fixed_ending: str | None = None
    brand_name: str | None = Field(default=None, max_length=128)
    stamp_name: str | None = Field(default=None, max_length=16)
    brand_tag: str | None = Field(default=None, max_length=64)
    default_voice_id: str | None = None
