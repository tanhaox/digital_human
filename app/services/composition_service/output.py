"""Composition service — Step 5: 校验输出 + 写 manifest + 写库; Step 6: 清理中间件。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.services.composition_service.common import _cleanup_intermediates, _now
from app.services.video_validator import ffprobe_metadata

logger = logging.getLogger(__name__)

__all__ = [
    "_finalize_output",
    "_build_manifest",
    "_write_manifest_file",
    "_finalize_cleanup",
    "_write_script_txt",
]


def _finalize_output(
    db: Any,
    job: Any,
    final_normalized: Path,
    slot_meta: list[dict[str, Any]],
    *,
    target_lufs: float,
    crossfade_sec: float,
    issues: list[str],
) -> tuple[Path, Path, float]:
    """Validate the normalized output, write manifest, persist DB state.

    Returns ``(final_path, manifest_path, duration_actual)``.
    """
    probe = ffprobe_metadata(final_normalized)
    if not probe.get("available"):
        raise RuntimeError("ffprobe unavailable for final validation")
    if probe.get("error"):
        raise RuntimeError(f"ffprobe error: {probe['error']}")

    fmt = probe.get("format", {})
    duration_actual = float(fmt.get("duration", 0))

    final_name = f"director_{job.id}.mp4"
    final_path = final_normalized.parent / final_name
    os.replace(final_normalized, final_path)

    manifest = _build_manifest(
        job, final_path, duration_actual,
        target_lufs=target_lufs,
        crossfade_sec=crossfade_sec,
        slot_meta=slot_meta,
        issues=issues,
    )
    manifest_path = _write_manifest_file(manifest, final_normalized.parent)

    # 合成成功 → 把洗稿文本写入成片同目录 (2026-08-08)
    # 文本取 job.script.script_text (用户洗稿后手动编辑过则以最新编辑为准),
    # 与成片同放 composition root, 便于对照成片/人工复核。
    try:
        _write_script_txt(job, final_normalized.parent)
    except Exception:
        logger.exception("[compose] write 洗稿.txt failed for job %s (non-fatal)", job.id)

    job.status = "completed"
    job.completed_at = _now()
    job.error_message = None  # 成功路径清掉历史失败残留
    db.commit()
    db.refresh(job)
    return final_path, manifest_path, duration_actual


def _write_script_txt(job: Any, parent: Path) -> None:
    """Write the rewrite (洗稿) script text as 洗稿.txt next to the final video."""
    script = getattr(job, "script", None)
    text = getattr(script, "script_text", None)
    if not text:
        logger.warning("[compose] job %s has no script_text, skip 洗稿.txt", job.id)
        return
    from app.services.pinyin_fix import strip_pinyin_marks

    (parent / "洗稿.txt").write_text(strip_pinyin_marks(text), encoding="utf-8")


def _write_manifest_file(manifest: dict[str, Any], parent: Path) -> Path:
    """Persist the composition manifest JSON under *parent*."""
    manifest_path = parent / "composition_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest_path


def _build_manifest(
    job: Any,
    final_path: Path,
    duration_actual: float,
    *,
    target_lufs: float,
    crossfade_sec: float,
    slot_meta: list[dict[str, Any]],
    issues: list[str],
) -> dict[str, Any]:
    """Build the composition manifest dict (schema_version 1.0)."""
    return {
        "schema_version": "1.0",
        "job_id": job.id,
        "status": "completed",
        "output_path": str(final_path),
        "duration_sec": duration_actual,
        "target_lufs": target_lufs,
        "crossfade_sec": crossfade_sec,
        "slots": slot_meta,
        "warnings": issues,
        "created_at": _now().isoformat(),
    }


def _finalize_cleanup(
    root: Path,
    keep_intermediates: bool,
    evt: Any = None,
) -> None:
    """Step 6: clean up intermediate build artifacts (fire cleanup SSE)."""
    freed_bytes = _cleanup_intermediates(root, keep_intermediates=keep_intermediates)
    if freed_bytes and evt:
        evt({"type": "compose_cleanup", "msg": f"清理中间文件释放 {freed_bytes / (1024*1024):.1f} MB"})
