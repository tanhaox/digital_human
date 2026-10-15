"""Slot workflow 公共辅助: 槽位目录 / 音频路径 / HF 模板选择.

供 slot_workflows 包内各模块复用, 不依赖具体 workflow 逻辑。
"""
from __future__ import annotations

from pathlib import Path

from app.config import get_config
from app.models import DirectorJob, DirectorSlot
from app.schemas import get_video_format_spec

# HF 视觉渲染默认模板（财经杂志感竖屏）
HF_TEMPLATE_ID = "news-magazine-v1"
# 横屏模板: 由 _pick_hf_template 按 video_format 选择
HF_TEMPLATE_ID_LS = "news-magazine-v1-ls"

__all__ = [
    "HF_TEMPLATE_ID",
    "HF_TEMPLATE_ID_LS",
    "slot_root",
    "ensure_slot_dir",
    "audio_slice_path",
    "_pick_hf_template",
]


def _pick_hf_template(job: DirectorJob) -> str:
    """按 job.video_format 选择 HF 模板 (横屏→横屏模板, 其余→竖屏模板)。

    修复 2026-08-07 H 线 bug: 此前 execute_hf_visual_slot 硬编码竖屏模板,
    导致选择 landscape 时所有 HF 视频仍产出 1080×1920 竖屏。
    """
    spec = get_video_format_spec(job.video_format)
    if spec["width"] > spec["height"]:
        return HF_TEMPLATE_ID_LS
    return HF_TEMPLATE_ID


def slot_root(slot: DirectorSlot) -> Path:
    cfg = get_config().defaults
    return Path(cfg.composition_output_root) / slot.director_job_id / "slots" / str(slot.id)


def ensure_slot_dir(slot: DirectorSlot) -> Path:
    root = slot_root(slot)
    root.mkdir(parents=True, exist_ok=True)
    return root


def audio_slice_path(job: DirectorJob) -> Path | None:
    if job.audio_file is None:
        return None
    return Path(job.audio_file.file_path) if job.audio_file.file_path else None
