# -*- coding: utf-8 -*-
"""音效库挂载 — 语义音效路径查找 / 草稿音效轨挂载 / 时长探测.

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft_mod
from pyJianYingDraft import trange

logger = logging.getLogger(__name__)

__all__ = ["sound_path", "attach_sound"]

# ── 音效挂载 (J2 前置, 2026-08-16): 语义分类见 data/jy_sounds/_semantics.json ──
# ⚠️ 拆包注记: 原单文件 parents[2]=digital_human/; 包内须 parents[3].
_SOUNDS_DIR = Path(__file__).resolve().parents[3] / "data" / "jy_sounds"

_US = 1_000_000  # 秒 → 微秒


def _probe_duration(path: str | Path) -> float | None:
    """pymediainfo 兜底探测时长 (AudioFile.duration 为空时)."""
    try:
        import pymediainfo

        mi = pymediainfo.MediaInfo.parse(str(path))
        track = mi.tracks[0] if mi.tracks else None
        if track and track.duration:
            return float(track.duration) / 1000.0
    except Exception as exc:
        logger.warning("[jy_export] 时长探测失败 %s: %s", path, exc)
    return None


def sound_path(name: str) -> Path | None:
    """按语义名取音效文件 (库: data/jy_sounds/<名>.mp3)."""
    p = _SOUNDS_DIR / f"{name}.mp3"
    return p if p.exists() else None


def sound_loudness(name: str) -> float | None:
    """音效实测响度 (EBU R128 integrated LUFS, 缓存 _loudness.json).

    0916 统一音量令: 库内文件电平差异大 (Victory 实测过热), 挂载侧按响度
    归一需要每条实测值; 缺文件/测量失败返回 None (调用方回退原始音量).
    """
    import json
    import re as _re
    import subprocess as _sp

    cache_p = _SOUNDS_DIR / "_loudness.json"
    try:
        cache = json.loads(cache_p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cache = {}
    if name in cache:
        return float(cache[name])
    p = sound_path(name)
    if not p:
        return None
    r = _sp.run(["ffmpeg", "-i", str(p), "-af", "ebur128", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30)
    hits = _re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", r.stderr or "")
    if not hits:
        return None
    val = float(hits[-1])  # 末值 = 终版积分 (首值是积分启动前的 -70 占位)
    cache[name] = val
    try:
        cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return val


def attach_sound(script: Any, track_name: str, sound_name: str, at_sec: float,
                 volume: float = 1.0, max_sec: float | None = None) -> bool:
    """往草稿音效轨挂一个音效. 缺文件时记日志返回 False (不阻塞导出).

    重叠防护 v2 (2026-08-25, 修 400 "New segment overlaps"): sfx 轨既有 HF
    转场音(whoosh) 又有字幕音效, 各自独立触发。v1 (ID-054) 只把新段结尾
    截到不越过既有段起点, 漏了"新起点落在既有段内部"(长 whoosh 跨入字幕
    音效帧) → pyJianYingDraft SegmentOverlap 400。v2 双向:
      1. 新起点落在既有段内部 → 整段跳过 (推迟挂载破坏同帧语义, 不如不放)
      2. 既有段起点落在新段内部 → 截断新段结尾 (v1 原逻辑)
    max_sec: 可选时长上限 (如 HF 边界音不超过其文字窗长度)。
    """
    p = sound_path(sound_name)
    if not p:
        logger.warning("[jy_export] 音效缺失, 跳过: %s", sound_name)
        return False
    dur = _probe_duration(p) or 1.0
    start_us = int(round(at_sec * _US))
    end_us = start_us + int(round(dur * _US))
    # 查目标轨已挂段: 双向重叠防护
    try:
        track = script.tracks[track_name]
        for seg in track.segments:
            s0 = seg.target_timerange.start
            if s0 <= start_us < seg.target_timerange.end:
                logger.info(
                    "[jy_export] 音效 %s 起点 %.2fs 落在既有段 [%d,%d] 内, 跳过",
                    sound_name, at_sec, s0, seg.target_timerange.end,
                )
                return False
            if start_us < s0 < end_us:
                end_us = s0
    except (KeyError, AttributeError):
        pass  # 轨道不存在/无法访问 → 保持原时长
    if max_sec is not None:
        end_us = min(end_us, start_us + int(round(max_sec * _US)))
    if end_us - start_us < 100_000:  # <0.1s 无意义, 跳过
        logger.info("[jy_export] 音效 %s 与已挂段重叠过密, 跳过 (%.2fs)", sound_name, at_sec)
        return False
    script.add_segment(
        draft_mod.AudioSegment(
            str(p), trange(start_us, end_us - start_us), volume=volume
        ),
        track_name,
    )
    return True
