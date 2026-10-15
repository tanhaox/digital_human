# -*- coding: utf-8 -*-
"""Pydantic schemas for API requests/responses (包化拆分).

原单文件 ``app/schemas.py`` 按领域拆分为本包子模块,此处汇总公共 API,
下游 ``from app.schemas import ...`` 与 ``from ..schemas import ...`` 保持兼容。
"""
from __future__ import annotations

from .articles import (
    ArticleBriefOut,
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    AudioFileOut,
    AudioJobOut,
    CorrectRequest,
    FetchUrlRequest,
    FetchUrlResponse,
    HostCreate,
    HostOut,
    HostUpdate,
    RewriteRequest,
    ScriptOut,
    ScriptUpdate,
    TtsAdaptRequest,
    SegmentOut,
    SegmentUpdate,
)
from .assets import (
    ImportFolderRequest,
    MaterialAssetOut,
    PexelsImageSearchRequest,
    PexelsImportItem,
    PexelsImportRequest,
    PexelsOnlineSearchRequest,
    PersonaCreate,
    PersonaOut,
    PersonaUpdate,
    ResolveItem,
    ResolveRequest,
    ResolveResponse,
    TaggingProgressOut,
    TaggingRunRequest,
    VIDEO_LOCATION_CHOICES,
    VIDEO_ORIENTATION_CHOICES,
    VIDEO_PEOPLE_CHOICES,
    VIDEO_PREFERENCE_CHOICES,
    VIDEO_SCENE_CHOICES,
    VIDEO_SHOT_TYPE_CHOICES,
    VIDEO_SOURCE_TYPE_CHOICES,
    VideoAssetOut,
    VideoAssetPreferenceRequest,
    VideoAssetUpdate,
)
from .digital_human_video import (
    DigitalHumanVideoCreate,
    DigitalHumanVideoOut,
    EligibleAudioItem,
    EligibleAudioResponse,
    GenerateVideoResponse,
    StoryboardItem,
    StoryboardUploadResponse,
)
from .director import (
    ComposeResponse,
    DirectorDirectResponse,
    DirectorJobCreate,
    DirectorJobOut,
    DirectorPlan,
    DirectorSlotOut,
    DirectorSlotPlan,
    RetrySlotResponse,
)
from .format import VIDEO_FORMAT_SPECS, get_video_format_spec
from .materials import (
    MaterialItemAddRequest,
    MaterialItemOut,
    MaterialPackageBriefOut,
    MaterialPackageOut,
    PackageCreateRequest,
    SupplementSearchRequest,
)
from .roles import (
    ApplyRoleResponse,
    ComfyUISubmitRequest,
    ComfyUISubmitResponse,
    ComfyUIWorkflowInfo,
    GenerateViewsRequest,
    GenerateViewsResponse,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    WorkflowSyncOut,
)
from .visual_render import (
    GenerateVisualResponse,
    TemplateInfo,
    VisualRenderJobCreate,
    VisualRenderJobOut,
)
from .voices import (
    VoiceCarnivalRequest,
    VoiceCreate,
    VoiceOut,
    VoiceParams,
    VoiceSetReferenceRequest,
    VoiceTestRequest,
    VoiceUpdate,
)

__all__ = [
    # articles
    "ArticleCreate", "RewriteRequest", "CorrectRequest", "TtsAdaptRequest", "FetchUrlRequest",
    "FetchUrlResponse", "ArticleUpdate", "ArticleOut", "SegmentOut",
    "ArticleBriefOut", "ScriptOut", "ScriptUpdate", "SegmentUpdate",
    "AudioJobOut", "AudioFileOut", "HostOut", "HostCreate", "HostUpdate",
    # voices
    "VoiceOut", "VoiceCreate", "VoiceUpdate", "VoiceParams", "VoiceTestRequest",
    "VoiceCarnivalRequest", "VoiceSetReferenceRequest",
    # roles / comfyui
    "RoleOut", "RoleCreate", "RoleUpdate", "GenerateViewsRequest",
    "GenerateViewsResponse", "ApplyRoleResponse", "WorkflowSyncOut",
    "ComfyUIWorkflowInfo", "ComfyUISubmitRequest", "ComfyUISubmitResponse",
    # digital human video
    "StoryboardItem", "DigitalHumanVideoCreate", "DigitalHumanVideoOut",
    "EligibleAudioItem", "EligibleAudioResponse", "StoryboardUploadResponse",
    "GenerateVideoResponse",
    # director
    "DirectorSlotPlan", "DirectorPlan", "DirectorJobCreate", "DirectorSlotOut",
    "DirectorJobOut", "DirectorDirectResponse", "RetrySlotResponse",
    "ComposeResponse",
    # visual render
    "VisualRenderJobCreate", "VisualRenderJobOut", "TemplateInfo",
    "GenerateVisualResponse",
    # assets
    "VIDEO_ORIENTATION_CHOICES", "VIDEO_SOURCE_TYPE_CHOICES",
    "VIDEO_LOCATION_CHOICES", "VIDEO_PEOPLE_CHOICES", "VIDEO_PREFERENCE_CHOICES",
    "VIDEO_SCENE_CHOICES", "VIDEO_SHOT_TYPE_CHOICES",
    "VideoAssetOut", "VideoAssetUpdate", "VideoAssetPreferenceRequest",
    "TaggingRunRequest", "TaggingProgressOut",
    "ImportFolderRequest",
    "PexelsImportItem", "PexelsImportRequest", "PexelsOnlineSearchRequest",
    "PexelsImageSearchRequest",
    "ResolveItem", "ResolveResponse", "ResolveRequest", "MaterialAssetOut",
    "PersonaCreate", "PersonaUpdate", "PersonaOut",
    # format
    "VIDEO_FORMAT_SPECS", "get_video_format_spec",
    # materials (素材聚合)
    "MaterialItemOut", "MaterialPackageBriefOut", "MaterialPackageOut",
    "PackageCreateRequest", "MaterialItemAddRequest", "SupplementSearchRequest",
]
