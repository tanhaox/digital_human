# -*- coding: utf-8 -*-
"""拆书项目模型 (2026-08-19) — BookProject(书+商品信息) / Episode(6集状态机).

方案: docs/design/拆书项目-实施方案.md。小黄车挂车动作在抖音侧人工, 系统仅记录+提醒。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _new_uuid, _now

__all__ = ["BookProject", "Episode"]


class BookProject(Base):
    """拆书项目: 一本书 = 一个 6 集系列.

    状态机: created → input_review → roadmap_review → writing → done / failed
    """

    __tablename__ = "book_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    book_title: Mapped[str] = mapped_column(String(256), nullable=False)
    author: Mapped[str | None] = mapped_column(String(256), default=None)
    # 作者简介 (2026-09-05 用户令: 尾页卡不要出版社/ISBN, 要简介; 豆瓣元数据补)
    author_bio: Mapped[str | None] = mapped_column(String(1024), default=None)
    publisher: Mapped[str | None] = mapped_column(String(256), default=None)
    isbn: Mapped[str | None] = mapped_column(String(64), default=None)
    # 小黄车: 商品链接/封面(外部AI设计)/卖点一句话 — 发布时生成挂车清单用
    cart_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    cover_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    selling_point: Mapped[str | None] = mapped_column(String(512), default=None)
    # 知海书页链接 (2026-08-19): 爬元数据/简介/目录当 L1, 免搜索; 下载人工
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    book_type: Mapped[str | None] = mapped_column(String(64), default=None)
    core_claim: Mapped[str | None] = mapped_column(String(1024), default=None)
    # 补全后全量输入 + 来源档级标签 (L0/L1/L2, 见方案 §3.1 防幻觉)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    source_path: Mapped[str | None] = mapped_column(String(1024), default=None)  # L0 精华/原书 (txt/epub)
    status: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    episodes: Mapped[list["Episode"]] = relationship(
        "Episode",
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Episode.ep_index",
        lazy="selectin",
    )


class Episode(Base):
    """拆书单集 (1-6).

    状态机: pending → generating → draft → confirmed; 级联重跑回 pending。
    """

    __tablename__ = "book_episodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    book_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("book_projects.id"), nullable=False
    )
    ep_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), default=None)
    # 路线图行: 主题/对应书中内容/核心任务/承上/启下
    roadmap_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    script_text: Mapped[str | None] = mapped_column(Text, default=None)
    coverage_json: Mapped[list[Any] | None] = mapped_column(JSON, default=list)  # 知识点覆盖清单
    # 书摘金句 3 句 (2026-09-05 B-Q1 卡): [{text, key}×3] — L0 原句池逐字选取,
    # 全书 6 集不重复, 句长 ≥12 字; 由创作层 episode_gen 选取落库
    quotes_json: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    target_duration_sec: Mapped[float] = mapped_column(Float, default=600.0)  # 弹性标签基准
    # 进产线桥接 (2026-08-19): 确认后生成 Script+segments, 复用 TTS/导演/合成/JY 全链
    script_id: Mapped[str | None] = mapped_column(String(36), default=None)
    director_job_id: Mapped[str | None] = mapped_column(String(36), default=None)
    # bs1 页单快照 (2026-09-08 老谭读书产线): 确认后的页单 (含字段/配图/时长),
    # [{type, narration, fields, img_query, img_cap, img_file, img_pexels_id, png, est_sec}]
    bs1_pages_json: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    # 六拍模块表 (0917 模块总线): [{idx, name, tail}] — 结构真相源, 正文标签只是
    # 人读视图; 生成/修稿时由 module_map.parse_modules 落库。TTS 模块墙/切场/分镜
    # 全取此表, 标记在正文中存活与否不影响下游。
    module_json: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # 成稿时间 (2026-09-12): 当前稿落定时刻 (生成/修稿都刷新) — 测试期各集版本不同靠它分辨;
    # updated_at 会被确认/进产线等任何写碰脏, 不能当稿龄用
    script_generated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    book: Mapped["BookProject"] = relationship("BookProject", back_populates="episodes")
