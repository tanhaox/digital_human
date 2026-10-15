"""HF visual render orchestrator — drives the full state machine.

Pipeline per job:
    preparing → rendering → validating → completed / failed

Each step:
- touches the DB so the UI polling can observe transitions
- records manifest at the end (or partial manifest on failure)
- does NOT swallow exceptions silently; always commits a terminal status
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_config
from app.models import VisualRenderJob
from app.services.hf_client import HFRenderError, render_visual
from app.services.template_filler import fill_template
from app.services.template_library import (
    get_template,
    resolve_template_dir,
    validate_input,
)
from app.services.video_validator import ffprobe_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RENDERER_NAME = "hyperframes"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_audio_track(mp4_path: Path, duration_sec: float) -> bool:
    """If mp4 has no audio stream, add a 48kHz aac silent track in-place.

    Returns True if audio was added, False if it already existed or ffmpeg unavailable.
    """
    if not shutil.which("ffmpeg"):
        return False
    # Quick probe: check for audio stream
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(mp4_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,  # 2026-08-22: 防 cmd 弹窗
    )
    if r.returncode == 0 and r.stdout.strip():
        return False  # already has audio

    logger.info("[hf] adding 48kHz silent audio track to %s", mp4_path.name)
    tmp = mp4_path.with_suffix(".tmp_audio.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(mp4_path),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{duration_sec:.3f}",
        "-c:v", "copy",
        "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(tmp),
    ]
    r2 = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=300,
                        creationflags=subprocess.CREATE_NO_WINDOW)  # 2026-08-22: 防 cmd 弹窗
    if r2.returncode != 0:
        logger.warning("[hf] silent audio add failed: %s", r2.stderr[:300])
        return False
    tmp.replace(mp4_path)
    return True


def _extract_first_frame(mp4_path: Path, png_path: Path) -> bool:
    return _extract_frame(mp4_path, png_path, at_seconds=None, label="first")


def _extract_middle_frame(mp4_path: Path, png_path: Path, duration: float) -> bool:
    mid = max(0.0, duration / 2)
    return _extract_frame(mp4_path, png_path, at_seconds=mid, label="middle")


def _extract_final_frame(mp4_path: Path, png_path: Path, duration: float) -> bool:
    return _extract_frame(mp4_path, png_path, at_seconds=max(0.0, duration - 0.05), label="final")


def _extract_frame(mp4_path: Path, png_path: Path, at_seconds: float | None, label: str) -> bool:
    """Use ffmpeg to extract a single frame. at_seconds=None → first frame."""
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not on PATH; skip %s frame extraction", label)
        return False
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if at_seconds is None:
        cmd += ["-i", str(mp4_path), "-vframes", "1", str(png_path)]
    else:
        cmd += ["-ss", f"{at_seconds:.3f}", "-i", str(mp4_path), "-vframes", "1", str(png_path)]
    try:
        # Force UTF-8 to sidestep the Windows gbk reader-thread crash.
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=20, check=False,
                           creationflags=subprocess.CREATE_NO_WINDOW)  # 2026-08-22: 防 cmd 弹窗
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg %s frame extraction timed out", label)
        return False
    ok = r.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0
    if not ok:
        logger.warning("ffmpeg %s frame extraction failed: %s", label, r.stderr[:200])
    return ok


def _read_renderer_version() -> str:
    """Best-effort lookup of HF CLI version."""
    cfg = get_config().defaults
    try:
        r = subprocess.run(
            [cfg.hyperframes_bin, "-y", "hyperframes", "--version"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=10, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,  # 2026-08-22: 防 cmd 弹窗
        )
        if r.returncode == 0:
            v = (r.stdout or r.stderr or "").strip().splitlines()
            return v[0] if v else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

def execute_visual_render_job(
    db: Session,
    job_id: str,
    template_id: str,
    input_data: dict,
) -> dict[str, Any]:
    """Synchronously execute a visual render job end-to-end.

    Returns a dict summarizing the terminal state.
    Raises nothing — all failure paths commit a ``failed`` status.
    """
    cfg = get_config().defaults
    started = _now()
    warnings: list[str] = []

    job: VisualRenderJob | None = db.get(VisualRenderJob, job_id)
    if job is None:
        return {"status": "failed", "error_code": "JOB_NOT_FOUND", "error_message": f"job {job_id} missing"}

    # 0. Input validation (jsonschema) — already done at create time, but recheck
    try:
        validate_input(template_id, input_data)
    except Exception as exc:  # jsonschema.ValidationError or KeyError
        job.status = "failed"
        job.error_code = "INVALID_INPUT"
        job.error_message = f"Input validation failed: {exc}"
        job.completed_at = _now()
        db.commit()
        return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}

    meta = get_template(template_id) or {}
    composition_id = meta.get("composition_id", "news_main")
    template_version = meta.get("version", "1.0.0")
    duration_sec = int(input_data.get("duration_sec") or 12)

    # 1. preparing — copy template + write input.json + substitute placeholders
    job.status = "preparing"
    job.started_at = started
    job.composition_id = composition_id
    job.template_version = template_version
    db.commit()

    try:
        template_dir = resolve_template_dir(template_id, cfg.hf_template_root)
    except FileNotFoundError as exc:
        job.status = "failed"
        job.error_code = "TEMPLATE_NOT_FOUND"
        job.error_message = str(exc)
        job.completed_at = _now()
        db.commit()
        return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}

    try:
        workspace = fill_template(template_dir, template_id, input_data, job_id)
    except Exception as exc:
        logger.exception("template_filler failed for %s", job_id)
        job.status = "failed"
        job.error_code = "TEMPLATE_FILL_FAILED"
        job.error_message = f"{type(exc).__name__}: {exc}"
        job.completed_at = _now()
        db.commit()
        return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}

    rendered_dir = workspace / "rendered"
    frames_dir = rendered_dir / "frames"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    # HF writes the mp4 into the cwd (workspace root) with the basename we pass.
    # We keep ``rendered/`` only as the destination for the extracted frames
    # so the manifest can describe both the video (root) and frames (nested).
    output_path = workspace / "visual_segment.mp4"
    job.input_path = str(workspace / "input.json")
    job.output_dir = str(rendered_dir)
    job.output_path = str(output_path)
    db.commit()

    # 2. rendering — npx hyperframes render
    job.status = "rendering"
    db.commit()

    render_start = time.monotonic()
    try:
        render_visual(
            project_dir=workspace,
            output_path=output_path,
            hyperframes_bin=cfg.hyperframes_bin,
            timeout_sec=cfg.hf_render_timeout_sec,
            # 提速 (2026-08-11): 2 worker 并行截帧 + fast_capture + GPU 编码
            # (本机 RTX 4090/4060, 独立 GPU 避免集显 WebGL 资源卡死)
            workers=2,
            fast_capture=True,
            gpu_encode=True,
        )
    except subprocess.TimeoutExpired:
        job.status = "failed"
        job.error_code = "TIMEOUT"
        job.error_message = f"HyperFrames exceeded {cfg.hf_render_timeout_sec}s timeout"
        job.completed_at = _now()
        db.commit()
        return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}
    except HFRenderError as exc:
        job.status = "failed"
        job.error_code = "RENDER_FAILED"
        job.error_message = str(exc)
        job.completed_at = _now()
        db.commit()
        return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}

    render_seconds = round(time.monotonic() - render_start, 2)

    if not output_path.exists() or output_path.stat().st_size == 0:
        job.status = "failed"
        job.error_code = "OUTPUT_MISSING"
        job.error_message = "HyperFrames returned success but mp4 not found / empty"
        job.completed_at = _now()
        db.commit()
        return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}

    # 3. validating — ffprobe (visual-only; no audio stream check for HF)
    job.status = "validating"
    db.commit()

    ff = ffprobe_metadata(output_path)
    if not ff.get("available"):
        warnings.append(f"ffprobe unavailable: {ff.get('reason', '?')}")
        media = {}
        actual_duration = float(duration_sec)
        actual_fps = 30
        actual_w = 1080
        actual_h = 1920
    elif ff.get("error"):
        warnings.append(f"ffprobe error: {ff['error']}")
        media = {}
        actual_duration = float(duration_sec)
        actual_fps = 30
        actual_w = 1080
        actual_h = 1920
    else:
        streams = ff.get("streams", [])
        vstreams = [s for s in streams if s.get("codec_type") == "video"]
        if not vstreams:
            job.status = "failed"
            job.error_code = "NO_VIDEO_STREAM"
            job.error_message = "FFprobe: output has no video stream"
            job.completed_at = _now()
            db.commit()
            return {"status": "failed", "error_code": job.error_code, "error_message": job.error_message}
        vs = vstreams[0]
        codec = vs.get("codec_name", "unknown")
        actual_w = int(vs.get("width", 0))
        actual_h = int(vs.get("height", 0))
        fps_str = vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "30/1"
        try:
            n, d = fps_str.split("/", 1)
            actual_fps = round(int(n) / int(d)) if int(d) else 30
        except Exception:
            actual_fps = 30
        try:
            actual_duration = float(ff.get("format", {}).get("duration", duration_sec))
        except (TypeError, ValueError):
            actual_duration = float(duration_sec)
        media = {
            "codec": codec,
            "width": actual_w,
            "height": actual_h,
            "fps": actual_fps,
            "duration_sec": round(actual_duration, 3),
        }
        # Soft validation (warn-only; HF visual does not need 8n+1 / audio)
        if abs(actual_duration - duration_sec) > 2.0:
            warnings.append(
                f"duration drift: expected {duration_sec}s, got {actual_duration:.2f}s"
            )

    # 3.5 normalize audio — HF outputs may lack audio track; add 48kHz silent audio if needed
    # so downstream concat -c copy doesn't hit stream mismatch
    _ensure_audio_track(output_path, actual_duration)

    # 4. extract first/middle/final frames
    first_png = frames_dir / "first.png"
    middle_png = frames_dir / "middle.png"
    final_png = frames_dir / "final.png"

    extracted_ok = {
        "first": _extract_first_frame(output_path, first_png),
        "middle": _extract_middle_frame(output_path, middle_png, actual_duration),
        "final": _extract_final_frame(output_path, final_png, actual_duration),
    }
    for k, v in extracted_ok.items():
        if not v:
            warnings.append(f"failed to extract {k} frame")

    preview_frames: dict[str, str] = {}
    for k, p in [("first", first_png), ("middle", middle_png), ("final", final_png)]:
        if p.exists() and p.stat().st_size > 0:
            preview_frames[k] = str(p)
        else:
            preview_frames[k] = ""

    # 5. write manifest
    manifest = {
        "schema_version": "1.0",
        "status": "completed",
        "job_id": job_id,
        "template_id": template_id,
        "template_version": template_version,
        "renderer": RENDERER_NAME,
        "renderer_version": _read_renderer_version(),
        "composition_id": composition_id,
        "input_path": job.input_path,
        "output_path": str(output_path),
        "preview_frames": preview_frames,
        "media": media,
        "started_at": started.isoformat(),
        "completed_at": _now().isoformat(),
        "render_seconds": render_seconds,
        "warnings": warnings,
        "errors": [],
    }
    manifest_path = workspace / "render_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 6. commit terminal state
    job.status = "completed"
    job.manifest_path = str(manifest_path)
    job.preview_frames = preview_frames
    job.media = media
    job.render_seconds = render_seconds
    job.warnings = warnings
    job.completed_at = _now()
    db.commit()

    return {
        "status": "completed",
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "preview_frames": preview_frames,
        "media": media,
        "render_seconds": render_seconds,
        "warnings": warnings,
    }