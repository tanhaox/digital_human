"""Composition service — 主音轨混音 + 分段 TTS timeline 构建。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.composition_service.common import _run_ffmpeg
from app.services.composition_service.ffmpeg_steps import _has_audio_stream

logger = logging.getLogger(__name__)

__all__ = [
    "_build_slot_segments",
    "_mix_master_audio",
    "_mix_master_track",
    "_resolve_master_audio",
    "_audio_status_msg",
    "_gen_silence_fallback",
    "_build_segment_filters",
]


def _mix_master_track(
    job: Any,
    root: Path,
    concat_video: Path,
    slot_segments: list[tuple[float, float, bool]] | None,
    *,
    total_duration: float,
    evt: Any = None,
) -> Path:
    """Step 3: resolve + mix the master TTS audio into the concat video.

    Returns ``with_audio.mp4`` and emits the ``audio`` SSE event.  Post-mix
    sanity check verifies an audio stream was actually embedded.
    """
    master_audio = _resolve_master_audio(job)
    logger.info("[compose] audio: path=%s exists=%s duration=%.1fs",
                master_audio, master_audio.exists() if master_audio else False, total_duration)
    if evt:
        audio_msg = _audio_status_msg(master_audio, total_duration)
        evt({"type": "compose_step", "step": "audio", "msg": audio_msg})
    with_audio = root / "with_audio.mp4"
    _mix_master_audio(
        root, concat_video,
        master_audio or Path("__missing__"),
        total_duration, with_audio,
        slot_segments=slot_segments or None,
    )
    # Post-mix sanity: verify audio stream was embedded
    if not _has_audio_stream(with_audio):
        logger.warning("[compose] with_audio.mp4 has NO audio stream after mixing!")
        if evt:
            evt({"type": "compose_step", "step": "audio",
                 "msg": "⚠ 混音后无音频流，请检查音频文件"})
    return with_audio


def _build_slot_segments(
    completed: list,
    host_wf: set[str],
) -> list[tuple[float, float, bool]]:
    """Build per-segment timeline for the master TTS track.

    host slots → silence matching the slot duration (ComfyUI video already
    carries the synced audio); non-host slots → TTS audio trimmed from the
    master.  ``(start, end, is_host)`` in timeline order.

    修复 2026-08-17: ``no_voiceover`` 尾卡 (片尾来源声明/参考卡) 也按静音处理 —
    否则非 host 尾卡会 ``atrim=start=<音轨末尾>`` 切出空音频段, concat 失败/
    音轨截断 (尾卡在 TTS 主音轨之后, 无对应语音).
    """
    return [
        (
            float(s.start_sec),
            float(s.end_sec),
            s.workflow in host_wf
            or bool((s.params_json or {}).get("render_config", {}).get("no_voiceover")),
        )
        for s in completed
    ]


def _resolve_master_audio(job: Any) -> Path | None:
    """Resolve the master TTS audio path from the job (None if absent)."""
    if job.audio_file and job.audio_file.file_path:
        return Path(job.audio_file.file_path)
    return None


def _audio_status_msg(master_audio: Path | None, total_duration: float) -> str:
    """SSE status message describing the master audio source being mixed."""
    if master_audio and master_audio.exists():
        return f"混入 TTS 主音轨 ({master_audio.name}, {total_duration:.0f}s)…"
    if master_audio:
        return f"TTS 音频文件缺失 ({master_audio.name})，生成静音…"
    return "无 TTS 音频，生成静音…"


def _mix_master_audio(
    root: Path,
    video_path: Path,
    audio_path: Path,
    duration_sec: float,
    out_path: Path,
    *,
    slot_segments: list[tuple[float, float, bool]] | None = None,
) -> None:
    """Mix the master TTS audio into the concat video.

    If *slot_segments* is provided (list of ``(start, end, is_host)`` in
    timeline order), the function builds an audio track segment-by-segment:
    host segments → silence matching the slot duration (ComfyUI video
    already carries the synced audio); non-host segments → TTS audio
    trimmed from the master.  The resulting track is then amix-ed with the
    concat video.  This avoids the cumulative alignment drift that a
    single ``volume=enable='between(...)'`` mute approach would produce.
    """
    audio_ok = audio_path.exists()

    if not audio_ok:
        audio_path = _gen_silence_fallback(root, duration_sec)
        audio_ok = True  # fallback is usable

    if slot_segments:
        filter_complex, out_labels = _build_segment_filters(slot_segments)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", out_labels,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-t", f"{duration_sec:.3f}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
    _run_ffmpeg(cmd)


def _gen_silence_fallback(root: Path, duration_sec: float) -> Path:
    """Generate a ``silence_fallback.wav`` of *duration_sec* at 48kHz stereo."""
    logger.warning("master audio missing, generating silence")
    silence = root / "silence_fallback.wav"
    cmd_silence = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{duration_sec:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        str(silence),
    ]
    _run_ffmpeg(cmd_silence)
    return silence


def _build_segment_filters(
    slot_segments: list[tuple[float, float, bool]],
) -> tuple[str, str]:
    """Build filter_complex for per-segment audio + its concat/amix tail.

    Returns ``(filter_complex, out_label)``.  host → ``aevalsrc`` silence;
    non-host → ``atrim`` TTS slice.  The tail concats segments into ``[tts]``
    then amix-es it with the video's original audio into ``[outa]``.
    """
    seg_filters: list[str] = []
    seg_labels: list[str] = []
    for i, (start, end, is_host) in enumerate(slot_segments):
        dur = end - start
        if is_host:
            seg_filters.append(f"aevalsrc=0:duration={dur:.6f}:s=48000[s{i}]")
        else:
            seg_filters.append(
                f"[1:a]atrim={start:.6f}:duration={dur:.6f},asetpts=PTS-STARTPTS[s{i}]"
            )
        seg_labels.append(f"[s{i}]")

    concat_labels = "".join(seg_labels)
    filter_complex = (
        ";".join(seg_filters)
        + f";{concat_labels}concat=n={len(slot_segments)}:v=0:a=1[tts]"
        + ";[0:a][tts]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[outa]"
    )
    return filter_complex, "[outa]"
