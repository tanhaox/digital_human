"""Composition service — Step 1: 校验 slot 输出存在 + 缺失自动补齐 (REPAIR)。

2026-08-08 逐 clip 时长归一化 (修复音画不同步):
    slot 素材实际时长可能偏离分配时长 (slot.end_sec - slot.start_sec) —
    典型是 hf_title/hf_chart (模板 duration_sec 硬约束 [5,30]s, 渲染值被
    hf.py 夹取到整数秒, 如分配 1.77s → 渲染 5.00s)。concat -c copy 不裁剪,
    累积偏差导致视频轨比 TTS 轨 (按分配时长精确切) 长一截, 尾部画面无配音。
    因此 Step 1 对每个 clip 做归一化: 实际时长偏离分配时长超容差 (±0.2s)
    的 clip 重编码为精确分配时长 (超长 -t 裁剪, 下溢 tpad 冻结尾帧 + apad),
    输出到 job root 下的 normalized_*.mp4 (保留中间件, 由 _cleanup_intermediates
    回收); 在容差内或已精确的 clip 直接复用原文件, 保持 -c copy 零重编码。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from app.services.composition_service.common import _run_ffmpeg, _WF_TIER
from app.services.video_validator import ffprobe_metadata

logger = logging.getLogger(__name__)

# 归一化判定容差 (s): 实际时长与分配时长偏差 ≤ 该值视为已对齐, 直接复用。
# -t 裁剪时视频流时长受 keyframe 对齐影响 ±1 帧 (≈0.033s), 取 0.2s 安全。
_NORMALIZE_TOLERANCE = 0.2

__all__ = [
    "_collect_slot_sources",
    "_dedupe_completed_slots",
    "_normalize_clip_duration",
    "_clip_actual_duration",
]


def _dedupe_completed_slots(completed: list[Any], evt: Any = None) -> list[Any]:
    """Dedupe completed slots to one clip per slot_index.

    Pick the HIGHEST quality workflow first, then by most-recent updated_at
    within the same quality tier. Returns slots sorted by slot_index.
    """
    # Quality tiers (lower = better):
    #   0: host / mixed  (primary GPU-rendered content)
    #   1: broll_pexels / broll_local / hf_*  (secondary content)
    #   9: black_placeholder (fallback placeholder — 无字幕纯黑屏, 字幕体系已砍)
    by_index: dict[int, Any] = {}
    for s in completed:
        cur = by_index.get(s.slot_index)
        s_tier = _WF_TIER.get(s.workflow, 5)
        if cur is None:
            by_index[s.slot_index] = s
        else:
            cur_tier = _WF_TIER.get(cur.workflow, 5)
            if s_tier < cur_tier:
                by_index[s.slot_index] = s  # better quality
            elif s_tier == cur_tier and s.updated_at and cur.updated_at and s.updated_at > cur.updated_at:
                by_index[s.slot_index] = s  # same tier, newer
    deduped = sorted(by_index.values(), key=lambda s: s.slot_index)

    # SSE: report dedup result so user can see workflow distribution
    if evt:
        from collections import Counter

        wf_counts = Counter(s.workflow for s in deduped)
        evt({"type": "compose_step", "step": "dedup",
             "msg": f"去重结果: {dict(wf_counts)}",
             "workflow_counts": dict(wf_counts)})
    return deduped


def _collect_slot_sources(
    db: Any,
    completed: list[Any],
    evt: Any = None,
    *,
    root: Path | None = None,
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Validate all completed slots have existing outputs; REPAIR missing ones.

    Returns ``(src_paths, slot_meta)`` — source clip paths in slot_index
    order, plus per-slot metadata for the composition manifest.
    """
    slot_meta: list[dict[str, Any]] = []
    src_paths: list[Path] = []
    for i, slot in enumerate(completed):
        src = Path(slot.output_path) if slot.output_path else None
        if src is None or not src.exists():
            # ── 缺失 slot 输出自动补齐 (2026-08-07) ──────────────────────
            # 正常情况下 slot 素材保留 7 天 (slot_retention_days), exists() 恒真,
            # 此分支是安全网: 仅当素材已被保留扫描清理 / 人为删除时触发,
            # 按 workflow 重新执行该 slot 补回素材。
            logger.warning(
                "[compose] slot %d output missing: %s — re-execute workflow %s",
                slot.slot_index, src, slot.workflow,
            )
            if evt:
                evt({"type": "compose_step", "step": "repair",
                     "msg": f"补齐缺失片段 #{slot.slot_index} ({slot.workflow})…"})
            src = _repair_slot_output(db, slot)
        src_paths.append(src)
        slot_meta.append({
            "slot_index": slot.slot_index,
            "start_sec": slot.start_sec,
            "end_sec": slot.end_sec,
            "workflow": slot.workflow,
            "text_context": slot.text_context,
            "source_path": str(src),
        })
    # ── 逐 clip 时长归一化 (2026-08-08): 让视频轨时长对齐 TTS 轨 ──────────
    # 只对偏离分配时长的 clip 重编码, 其余保持 -c copy 零重编码。
    # 输出落点 = job root (而非 src_paths[0].parent), 确保 _cleanup_intermediates
    # 能回收 normalized_*.mp4 中间件。
    src_paths = _normalize_clip_duration(src_paths, completed, root=root)
    if evt:
        evt({"type": "compose_step", "step": "validate", "msg": f"校验 {len(completed)} 个片段…"})
    return src_paths, slot_meta


