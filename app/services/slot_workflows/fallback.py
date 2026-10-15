"""Black placeholder 兜底线: 通用素材 fallback (非纯黑帧).

对应 _EXECUTION_PHASES 的 L 线 (black_placeholder)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_config
from app.infrastructure import render_scale_pad
from app.models import DirectorSlot
from app.schemas import get_video_format_spec
from app.services.slot_workflows.common import ensure_slot_dir

logger = logging.getLogger(__name__)

__all__ = [
    "execute_black_placeholder_slot",
    "_pick_fallback_material",
]


def _pick_fallback_material(db: Session, slot: DirectorSlot) -> Path:
    """Pick a generic b-roll clip from materials_dir as the universal fallback.

    We intentionally avoid pure black frames. The chosen clip is treated as
    "small error covering big error": visually harmless generic footage.
    """
    import random

    cfg = get_config().defaults
    materials_dir = Path(cfg.materials_dir)
    if not materials_dir.exists():
        raise RuntimeError(f"materials_dir not found: {materials_dir}")

    # Collect all usable video files once; deterministic but varied per slot.
    videos = sorted(p for p in materials_dir.rglob("*") if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm") and p.is_file())
    if not videos:
        raise RuntimeError("no fallback material videos found in materials_dir")

    # Use slot_index to pick deterministically, then shuffle slightly by job id.
    base_idx = (slot.slot_index or 0) % max(1, len(videos))
    job_hash = sum(ord(c) for c in (slot.director_job_id or ""))
    idx = (base_idx + job_hash) % len(videos)
    return videos[idx]


def execute_black_placeholder_slot(db: Session, slot: DirectorSlot) -> str:
    """Universal fallback clip (generic b-roll), not pure black.

    ID-025: the old pure-black placeholder was flagged during QC because it
    creates dead air in the final composition. We now trim/scale a generic
    material clip from materials_dir to the slot duration.
    """
    root = ensure_slot_dir(slot)
    spec = get_video_format_spec(slot.director_job.video_format)
    duration = round(slot.end_sec - slot.start_sec, 3)
    out_path = root / f"black_placeholder_{slot.slot_index:03d}.mp4"

    src = _pick_fallback_material(db, slot)
    logger.info("[black_placeholder] slot %d using fallback material %s", slot.slot_index, src.name)
    render_scale_pad(
        src, out_path,
        width=spec["width"], height=spec["height"], duration=duration,
    )
    return str(out_path)
