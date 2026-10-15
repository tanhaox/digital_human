"""Launch script for digital human pipeline web UI."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

# Ensure project root is on path for local imports.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn

from app.config import load_config

logger = logging.getLogger(__name__)

# Known local ffmpeg install used by this Windows machine.
_FFMPEG_BIN = Path(r"C:\Programs\ffmpeg\bin")


def _ensure_ffmpeg_on_path() -> None:
    """If ffmpeg/ffprobe are not on PATH, prepend the known install dir.

    This makes run_web.py resilient to stale/cached user PATH after Windows
    reinstalls or terminal sessions started before PATH updates.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    if _FFMPEG_BIN.exists():
        path = os.environ.get("Path", os.environ.get("PATH", ""))
        parts = [p for p in path.split(os.pathsep) if p]
        bin_str = str(_FFMPEG_BIN)
        if bin_str not in parts:
            parts.insert(0, bin_str)
            joined = os.pathsep.join(parts)
            os.environ["Path"] = joined
            os.environ["PATH"] = joined
            logger.info("Prepended fallback ffmpeg directory: %s", bin_str)


def main() -> int:
    _ensure_ffmpeg_on_path()

    config_path = PROJECT_ROOT / "config" / "app.yaml"
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        print("Copy config/app.example.yaml to config/app.yaml and fill in your DeepSeek key.", file=sys.stderr)
        return 1

    cfg = load_config(config_path)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    logger.info("run_web startup: ffmpeg=%s ffprobe=%s", ffmpeg, ffprobe)
    uvicorn.run("app.main:app", host=cfg.app.host, port=cfg.app.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
