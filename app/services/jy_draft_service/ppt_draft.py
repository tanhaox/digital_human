# -*- coding: utf-8 -*-
"""PPT 整页草稿 — 各页 mp4/静态帧 → video 轨 + 整段音频 + 台词字幕.

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft_mod
from pyJianYingDraft import ClipSettings, TextSegment, trange

from app.services.jy_draft_service.common import (
    _JY_PPT_ENTRANCE,
    _JY_PPT_TRANSITION,
    _US,
    _drafts_dir,
    _trange_sec,
)
from app.services.jy_draft_service.sfx import _probe_duration
from app.services.jy_draft_service.subtitle_text import split_subtitle, wash_subtitle_text

logger = logging.getLogger(__name__)

__all__ = ["export_ppt_draft"]


def export_ppt_draft(job_id: str, work_root: str | Path) -> dict[str, Any]:
    """PPT 出片 → 剪映草稿 (2026-08-20): 各页 mp4 → video 轨, 整段音频 → voice 轨,
    每页台词 → caption 字幕轨. 音画不合成, 打开剪映即可审片/调 BGM/导出.

    ppt.py 管线产物: work_root/{job_id}/ppt_manifest.json 含 {clips[], audio}.
    纯 JSON 写盘, <1s. Raises ValueError 缺产物/音频.
    """
    workdir = Path(work_root) / job_id
    manifest_path = workdir / "ppt_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"PPT 产物缺失 (未完成渲染): {job_id}")
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    clips = meta.get("clips") or []
    audio_path = meta.get("audio")
    if not clips:
        raise ValueError("没有已渲染的 PPT 页, 先完成成片再导出")
    if not audio_path or not Path(audio_path).exists():
        raise ValueError("PPT 音频缺失 (audio 未生成)")

    # 校验各页 mp4 存在
    valid_clips = []
    for c in clips:
        p = Path(c["path"])
        if p.exists():
            valid_clips.append(c)
    if not valid_clips:
        raise ValueError("各页 mp4 文件均缺失")

    width, height = 1920, 1080  # PPT 16:9 横屏
    name = f"PPT_{datetime.now().strftime('%Y%m%d_%H%M')}_{job_id[:8]}"
    folder = draft_mod.DraftFolder(str(_drafts_dir()))
    script = folder.create_draft(name, width, height, allow_replace=True)
    script.append_tracks([
        draft_mod.TrackSpec(draft_mod.TrackType.audio, "voice"),
        draft_mod.TrackSpec(draft_mod.TrackType.audio, "sfx"),
        draft_mod.TrackSpec(draft_mod.TrackType.video, "main"),
        draft_mod.TrackSpec(draft_mod.TrackType.text, "caption"),
    ])

    # ── audio 轨: 整段 TTS (无 manifest, 直接整段) ──
    audio_path = Path(audio_path)
    dur = float(_probe_duration(audio_path) or 0)
    if dur <= 0:
        raise ValueError("音频时长未知, 无法导出")
    script.add_segment(
        draft_mod.AudioSegment(str(audio_path), _trange_sec(0, dur)),
        "voice",
    )

    # ── video 轨: 各页静态帧/mp4 按对齐 start_sec 放轨, 静音 (声音归 voice 轨) ──
    # 2026-08-21 剪映分流: 每页 = 1 静态帧 PNG (photo 素材), 入场动画+转场由剪映给,
    # 替代逐帧捕获 (21页真实稿 3h → 秒级截图 + 剪映 GPU 导出).
    # 素材实际时长可能略短于分配时长 (ffmpeg 帧数向下取整) → 用 mat.duration 钳制,
    # 避免 source_timerange 超出素材时长报错 (2026-08-20).
    mat_cache: dict[str, draft_mod.VideoMaterial] = {}
    for idx, c in enumerate(valid_clips):
        p = Path(c["path"])
        mat = mat_cache.get(str(p))
        if mat is None:
            mat = draft_mod.VideoMaterial(str(p))
            mat_cache[str(p)] = mat
        start_us = int(round(float(c.get("start_sec", 0.0)) * _US))
        alloc_us = int(round(float(c.get("duration_sec", 5.0)) * _US))
        mat_us = int(mat.duration) if mat.duration else alloc_us
        use_us = min(alloc_us, mat_us) if mat_us > 0 else alloc_us
        seg = draft_mod.VideoSegment(
            mat, trange(start_us, max(use_us, 100000)), volume=0,
        )
        # 入场动画: 整页渐显 (母本/知识付费稿标配); 首页不加转场
        if _JY_PPT_ENTRANCE is not None:
            seg.add_animation(_JY_PPT_ENTRANCE)
        if idx > 0 and _JY_PPT_TRANSITION is not None:
            seg.add_transition(_JY_PPT_TRANSITION)
        script.add_segment(seg, "main")

    # ── text 轨: 每页台词作字幕 (对齐该页窗口) ──
    n_text = 0
    for c in valid_clips:
        notes = (c.get("notes") or "").strip()
        if not notes:
            continue
        # 洗 TTS 读法 → 阅读文本; 超长断句 (横屏上限 30 字)
        washed = wash_subtitle_text(notes)
        chunks = split_subtitle(washed, 30)
        start_us = int(round(float(c.get("start_sec", 0.0)) * _US))
        dur_us = int(round(float(c.get("duration_sec", 5.0)) * _US))
        total_len = max(len(washed), 1)
        alloc = [dur_us * len(ch) // total_len for ch in chunks]
        alloc[-1] = dur_us - sum(alloc[:-1]) if alloc else dur_us
        seg_start = start_us
        for chunk, chunk_us in zip(chunks, alloc):
            try:
                seg = TextSegment(
                    chunk, trange(seg_start, max(chunk_us, 1000)),
                    clip_settings=ClipSettings(transform_y=-0.75),
                )
                script.add_segment(seg, "caption")
                n_text += 1
            except Exception as exc:
                logger.warning("[jy_export] PPT 字幕段失败: %s | %s", chunk[:20], exc)
            seg_start += chunk_us

    script.save()
    draft_dir = _drafts_dir() / name
    result = {
        "draft_name": name,
        "draft_dir": str(draft_dir),
        "canvas": f"{width}x{height}",
        "video_segments": len(valid_clips),
        "audio_segments": 1,
        "text_segments": n_text,
        "audio_mode": "整段 TTS",
        "skipped_slots": [],
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    logger.info("[jy_export] PPT %s -> %s (%s)", job_id, name, result)
    return result
