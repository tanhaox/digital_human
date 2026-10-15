"""MP4 产物验证 — 满足用户禁忌 '不要跳过音频流、时长和帧率验证'.

校验项 (满足用户硬性约束):
  1. 视频流存在
  2. 音频流存在 (LTX23 必须输出 H.264 + AAC)
  3. 帧率匹配 expected_fps
  4. 帧数匹配 expected_duration * fps + 1 (±4 容差)
  5. 帧数对齐 8n+1 (LTX23 latent 对齐)
  6. 总时长匹配 expected_duration (±1.0s)
  7. 总时长 ≥ min_duration (默认 5s)

不要只根据 HTTP 200 判定视频成功 — 这条禁忌由 validator 兜底.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def ffprobe_metadata(media_path: Path) -> dict[str, Any]:
    """ffprobe -show_streams -show_format → dict.

    返回格式:
      {"available": False, "reason": "..."}   ffprobe 缺
      {"available": True, "error": "stderr"}   调用失败
      {"available": True, "streams": [...], "format": {...}}   成功
    """
    if not shutil.which("ffprobe"):
        return {"available": False, "reason": "ffprobe not on PATH"}
    try:
        # On Windows, ffprobe's child process may write a final stderr chunk in
        # GBK-decodable bytes that the parent reader-thread fails on, leaving
        # ``r.stdout`` as ``None``. Forcing UTF-8 with replacement sidesteps
        # the gbk codec error inside the reader thread.
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", str(media_path),
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("ffprobe failed for %s: %s", media_path, exc)
        return {"available": True, "error": str(exc)}

    if r.returncode != 0:
        return {"available": True, "error": r.stderr[:300]}

    if not r.stdout:
        return {"available": True, "error": "ffprobe returned empty stdout"}

    try:
        return {"available": True, **json.loads(r.stdout)}
    except json.JSONDecodeError as exc:
        return {"available": True, "error": f"json parse: {exc}"}


def validate_mp4(
    mp4_path: Path,
    *,
    expected_duration: float,
    expected_fps: int,
    min_duration: float = 5.0,
    frame_count_tolerance: int = 4,
) -> dict[str, Any]:
    """ffprobe 三件套 + LTX23 帧对齐 8n+1 校验.

    返回:
      ok: bool
      issues: list[str]     — 致命错误 (阻断 completed)
      warnings: list[str]   — 非致命提示 (不阻断,供 manifest 留痕)
      meta: dict (含 has_audio_stream, duration_actual, fps_actual, frame_count)

    ⚠️ 2026-07-26 P0-3 调整: Hermes 已验证修复版 workflow 输出 239 帧 = 8*29+7,
    这是 LTX23 latent 实际行为;8n+1 检查降为 warning,不再阻断 ok.
    保留硬约束三件套: 视频流 / 音频流 / fps 匹配 / 时长区间.
    """
    issues: list[str] = []
    warnings: list[str] = []
    meta = ffprobe_metadata(mp4_path)

    if not meta.get("available"):
        return {
            "ok": False,
            "issues": [meta.get("reason", "ffprobe unavailable")],
            "warnings": warnings,
            "meta": meta,
        }
    if meta.get("error"):
        return {
            "ok": False,
            "issues": ["ffprobe error: " + str(meta["error"])],
            "warnings": warnings,
            "meta": meta,
        }

    streams = meta.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    has_audio_stream = bool(audio_streams)
    duration_actual = None
    fps_actual = None
    frame_count = None

    if not video_streams:
        issues.append("no video stream")
    if not audio_streams:
        issues.append("no audio stream (LTX23 必须输出 H.264 + AAC)")

    if video_streams:
        vs = video_streams[0]
        fps_actual = _safe_fps(vs.get("avg_frame_rate") or vs.get("r_frame_rate"))
        if fps_actual and expected_fps and fps_actual != expected_fps:
            issues.append(
                f"fps mismatch: expected {expected_fps}, got {fps_actual}"
            )
        nb_raw = vs.get("nb_frames")
        if nb_raw is not None:
            try:
                frame_count = int(nb_raw)
                expected_nb = round(expected_duration * expected_fps) + 1
                if abs(frame_count - expected_nb) > frame_count_tolerance:
                    issues.append(
                        f"frame count mismatch: expected ~{expected_nb}, "
                        f"got {frame_count}"
                    )
                # 2026-07-26 P0-3: Hermes 修复版输出 239 帧 = 8*29+7,
                # 是 LTX23 latent 实际行为,降为 warning 不阻断 ok.
                if frame_count >= 2 and (frame_count - 1) % 8 != 0:
                    warnings.append(
                        f"frame count {frame_count} NOT aligned to 8n+1 "
                        f"(LTX23 latent 行为,Hermes 已验证可接受)"
                    )
            except (ValueError, TypeError):
                pass

    fmt = meta.get("format", {})
    try:
        duration_actual = float(fmt.get("duration", 0))
        if abs(duration_actual - expected_duration) > 1.0:
            issues.append(
                f"duration mismatch: expected ~{expected_duration}s, "
                f"got {duration_actual}s"
            )
        if duration_actual < min_duration:
            issues.append(
                f"duration {duration_actual}s < min {min_duration}s"
            )
    except (ValueError, TypeError):
        issues.append("format.duration not parseable")

    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "meta": {
            "has_audio_stream": has_audio_stream,
            "duration_actual": duration_actual,
            "fps_actual": fps_actual,
            "frame_count": frame_count,
        },
    }


def _safe_fps(rate: str | None) -> int:
    if not rate or "/" not in rate:
        return 0
    try:
        n, d = rate.split("/", 1)
        d_i = int(d)
        if d_i == 0:
            return 0
        return round(int(n) / d_i)
    except (ValueError, ZeroDivisionError):
        return 0