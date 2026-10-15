"""Shared constants for the TTS client library.

URL 常量与输出目录保持唯一来源 (Single Source of Truth), 避免在
多模块间散落魔法数字。DEFAULT_INDEXTTS_URL 与 DEFAULT_OUTPUT_DIR 被
tests/test_tts_client.py 硬性钉死, 变更必须同步修改测试。
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs" / "audio"

DEFAULT_FISH_URL = "http://127.0.0.1:7860"
DEFAULT_F5_URL = "http://127.0.0.1:7861"
DEFAULT_INDEXTTS_URL = "http://127.0.0.1:7862"

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_FISH_URL",
    "DEFAULT_F5_URL",
    "DEFAULT_INDEXTTS_URL",
]
