"""TTS 库: scripts/tts_client.py 拆包后的公共 API 聚合层.

用法: `from tts_lib import synthesize, synthesize_lines, tts_client 引擎等`.
本模块只做符号聚合与转发, 不含业务逻辑 — 各子模块职责单一。
"""
from __future__ import annotations

from .audio import (
    _concat_wavs_with_ffmpeg,
    _ensure_dir,
    _split_wav_by_silence,
    _write_wav,
    apply_ffmpeg_params,
)
from .cli import main
from .constants import (
    DEFAULT_INDEXTTS_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_F5_URL,
    DEFAULT_FISH_URL,
)
from .engines import f5_tts, fish_speech_tts, indextts_tts
from .lines import synthesize_lines
from .orchestrator import Backend, _synthesize_single, synthesize
from .text import _split_text

__all__ = [
    # public synthesis API
    "synthesize",
    "synthesize_lines",
    "_synthesize_single",
    # engines
    "fish_speech_tts",
    "f5_tts",
    "indextts_tts",
    # CLI
    "main",
    # constants
    "DEFAULT_FISH_URL",
    "DEFAULT_F5_URL",
    "DEFAULT_INDEXTTS_URL",
    "DEFAULT_OUTPUT_DIR",
    # helpers (kept for shim backward-compat)
    "_concat_wavs_with_ffmpeg",
    "_ensure_dir",
    "_split_wav_by_silence",
    "_write_wav",
    "_split_text",
    "apply_ffmpeg_params",
    "Backend",
]
