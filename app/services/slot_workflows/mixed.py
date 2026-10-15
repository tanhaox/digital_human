"""Mixed workflow: host 前景 + broll 背景 ffmpeg 合成.

对应 _EXECUTION_PHASES 的 C 线 (mixed_host_broll)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.infrastructure import run_ffmpeg
from app.models import DirectorSlot
from app.schemas import get_video_format_spec
from app.services.slot_workflows.broll_pexels import execute_broll_pexels_slot
from app.services.slot_workflows.common import ensure_slot_dir
from app.services.slot_workflows.host import execute_host_slot

__all__ = [
    "execute_mixed_host_broll_slot",
]


def execute_mixed_host_broll_slot(db: Session, slot: DirectorSlot) -> str:
    """Host video foreground composited over broll background."""
    root = ensure_slot_dir(slot)
    spec = get_video_format_spec(slot.director_job.video_format)
    host_path = execute_host_slot(db, slot)
    broll_path = execute_broll_pexels_slot(db, slot)

    out_path = root / f"mixed_{slot.slot_index:03d}.mp4"
    # Host overlay: 60% width centered
    ow = int(spec["width"] * 0.6)
    oh = int(spec["height"] * 0.6)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(broll_path), "-i", str(host_path),
        "-filter_complex",
        f"[1:v]scale={ow}:{oh}[host];[0:v][host]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,9999)'[v]",
        "-map", "[v]", "-map", "1:a:0",
        "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-shortest",
        str(out_path),
    ]
    run_ffmpeg(cmd)
    return str(out_path)
