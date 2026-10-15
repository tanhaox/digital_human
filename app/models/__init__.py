# -*- coding: utf-8 -*-
"""SQLAlchemy 模型聚合入口(包化拆分).

原单文件 ``app/models.py`` 按领域拆分为子模块,此处汇总公共 API,
下游 ``from app.models import ...`` 与 ``from ..models import ...`` 保持兼容。
"""
from __future__ import annotations

from .assets import DownloadLog, ImageExtraction, MaterialAsset, VideoAsset, VideoOutput
from .base import Base, _new_uuid, _now
from .book import BookProject, Episode
from .material import MaterialIngestJob
from .content import (
    Article,
    AudioFile,
    AudioJob,
    CrawlTask,
    Host,
    MaterialItem,
    MaterialPackage,
    Script,
    Segment,
    Voice,
)
from .director import DirectorJob, DirectorSlot, VisualRenderJob
from .roles import DigitalHumanVideo, Persona, Role, WorkflowSync

__all__ = [
    # base / 基础设施
    "Base", "_now", "_new_uuid",
    # 内容流水线
    "Host", "Voice", "Article", "Script", "Segment",
    "AudioJob", "AudioFile", "CrawlTask",
    "MaterialPackage", "MaterialItem",
    # 角色 / 数字人
    "Role", "WorkflowSync", "DigitalHumanVideo", "Persona",
    # 拆书项目
    "BookProject", "Episode",
    # 导演台
    "DirectorJob", "DirectorSlot", "VisualRenderJob",
    # 素材 / 成品
    "MaterialAsset", "DownloadLog", "VideoAsset", "VideoOutput",
    # 素材摄入产线 (2026-08-30)
    "MaterialIngestJob",
    # 图片反推提取库 (2026-09-06)
    "ImageExtraction",
]
