"""TTS 静音切分: 按 ffmpeg silence 检测把长 WAV 切成逐行文件.

输入是由 '||' 拼接的逐行 TTS 合成结果, 段间天然存在 ~0.3-0.5s 停顿。
检测不到足够边界时按字符占比比例划分。仅依赖 ffmpeg / soundfile / numpy,
与引擎与编排层解耦。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .text import _tts_text

__all__ = ["_split_wav_by_silence", "_write_wav"]


def _split_wav_by_silence(
    wav_path: Path,
    expected_count: int,
    output_dir: Path,
    stem: str,
    line_texts: list[str],
) -> list[Path]:
    """Split a WAV into per-segment files using FFmpeg silence detection.

    Input generated from lines joined with '||' produces natural ~0.3-0.5s
    pauses; silence detection at -30dB/0.2s locates boundaries. When detection
    finds fewer splits than expected, falls back to proportional division by
    char count (each segment's share of total chars).
    """
    silences = _detect_silences(wav_path)
    total_dur = _wav_duration(wav_path)
    use_splits = _compute_split_points(silences, total_dur, expected_count, line_texts)
    split_points = [0.0] + use_splits + [total_dur]
    return _cut_segments(wav_path, split_points, output_dir, stem)


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> Path:
    sf.write(str(path), audio, sample_rate)
    return path


def _detect_silences(wav_path: Path) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect; return (start, end) pairs (empty on failure)."""
    silences: list[tuple[float, float]] = []
    try:
        cmd = [
            "ffmpeg", "-i", str(wav_path),
            "-af", "silencedetect=noise=-30dB:d=0.2",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30)
        cur_start: float | None = None
        for line in result.stderr.split("\n"):
            ms = re.search(r"silence_start:\s*(-?[\d.]+)", line)
            if ms:
                cur_start = float(ms.group(1))
                continue
            me = re.search(r"silence_end:\s*([\d.]+)", line)
            if me and cur_start is not None:
                silences.append((max(0.0, cur_start), float(me.group(1))))
                cur_start = None
    except Exception:
        silences = []
    return silences


def _wav_duration(wav_path: Path) -> float:
    """Total duration in seconds (0.0 if metadata unreadable)."""
    try:
        return round(sf.info(str(wav_path)).duration, 3)
    except Exception:
        return 0.0


def _compute_split_points(
    silences: list[tuple[float, float]],
    total_dur: float,
    expected_count: int,
    line_texts: list[str],
) -> list[float]:
    """Snap per-line proportional boundaries to the nearest silence midpoint."""
    candidates = _silence_candidates(silences, total_dur)
    expected = _proportional_boundaries(line_texts, total_dur)
    avg_seg = total_dur / max(1, expected_count)
    # Tighten tolerance so snap stays close to the proportional boundary and
    # does not drift into an intra-sentence pause.
    tolerance = max(0.45, 0.22 * avg_seg)

    use_splits: list[float] = []
    prev = 0.0
    for exp in expected:
        best: float | None = None
        for c in candidates:
            if c <= prev + 0.1:
                continue
            if best is None or abs(c - exp) < abs(best - exp):
                best = c
        pick = best if best is not None and abs(best - exp) <= tolerance else exp
        pick = min(max(pick, prev + 0.05), total_dur)
        use_splits.append(pick)
        prev = pick
    return use_splits


def _silence_candidates(silences: list[tuple[float, float]], total_dur: float) -> list[float]:
    """Midpoints of usable silences; ignore very short ones (commas / pauses)."""
    min_split = 0.3
    return [
        (s + e) / 2.0
        for s, e in silences
        if (e - s) >= 0.25 and min_split < (s + e) / 2.0 < total_dur - min_split
    ]


def _proportional_boundaries(line_texts: list[str], total_dur: float) -> list[float]:
    """Per-line expected boundaries by cleaned-text char proportion."""
    clean_texts = [_tts_text(t) or t for t in line_texts]
    total_chars = sum(len(t) for t in clean_texts) or 1
    expected: list[float] = []
    cum = 0.0
    for t in clean_texts[:-1]:
        cum += len(t) / total_chars * total_dur
        expected.append(cum)
    return expected


def _cut_segments(
    wav_path: Path,
    split_points: list[float],
    output_dir: Path,
    stem: str,
) -> list[Path]:
    """Cut each [start, end] window with ffmpeg aselect; sub-50ms → silent WAV."""
    out_paths: list[Path] = []
    for i in range(len(split_points) - 1):
        start = split_points[i]
        end = split_points[i + 1]
        seg_path = output_dir / f"{stem}_{i:02d}.wav"
        dur = end - start
        if dur < 0.05:
            # Sub-50ms segment — produce a minimal silent WAV
            _write_wav(seg_path, np.zeros((1,), dtype=np.float32), 24000)
            out_paths.append(seg_path)
            continue

        cmd = [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-af", f"aselect=between(t\\,{start}\\,{end}),asetpts=N/SR/N",
            str(seg_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=True, timeout=30)
        except Exception:
            _write_wav(seg_path, np.zeros((1,), dtype=np.float32), 24000)
        out_paths.append(seg_path)

    return out_paths
