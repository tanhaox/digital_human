# -*- coding: utf-8 -*-
"""内容模型 — 文章/脚本/段落/音频任务/音频文件/爬虫任务.

核心流水线: Article → Script → Segment → (AudioJob → AudioFile)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _new_uuid, _now

__all__ = [
    "Host",
    "Voice",
    "Article",
    "Script",
    "Segment",
    "AudioJob",
    "AudioFile",
    "MaterialPackage",
    "MaterialItem",
    "CrawlTask",
]


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    persona_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    fixed_opening: Mapped[str | None] = mapped_column(Text, default=None)
    fixed_ending: Mapped[str | None] = mapped_column(Text, default=None)
    reference_image: Mapped[str | None] = mapped_column(String(512), default=None)
    default_voice_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voices.id"), default=None
    )
    # 品牌/账号字段 (2026-08-08): 账号信息挂到 Host, 洗稿选 host 后随 script.host_id
    # 走完整条流水线 (H 线 HF 上屏 / C 线出镜). 留空时流水线回退 host.name / 前 2 字.
    brand_name: Mapped[str | None] = mapped_column(String(128), default=None)
    stamp_name: Mapped[str | None] = mapped_column(String(16), default=None)
    brand_tag: Mapped[str | None] = mapped_column(String(64), default=None)
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    voices: Mapped[list["Voice"]] = relationship(
        "Voice",
        back_populates="host",
        foreign_keys="Voice.host_id",
        lazy="selectin",
    )
    default_voice: Mapped["Voice | None"] = relationship(
        "Voice",
        foreign_keys=[default_voice_id],
        lazy="selectin",
    )


class Voice(Base):
    __tablename__ = "voices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), default=None)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    backend: Mapped[str] = mapped_column(String(32), default="fish")  # fish / f5 / elevenlabs
    reference_audio_path: Mapped[str | None] = mapped_column(String(512), default=None)
    reference_text: Mapped[str | None] = mapped_column(Text, default=None)
    base_url_fish: Mapped[str | None] = mapped_column(String(256), default=None)
    base_url_f5: Mapped[str | None] = mapped_column(String(256), default=None)
    master_audio_path: Mapped[str | None] = mapped_column(String(512), default=None)
    master_text: Mapped[str | None] = mapped_column(Text, default=None)
    base_url_indextts: Mapped[str | None] = mapped_column(String(256), default=None)
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    host: Mapped["Host | None"] = relationship(
        "Host",
        back_populates="voices",
        foreign_keys=[host_id],
    )


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    title: Mapped[str | None] = mapped_column(String(512), default=None)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 赛道 (2026-08-16 用户方案: 建稿时勾选, 驱动评论层/七层审计走对应分支)
    # tech=科技/商业(默认, 七层科技版+财经观众) / geo=地缘/国际(七层地质版+地缘观众)
    track: Mapped[str] = mapped_column(String(16), default="tech")
    perspective_1: Mapped[str | None] = mapped_column(Text, default=None)  # 洗稿前的补充观点
    # 评论层 (2026-08-15): 解构层产物 (reactions/comment_archetypes/narrative/research),
    # 洗稿时自动生成, 也可单独触发; 新闻线索页「评论层」面板展示, 供人工洞察
    deconstruct_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # 证据图管线① (2026-09-04): 抓取 URL 时「搜图」开关产物 [{url,alt,w,h}],
    # 非空 = 总闸开过, 素材包建包时自动扫图
    images_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending / rewritten / generated / failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    scripts: Mapped[list["Script"]] = relationship(
        "Script",
        back_populates="article",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    crawl_task: Mapped["CrawlTask | None"] = relationship(
        "CrawlTask",
        back_populates="article",
        uselist=False,
    )


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    article_id: Mapped[str] = mapped_column(String(36), ForeignKey("articles.id"), nullable=False)
    host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), default=None)
    version: Mapped[int] = mapped_column(Integer, default=1)
    prompt_template: Mapped[str | None] = mapped_column(String(128), default=None)
    script_text: Mapped[str] = mapped_column(Text, nullable=False)
    project_dir: Mapped[str | None] = mapped_column(String(1024), default=None)
    video_format: Mapped[str] = mapped_column(String(16), default="portrait")  # portrait / landscape / square
    perspective_2: Mapped[str | None] = mapped_column(Text, default=None)  # 洗稿后的修正观点
    # 爆品改造 (2026-08-10): 洗稿后自动跑 P1开场→P2预埋→P3节奏.
    # boosted_text = 改造后全文 (保留 script_text 为洗稿原稿, 供对比回滚);
    # boost_titles = P1 输出的标题候选 JSON, 前端看稿页展示供人工选用 (默认用第一个).
    boosted_text: Mapped[str | None] = mapped_column(Text, default=None)
    boost_titles: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    # 解构层产物 (2026-08-12): DECONSTRUCT 产出的用户反应清单
    # (reactions/narrative/research), 洗稿时落库, 爆品改造 P1/P2 读取注入
    # → P1 钩子戳观众痛点、P2 争议对准焦虑。
    deconstruct_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    # 情绪标注 (2026-08-13): P5 产出 — 段落级 [情绪/强度] 标注, 供 TTS 情绪合成 +
    # 导演配画面 + 画面搜索 消费. 结构: [{"text": "段落文字", "emotion": "calm|serious|angry|surprised|happy", "strength": "弱|中|强"}, ...]
    emotion_annotations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=list)
    # 素材聚合 (2026-08-15): 洗稿时挂的素材包, 回溯脚本用了哪些多源素材
    material_package_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("material_packages.id"), default=None
    )
    status: Mapped[str] = mapped_column(
        String(32), default="drafting"
    )  # drafting / approved / generated
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    article: Mapped["Article"] = relationship("Article", back_populates="scripts")
    host: Mapped["Host | None"] = relationship("Host", lazy="selectin")
    segments: Mapped[list["Segment"]] = relationship(
        "Segment",
        back_populates="script",
        cascade="all, delete-orphan",
        order_by="Segment.line_index",
        lazy="selectin",
    )
    audio_jobs: Mapped[list["AudioJob"]] = relationship(
        "AudioJob",
        back_populates="script",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    script_id: Mapped[str] = mapped_column(String(36), ForeignKey("scripts.id"), nullable=False)
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    control_chars: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    segment_type: Mapped[str | None] = mapped_column(
        String(32), default="body"
    )  # opening / hook / body / cta / ending
    selected_for_host: Mapped[bool] = mapped_column(Boolean, default=True)
    host_order: Mapped[int] = mapped_column(Integer, default=0)
    estimated_duration: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    script: Mapped["Script"] = relationship("Script", back_populates="segments")
    audio_files: Mapped[list["AudioFile"]] = relationship(
        "AudioFile",
        back_populates="segment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AudioJob(Base):
    __tablename__ = "audio_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    script_id: Mapped[str] = mapped_column(String(36), ForeignKey("scripts.id"), nullable=False)
    voice_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("voices.id"), default=None)
    output_dir: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending / running / cancelling / cancelled / completed / failed / stale
    total_segments: Mapped[int] = mapped_column(Integer, default=0)
    completed_segments: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    tts_cache_key: Mapped[str | None] = mapped_column(String(64), default=None, index=True)  # TTS缓存: 音色+台词哈希 (2026-08-21)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    script: Mapped["Script"] = relationship("Script", back_populates="audio_jobs")
    voice: Mapped["Voice | None"] = relationship("Voice", lazy="selectin")
    audio_files: Mapped[list["AudioFile"]] = relationship(
        "AudioFile",
        back_populates="audio_job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AudioFile(Base):
    __tablename__ = "audio_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    audio_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("audio_jobs.id"), nullable=False)
    segment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("segments.id"), default=None)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration: Mapped[float | None] = mapped_column(Float, default=None)
    sample_rate: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    audio_job: Mapped["AudioJob"] = relationship("AudioJob", back_populates="audio_files")
    segment: Mapped["Segment | None"] = relationship("Segment", back_populates="audio_files")


class MaterialPackage(Base):
    """素材包 (2026-08-15): 七层洗稿的多源素材聚合容器.

    一篇文章一个包, 洗稿时整包按层注入上下文, 解决 7 层模板单篇原文
    喂不饱 L3~L6 (背景/参数/实测/商业) 的字数塌陷问题.
    """

    __tablename__ = "material_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    article_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("articles.id"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(512), default=None)
    # collecting (抓取/待审计) / audited (审计完成) / failed
    status: Mapped[str] = mapped_column(String(32), default="collecting")
    # 七层覆盖审计产物: {layers: {Lx: {covered, evidence, gaps, search_queries}},
    #                   item_tags: {"1": ["L3","L4"]}, summary: str}
    audit_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    search_rounds: Mapped[int] = mapped_column(Integer, default=0)  # 补搜轮次
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    items: Mapped[list["MaterialItem"]] = relationship(
        "MaterialItem",
        back_populates="package",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MaterialItem(Base):
    """素材条目: url 抓取 / manual 手动粘贴 / search 智谱补搜."""

    __tablename__ = "material_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    package_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("material_packages.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(16), default="url")  # url / manual / search
    title: Mapped[str | None] = mapped_column(String(512), default=None)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    media: Mapped[str | None] = mapped_column(String(128), default=None)  # 来源媒体名(搜索结果)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fetch_ok: Mapped[bool] = mapped_column(Boolean, default=True)  # url 抓取失败标记
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    search_query: Mapped[str | None] = mapped_column(String(256), default=None)  # 补搜来源查询词
    layer_tags: Mapped[list[str] | None] = mapped_column(JSON, default=None)  # 审计标注 ["L3","L4"]
    # 证据图管线① (2026-09-04): 扫图产物 [{url, alt, w, h, local_path,
    # source_media, vlm:{is_chart,kind,desc_zh,numbers,quality,watermark}|null}]
    images_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    package: Mapped["MaterialPackage"] = relationship("MaterialPackage", back_populates="items")


class CrawlTask(Base):
    __tablename__ = "crawl_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    source_type: Mapped[str] = mapped_column(String(32), default="url")  # rss / url / api
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending / fetched / failed
    article_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("articles.id"), default=None)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    article: Mapped["Article | None"] = relationship("Article", back_populates="crawl_task")