def _clip_actual_duration(clip: Path) -> float:
    """Probe a clip's actual duration in seconds (0.0 on any failure)."""
    try:
        probe = ffprobe_metadata(clip)
        return float(probe.get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


def _normalize_clip_duration(
    src_paths: list[Path],
    completed: list[Any],
    *,
    root: Path | None = None,
) -> list[Path]:
    """Trim/pad each clip to its slot's allocated duration (fix A/V sync).

    Returns a new list where deviant clips are replaced by re-encoded
    ``normalized_<slot_index>.mp4`` clips (kept as intermediates so
    ``_cleanup_intermediates`` recycles them); aligned clips pass through.
    """
    # 归一化输出落点: 优先 job root (cleanup 能回收), 缺省回退第一个 clip 父目录。
    out_root = root or (src_paths[0].parent if src_paths else None)
    normalized: list[Path] = []
    for i, (clip, slot) in enumerate(zip(src_paths, completed)):
        alloc = float(slot.end_sec - slot.start_sec)
        actual = _clip_actual_duration(clip)
        if actual <= 0:
            # 无法探测时长 — 无法安全归一化, 原样放行 (不裁剪避免切坏画面)
            normalized.append(clip)
            continue
        drift = actual - alloc
        if abs(drift) <= _NORMALIZE_TOLERANCE:
            normalized.append(clip)
            continue
        if out_root is None:
            normalized.append(clip)
            continue
        try:
            out = _trim_pad_clip(clip, out_root, slot.slot_index, alloc)
            logger.warning(
                "[compose] slot %d normalized: actual=%.2fs alloc=%.2fs drift=%+.2fs -> %s",
                slot.slot_index, actual, alloc, drift, out.name,
            )
            normalized.append(out)
        except Exception:
            logger.exception(
                "[compose] slot %d normalize failed (drift %+.2fs), reuse original",
                slot.slot_index, drift,
            )
            normalized.append(clip)
    return normalized


def _trim_pad_clip(clip: Path, root: Path, slot_index: int, alloc: float) -> Path:
    """Re-encode *clip* to exactly *alloc* seconds (trim over / pad under).

    ``tpad=stop_mode=clone`` freezes the last frame (+1s) and ``apad`` adds
    silence, then ``-t <alloc>`` pins the exact length — for over-length clips
    ``-t`` trims the extra (frame precision, keyframe drift ≤ ~1 frame), for
    under-length clips the frozen tail + silence fill the gap.  Output uses the
    same H.264 / 48kHz AAC params as ``render_scale_pad`` so it mixes cleanly
    with the ``-c copy`` concat.
    """
    out = root / f"normalized_{slot_index:03d}.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(clip),
        "-vf", "tpad=stop_mode=clone:stop_duration=1,fps=30,format=yuv420p",
        "-af", "apad=pad_dur=1",
        "-t", f"{alloc:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
        str(out),
    ]
    _run_ffmpeg(cmd)
    return out


def _repair_slot_output(db: Any, slot: Any) -> Path:
    """Re-execute the slot's workflow to restore a missing output file.

    Returns the repaired output path (updated on the slot row + committed).
    """
    from app.services.slot_workflows import WORKFLOW_HANDLERS

    handler = WORKFLOW_HANDLERS.get(slot.workflow)
    if handler is None:
        raise RuntimeError(
            f"slot {slot.slot_index} output missing (no handler for {slot.workflow})"
        )
    try:
        if slot.workflow in ("hf_chart", "hf_title"):
            new_out = handler(db, slot, slot.workflow)
        else:
            new_out = handler(db, slot)
    except Exception as exc:
        raise RuntimeError(
            f"slot {slot.slot_index} repair failed ({slot.workflow}): {exc}"
        ) from exc
    if not new_out or not Path(new_out).exists():
        raise RuntimeError(
            f"slot {slot.slot_index} repaired output missing: {new_out}"
        )
    slot.output_path = new_out
    db.commit()
    logger.info("[compose] slot %d repaired -> %s", slot.slot_index, new_out)
    return Path(new_out)
