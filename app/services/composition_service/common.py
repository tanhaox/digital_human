"""Composition service — 通用纯工具（时间戳 / 路径 / 清理 / ffmpeg 薄包装）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_config
from app.infrastructure import run_ffmpeg
from app.models import DirectorJob

logger = logging.getLogger(__name__)

__all__ = [
    "_now",
    "_job_root",
    "_ensure_root",
    "_cleanup_intermediates",
    "_run_ffmpeg",
    "_HOST_WF",
    "_WF_TIER",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_root(job: DirectorJob) -> Path:
    cfg = get_config().defaults
    return Path(cfg.composition_output_root) / job.id


def _ensure_root(job: DirectorJob) -> Path:
    root = _job_root(job)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cleanup_intermediates(root: Path, keep_intermediates: bool = False) -> int:
    """Delete intermediate build artifacts, keep only final output + manifest.

    Intermediate files:
        normalized_*.mp4  — re-encoded slot clips
        concat_list.txt   — concat demuxer file list
        concat_raw.mp4    — raw concatenated video
        with_audio.mp4    — mixed audio before loudnorm
        normalized.mp4    — loudnorm output (copy source for final)
        silence_fallback.wav — generated silence when master TTS missing

    Retained:
        director_*.mp4   — final output
        composition_manifest.json — metadata

    Returns bytes freed (approximate, 0 if keep_intermediates).
    """
    if keep_intermediates:
        return 0

    patterns = [
        "normalized_*.mp4",   # 逐 clip 时长归一化产物 (2026-08-08 音画同步修复)
        "concat_list.txt",
        "concat_raw.mp4",
        "with_audio.mp4",
        "normalized.mp4",
        "silence_fallback.wav",
    ]
    freed = 0
    for pat in patterns:
        for f in sorted(root.glob(pat)):
            try:
                st = f.stat()
                f.unlink()
                freed += st.st_size
                logger.debug("[cleanup] deleted %s (%d bytes)", f.name, st.st_size)
            except OSError as exc:
                logger.warning("[cleanup] failed to delete %s: %s", f.name, exc)
    if freed:
        logger.info("[cleanup] freed %.1f MB from %s", freed / (1024 * 1024), root.name)
    return freed


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run ffmpeg, raising RuntimeError on failure (合成链路耗时, timeout 600s).

    实现统一在 app.infrastructure.ffmpeg.run_ffmpeg。
    """
    run_ffmpeg(cmd, timeout=600)


# ---------------------------------------------------------------------------
# Slot workflow quality tiers (dedupe) — host/mixed 优先, broll/hf 次之,
# black_placeholder 兜底。未知 workflow 按 5 处理。
# ---------------------------------------------------------------------------

_WF_TIER = {
    "host": 0, "mixed_host_broll": 0,
    "broll_pexels": 1, "broll_local": 1,
    "hf_chart": 1, "hf_title": 1,
    "evidence_image": 1,  # 证据图与 broll 同档 (2026-09-04); 缺位=未知 5, 会输给任何已知 workflow
    "black_placeholder": 9,
}

# Host workflows render their own synced audio (ComfyUI) — in the segment
# timeline these get silence instead of TTS audio.
_HOST_WF = {"host", "mixed_host_broll"}
