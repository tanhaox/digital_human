# -*- coding: utf-8 -*-
"""音色 (Voice) 相关 schemas 与合成参数."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "VoiceOut",
    "VoiceCreate",
    "VoiceUpdate",
    "VoiceParams",
    "VoiceTestRequest",
    "VoiceCarnivalRequest",
    "VoiceSetReferenceRequest",
]


class VoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    host_id: str | None
    name: str
    backend: str
    reference_audio_path: str | None
    reference_text: str | None
    base_url_fish: str | None
    base_url_f5: str | None
    master_audio_path: str | None
    master_text: str | None
    base_url_indextts: str | None
    config_json: dict[str, Any] | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def params(self) -> dict[str, Any] | None:
        if self.config_json and isinstance(self.config_json, dict):
            return self.config_json.get("params")
        return None


class VoiceParams(BaseModel):
    """Voice synthesis parameters: FFmpeg effects + TTS engine overrides."""

    # FFmpeg post-processing (all backends)
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="语速 (0.5–2.0)")
    pitch: float = Field(default=0, ge=-12, le=12, description="语调/音高偏移 (半音, -12–+12, 步进0.5)")
    volume: float = Field(default=1.0, ge=0.0, le=2.0, description="音量增益 (0.0–2.0)")

    # Timbre / EQ shaping (all backends) — 真正的音色维度
    bass_gain: float = Field(default=0, ge=-24, le=24, description="低频厚度 (dB, -24–+24)")
    presence_gain: float = Field(default=0, ge=-24, le=24, description="临场感/中频 (dB, -24–+24)")
    air_gain: float = Field(default=0, ge=-24, le=24, description="高频通透 (dB, -24–+24)")

    # Fish Speech engine parameters (fish backend only)
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, description="TTS 温度 (0.1–1.5)")
    top_p: float | None = Field(default=None, ge=0.1, le=1.0, description="TTS top_p (0.1–1.0)")
    repetition_penalty: float | None = Field(
        default=None, ge=1.0, le=2.0, description="TTS 重复惩罚 (1.0–2.0)"
    )
    seed: int | None = Field(
        default=None, ge=0, le=2147483647, description="TTS 随机种子 (None=随机, 固定数字=确定输出)"
    )

    # Quick preset (auto-fills the above fields when set)
    preset: str | None = Field(
        default=None,
        pattern=r"^(default|male|female|deep_male|loli|fast)$",
        description="快捷预设: default/male/female/deep_male/loli/fast",
    )

    # IndexTTS2 engine parameters (indextts backend only)
    master_style: str | None = Field(
        default=None,
        pattern=r"^(calm|excited|relaxed)$",
        description="IndexTTS2 主音色风格 (calm/excited/relaxed)",
    )
    do_sample: bool | None = Field(default=None, description="IndexTTS2 是否采样 (None=服务器默认 True)")
    top_p: float | None = Field(default=None, ge=0.1, le=1.0, description="IndexTTS2 top_p")
    top_k: int | None = Field(default=None, ge=1, le=100, description="IndexTTS2 top_k")
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, description="IndexTTS2 温度")
    max_text_tokens_per_segment: int | None = Field(
        default=None, ge=20, le=600, description="IndexTTS2 单段最大 token 数"
    )


class VoiceCreate(BaseModel):
    host_id: str | None = None
    name: str = Field(..., min_length=1, max_length=128)
    backend: str = Field(default="fish", pattern=r"^(fish|f5|elevenlabs|indextts)$")
    reference_audio_path: str | None = None
    reference_text: str | None = None
    base_url_fish: str | None = None
    base_url_f5: str | None = None
    master_audio_path: str | None = None
    master_text: str | None = None
    base_url_indextts: str | None = None
    config_json: dict[str, Any] | None = None
    params: VoiceParams | None = None


class VoiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    backend: str | None = Field(default=None, pattern=r"^(fish|f5|elevenlabs|indextts)$")
    reference_audio_path: str | None = None
    reference_text: str | None = None
    base_url_fish: str | None = None
    base_url_f5: str | None = None
    master_audio_path: str | None = None
    master_text: str | None = None
    base_url_indextts: str | None = None
    config_json: dict[str, Any] | None = None
    params: VoiceParams | None = None


class VoiceTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    params: VoiceParams | None = None


class VoiceCarnivalRequest(BaseModel):
    """疯狂抽卡: 生成 N 个版本供用户挑选"""

    text: str = Field(..., min_length=1, max_length=500)
    params: VoiceParams | None = None
    count: int = Field(default=5, ge=3, le=20, description="生成个数 (3-20)")


class VoiceSetReferenceRequest(BaseModel):
    """将某段音频及其文本设为音色的固定参考音锚点"""

    audio_path: str = Field(
        ..., min_length=1, max_length=1024, description="选定音频的文件路径 (绝对路径)"
    )
    audio_text: str = Field(
        ..., min_length=1, max_length=500, description="该音频对应的准确文本"
    )
