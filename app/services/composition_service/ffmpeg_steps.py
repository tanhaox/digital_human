"""Composition service — ffmpeg 拼接 / 音频检测 / 响度归一化步骤。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from app.services.composition_service.common import _run_ffmpeg

logger = logging.getLogger(__name__)

__all__ = [
    "_concat_demuxer_concat",
    "_has_audio_stream",
    "_loudnorm",
]


def _concat_demuxer_concat(
    root: Path,
    clips: list[Path],
    crossfade_sec: float = 0.2,  # noqa: ARG001 — 已弃用: 本机 ffmpeg fade 滤镜在多段上压黑,详见下
) -> Path:
    """Concatenate clips using ffmpeg concat demuxer (no crossfade).

    修复 2026-07-31 黑屏 bug 的最终定案: 彻底去掉 fade 滤镜.
    实证 (digital_human/scripts/diag_*_tmp.py):
      1) 旧写法 fade=t=out:st=0:d=0.2:alpha=0,fade=t=in:st=0:d=0.2 在时间0压黑全片
      2) 本机 ffmpeg 的 fade=t=out 不带 alpha=1 会把画面叠成黑底 (亮度0.0)
      3) fade=t=in 带 st>0 会把该 fade 之前的所有帧压黑 → 逐段淡入淡出在多段
         concat 上只剩最后一段可见
    crossfade_sec 参数保留仅为兼容调用方,不再生效 (0.2s 的 MVP 点缀不值得为
    它承担黑屏风险).
    """
    list_path = root / "concat_list.txt"
    lines = [f"file '{p.resolve().as_posix()}'" for p in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")

    concat_path = root / "concat_raw.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-fflags", "+genpts",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(concat_path),
    ]
    _run_ffmpeg(cmd)
    return concat_path


def _has_audio_stream(video_path: Path) -> bool:
    """Quick check whether *video_path* contains at least one audio stream."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0",
             str(video_path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def _loudnorm(video_path: Path, out_path: Path, target_lufs: float = -14.0) -> None:
    """Two-pass loudnorm to target integrated LUFS."""
    # Pre-check: if there is no audio stream, just copy the video.
    if not _has_audio_stream(video_path):
        _copy_fallback(video_path, out_path, "loudnorm skipped: input has no audio stream")
        return

    measured = _loudnorm_measure(video_path, target_lufs)

    # Detect silence / inaudible audio (input_i == "-inf" or below -70 LUFS)
    if _is_silent(measured):
        input_i = measured.get("input_i", "-inf")
        _copy_fallback(
            video_path, out_path,
            f"loudnorm: audio appears silent (input_i={input_i}), skipping normalization",
        )
        return

    if not measured:
        _copy_fallback(video_path, out_path, "loudnorm pass 1 returned no JSON, copying audio without normalization")
        return

    # Pass 2: apply measured parameters
    _loudnorm_pass2(video_path, out_path, target_lufs, measured)


def _is_silent(measured: dict[str, Any]) -> bool:
    """True when measured ``input_i`` is "-inf" or below -70 LUFS."""
    input_i = measured.get("input_i", "-inf")
    try:
        return float(input_i) < -70
    except (ValueError, TypeError):
        return False


def _loudnorm_pass2(
    video_path: Path,
    out_path: Path,
    target_lufs: float,
    measured: dict[str, Any],
) -> None:
    """Pass 2: apply the measured loudnorm parameters via ffmpeg."""
    filter_str = (
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
        f"measured_I={measured.get('input_i', -23.0)}:"
        f"measured_TP={measured.get('input_tp', -1.0)}:"
        f"measured_LRA={measured.get('input_lra', 1.0)}:"
        f"measured_thresh={measured.get('input_thresh', -30.0)}:"
        f"offset={measured.get('target_offset', 0.0)}"
    )
    cmd2 = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-af", filter_str,
        "-c:v", "copy",
        str(out_path),
    ]
    _run_ffmpeg(cmd2)


def _copy_fallback(video_path: Path, out_path: Path, warning: str) -> None:
    """Log *warning* and copy the input to the output without normalization."""
    logger.warning(warning)
    os.replace(video_path, out_path)


def _loudnorm_measure(video_path: Path, target_lufs: float) -> dict[str, Any]:
    """Pass 1: measure loudness — returns parsed JSON dict (empty if unparsable)."""
    # Pass 1: measure — must use -loglevel info so loudnorm JSON appears in stderr
    cmd1 = [
        "ffmpeg", "-y", "-loglevel", "info",
        "-i", str(video_path),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    # 2026-08-08: 600s 卡死窗口 → Popen + 进程注册 + 杀树 (同 run_ffmpeg 样板).
    # force-stop 依赖 proc_registry 按 job_id 杀整棵进程树, 否则 compose 卡死无法中断.
    import platform
    from app.services.proc_registry import register_subprocess, unregister_subprocess

    is_win = platform.system() == "Windows"
    # CREATE_NO_WINDOW (2026-08-22): ffmpeg 控制台程序, 缺它会从服务器进程 spawn 弹 cmd 窗口
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if is_win else 0
    )
    proc = subprocess.Popen(
        cmd1,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    register_subprocess(proc)
    try:
        try:
            _, r_stderr = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            if is_win:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                proc.kill()
            try:
                _, r_stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                r_stderr = ""
                proc.kill()
    finally:
        unregister_subprocess(proc)

    # Parse JSON from stderr tail (loudnorm writes JSON at AV_LOG_INFO)
    json_str = ""
    if r_stderr:
        lines = r_stderr.strip().splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "{":
                json_str = "\n".join(lines[i:])
                break
    measured: dict[str, Any] = {}
    if json_str:
        try:
            measured = json.loads(json_str)
        except json.JSONDecodeError:
            measured = {}
    return measured
