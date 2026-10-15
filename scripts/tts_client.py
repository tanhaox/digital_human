"""Deprecated shim for scripts/tts_client.py.

实现已迁移到 scripts/tts_lib/ (见 docs/improvements/ 诊断报告)。本文件仅为
向后兼容转发: 同时支持 `import tts_client` (conftest 注入 scripts/ 目录) 与
`from scripts import tts_client` (uvicorn 包模式)。原实现备份于同目录
tts_client.py.bak。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from tts_lib import (  # noqa: E402
    DEFAULT_INDEXTTS_URL,
    DEFAULT_F5_URL,
    DEFAULT_FISH_URL,
    DEFAULT_OUTPUT_DIR,
    Backend,
    _concat_wavs_with_ffmpeg,
    _ensure_dir,
    _split_text,
    _split_wav_by_silence,
    _synthesize_single,
    _write_wav,
    apply_ffmpeg_params,
    f5_tts,
    fish_speech_tts,
    indextts_tts,
    main,
    synthesize,
    synthesize_lines,
)

__all__ = [
    "synthesize",
    "synthesize_lines",
    "_synthesize_single",
    "fish_speech_tts",
    "f5_tts",
    "indextts_tts",
    "main",
    "DEFAULT_FISH_URL",
    "DEFAULT_F5_URL",
    "DEFAULT_INDEXTTS_URL",
    "DEFAULT_OUTPUT_DIR",
    "_concat_wavs_with_ffmpeg",
    "_ensure_dir",
    "_split_wav_by_silence",
    "_write_wav",
    "_split_text",
    "apply_ffmpeg_params",
    "Backend",
]


if __name__ == "__main__":
    raise SystemExit(main())
