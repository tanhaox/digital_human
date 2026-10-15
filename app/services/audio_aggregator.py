"""音频聚合器 — 接收 N 段 wav, ffmpeg concat 成 ≥ min_dur_sec 单段 wav.

为什么需要:
  主流水线按句子切段, 平均 16-22 中文字符 → 2.5-4.0s 音频.
  LTX 2.3 最低可用 ~5s. 直接喂单 segment wav → ComfyUI 因 latent length 不对齐 8n+1
  而产出失败或退化 (重复首帧 / 静帧).

策略:
  - 按用户选定的 segment_paths 顺序拼接
  - 累加直到 ≥ target_duration_sec (默认 10s)
  - 用 ffmpeg concat demuxer (而非 amix), 保留原 wav 时长不变
  - 段间插入 0.3s 静音 (与 TTS 内部 || 停顿语义对齐 — scripts/tts_client.py:752)
  - 输出 16kHz mono PCM s16le (LTX23 LTXVAudioVAEEncode 推荐输入规格)

边界:
  - 全程 subprocess (不依赖 pydub)
  - ffmpeg/ffprobe 缺 → 返回 available=False + 友好提示
  - 极端情况: 只 1 段 3s 时, 报 issues["only 3.2s, < min 5s"] → status=failed
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _ffprobe_duration(wav: Path) -> float | None:
    """ffprobe 取 wav 时长 (秒). ffmpeg 缺返回 None."""
    if not shutil.which("ffprobe"):
        return None
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(wav),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("ffprobe_duration failed for %s: %s", wav, exc)
        return None


def aggregate_segments(
    segment_paths: list[Path] | list[str],
    *,
    target_duration_sec: float = 10.0,
    min_duration_sec: float = 5.0,
    silence_gap_sec: float = 0.3,
    output_path: Path | str | None = None,
    target_sr: int = 16000,
    target_channels: int = 1,
) -> dict[str, Any]:
    """聚合 N 段 wav → 16kHz mono 单段 wav.

    返回 dict:
      ok: bool
      output_path: Path | None
      actual_duration_sec: float | None
      segments_used: int
      silence_inserted_sec: float
      issues: list[str]
    """
    if not shutil.which("ffmpeg"):
        return {"ok": False, "issues": ["ffmpeg not on PATH"], "segments_used": 0}
    if not segment_paths:
        return {"ok": False, "issues": ["no segments provided"], "segments_used": 0}

    paths = [Path(p) for p in segment_paths]
    for p in paths:
        if not p.exists():
            return {"ok": False, "issues": [f"segment not found: {p}"], "segments_used": 0}

    seg_durations: list[float] = []
    for p in paths:
        d = _ffprobe_duration(p)
        if d is None:
            return {"ok": False, "issues": [f"ffprobe failed for {p}"], "segments_used": 0}
        seg_durations.append(d)

    # 累加直到 ≥ target_duration_sec (或用尽)
    cum_dur = 0.0
    used_idx: list[int] = []
    for i, d in enumerate(seg_durations):
        if used_idx:
            cum_dur += silence_gap_sec
        used_idx.append(i)
        cum_dur += d
        if cum_dur >= target_duration_sec:
            break

    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / f"dhv_agg_{int(time.time() * 1000)}.wav"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 写 concat list (ffmpeg concat demuxer 格式)
    list_file = Path(tempfile.gettempdir()) / f"dhv_concat_{int(time.time() * 1000)}.txt"
    used_paths = [paths[i] for i in used_idx]
    # Windows 路径用 forward-slash + 单引号转义
    list_content = "\n".join(
        f"file '{p.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'"
        for p in used_paths
    )
    list_file.write_text(list_content, encoding="utf-8")

    raw_path = output_path.with_suffix(".raw.wav")
    issues: list[str] = []

    try:
        # step 1: concat demuxer (不重编, 速度快)
        r1 = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c", "copy", str(raw_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if r1.returncode != 0:
            return {
                "ok": False, "issues": [f"concat failed: {r1.stderr[:300]}"],
                "segments_used": len(used_idx),
            }

        # step 2: 重采样 + 转 mono + s16le (LTX23 推荐输入)
        r2 = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(raw_path),
                "-ar", str(target_sr), "-ac", str(target_channels),
                "-sample_fmt", "s16", "-c:a", "pcm_s16le",
                str(output_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if r2.returncode != 0:
            return {
                "ok": False, "issues": [f"resample failed: {r2.stderr[:300]}"],
                "segments_used": len(used_idx),
            }
    finally:
        raw_path.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)

    actual = _ffprobe_duration(output_path)
    if actual is None:
        issues.append("ffprobe failed on aggregated output")
    elif actual < min_duration_sec:
        issues.append(f"only {actual:.1f}s, < min {min_duration_sec}s")

    silence_total = silence_gap_sec * max(0, len(used_idx) - 1)
    return {
        "ok": not issues,
        "output_path": output_path,
        "actual_duration_sec": actual,
        "segments_used": len(used_idx),
        "silence_inserted_sec": silence_total,
        "issues": issues,
    }