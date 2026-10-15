# -*- coding: utf-8 -*-
"""ffmpeg / ffprobe 基础设施 — 统一进程调用与常见转码 helper.

集中 slot_workflows / composition_service / audio_aggregator 等处的重复
ffmpeg 命令, 统一超时与错误语义:
    - ffmpeg 不在 PATH → RuntimeError("ffmpeg not on PATH")
    - 命令返回码非 0 → RuntimeError("ffmpeg failed: {stderr[:500]}")
    - 超时 → RuntimeError("ffmpeg timed out after {timeout}s: {stderr[:500]}")
      (原 subprocess.TimeoutExpired 语义, 调用方仅 catch 泛异常, 兼容)
仅做外部系统适配, 不承载业务逻辑。
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "run_ffmpeg",
    "extract_audio_slice",
    "extract_audio_slice_wav",
    "replace_video_audio",
    "render_scale_pad",
    "frames_to_mp4",
    "normalize_audio",
]


def run_ffmpeg(cmd: list[str], *, timeout: int = 300) -> None:
    """Run ffmpeg and raise RuntimeError on failure.

    timeout 默认 300s (slot 侧语义); composition_service 等耗时长任务
    显式传 600 保持原行为。

    2026-08-08 重写: subprocess.run(timeout=) → Popen + 进程注册 + 杀树.
    原因: subprocess.run timeout 只杀主进程, ffmpeg 的子进程 (编码线程等)
    继承 stdout/stderr 管道 → communicate() 死锁, 协作式取消失效.
    force-stop 依赖 proc_registry 按 job_id 杀整棵进程树 (taskkill /F /T).
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not on PATH")

    from app.services.proc_registry import register_subprocess, unregister_subprocess

    is_win = platform.system() == "Windows"
    # CREATE_NO_WINDOW: ffmpeg 是控制台程序, 无 CREATE_NO_WINDOW 会从服务器进程
    # spawn 时弹 cmd 窗口 (2026-08-22 与 HF chrome-headless-shell 同因)
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if is_win else 0
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    register_subprocess(proc)

    timed_out = False
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if is_win:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            proc.kill()
        try:
            _, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stderr = ""
            proc.kill()
    finally:
        unregister_subprocess(proc)

    if timed_out:
        raise RuntimeError(
            f"ffmpeg timed out after {timeout}s: {(stderr or '')[:500]}"
        )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {(stderr or '')[:500]}")


def extract_audio_slice(audio_path: Path, out_path: Path, start: float, end: float) -> None:
    """Extract [start, end) slice as AAC 192k (TTS 聚合用)."""
    duration = end - start
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(audio_path),
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def extract_audio_slice_wav(audio_path: Path, out_path: Path, start: float, end: float) -> None:
    """Extract slot audio segment as 16-bit PCM WAV (ComfyUI LoadAudio compatible)."""
    duration = end - start
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(audio_path),
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-acodec", "pcm_s16le", "-ar", "48000",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def replace_video_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    """Replace *video_path* audio track with *audio_path*, video stream copied."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def render_scale_pad(
    src: Path,
    out_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
) -> None:
    """Trim + scale + pad to (width,height), 30fps H.264 + silent AAC 192k.

    统一 slot_workflows 中 broll_pexels / broll_local / black_placeholder
    三处相同的转码命令 (含 anullsrc 静音轨, 保证成片带音频流)。
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-ss", "0", "-t", f"{duration:.3f}",
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def frames_to_mp4(
    frames_dir: Path,
    pattern: str,
    out_path: Path,
    fps: int = 25,
    width: int = 1920,
    height: int = 1080,
    *,
    crf: int = 18,
    timeout: int = 600,
) -> None:
    """Encode PNG frame sequence to mp4 (Playwright seek-and-snap 后续).

    pattern 如 "frame_%06d.png", frames_dir 下需有连续编号帧.
    HyperFrames encodeFramesFromDir 形态: image2 demuxer → libx264 yuv420p.
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(Path(frames_dir) / pattern),
        "-vf", f"scale={width}:{height}",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out_path),
    ]
    run_ffmpeg(cmd, timeout=timeout)


def normalize_audio(
    src: Path,
    out_path: Path,
    *,
    lufs: float = -16.0,
    tp: float = -1.5,
    lra: float = 11,
    timeout: int = 120,
) -> Path:
    """响度归一 (2026-08-21): TTS 输出 mean≈-39dB 偏轻, loudnorm 拉到
    I=-16 LUFS / TP=-1.5 (口播标准). 产线所有语音落盘前调用.
    """
    run_ffmpeg([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-af", f"loudnorm=I={lufs}:TP={tp}:LRA={lra}",
        "-c:a", "pcm_s16le",
        str(out_path),
    ], timeout=timeout)
    return out_path
