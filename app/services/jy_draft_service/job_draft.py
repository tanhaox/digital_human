# -*- coding: utf-8 -*-
"""J1 主入口 — 导演 job → 剪映草稿文件夹 (slot→video 轨 + TTS→audio 轨 + 字幕).

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

import pyJianYingDraft as draft_mod
from pyJianYingDraft import ClipSettings, TextIntro, Timerange, trange

from app.models.director import DirectorJob
from app.services.jy_draft_service.auto_choreo import _auto_choreograph
from app.services.jy_draft_service.common import (
    _HF_TEXT_FAMILIES,
    _TITLE_IN_SOUNDS,
    _US,
    _drafts_dir,
    _load_manifest,
    _trange_sec,
)
from app.services.jy_draft_service.sfx import _probe_duration, attach_sound, sound_path
from app.services.jy_draft_service.subtitle_style import (
    _StyledTextSegment,
    _SUBTITLE_COLOR_HF,
)
from app.services.jy_draft_service.subtitle_text import (
    find_highlight_ranges,
    split_subtitle,
    wash_subtitle_text,
)

logger = logging.getLogger(__name__)

__all__ = ["export_job_draft"]


def export_job_draft(db: Session, job_id: str) -> dict[str, Any]:
    """导演 job → 剪映草稿文件夹. 同步执行 (纯 JSON 写盘, <1s).

    Returns: {draft_name, draft_dir, video_segments, audio_segments,
              text_segments, canvas, skipped_slots}
    Raises: ValueError (job 不存在 / 无可导出 slot / 无音频)
    """
    job = db.query(DirectorJob).filter(DirectorJob.id == job_id).first()
    if not job:
        raise ValueError(f"任务不存在: {job_id}")

    slots = [s for s in job.slots if s.status == "completed" and s.output_path]
    slots.sort(key=lambda s: s.slot_index)
    if not slots:
        raise ValueError("没有已完成的 slot, 先执行任务再导出")

    audio_file = job.audio_file
    if not audio_file or not Path(audio_file.file_path).exists():
        raise ValueError("任务没有已合成的音频 (audio_file 缺失)")

    width, height = (1080, 1920) if job.video_format != "landscape" else (1920, 1080)
    name = f"DH_{datetime.now().strftime('%Y%m%d_%H%M')}_{job.script_id[:8]}"
    folder = draft_mod.DraftFolder(str(_drafts_dir()))
    script = folder.create_draft(name, width, height, allow_replace=True)

    # 轨道: 后来居上 — caption 最上(内联划重点), video 中, audio 底
    # 2026-08-17 v2: 砍掉 emph1~3 独立强调轨(与字幕/HF卡高度重合+截断问题),
    # 强调改为字幕内联划重点(加大字号差+变色) — 剪映"智能划重点"的真实做法
    script.append_tracks([
        draft_mod.TrackSpec(draft_mod.TrackType.audio, "voice"),
        draft_mod.TrackSpec(draft_mod.TrackType.audio, "sfx"),
        draft_mod.TrackSpec(draft_mod.TrackType.video, "main"),
        draft_mod.TrackSpec(draft_mod.TrackType.text, "caption"),
    ])

    # ── audio 轨: TTS 分段逐段进轨 (时间轴 = 累计时长), 无 manifest 回退整段 ──
    manifest = _load_manifest(audio_file.file_path)
    # 底稿双轨 (2026-09-10): 字幕读 display.json 底稿真实形 (GPT-6/百分之X),
    # TTS 适配形只进合成; 缺 sidecar / 行数不齐回退 manifest 文 (老产物兼容)。
    _display_lines: list[str] = []
    try:
        import json as _json_disp
        _dp = Path(audio_file.file_path).parent / "display.json"
        if _dp.is_file():
            _display_lines = (_json_disp.loads(
                _dp.read_text(encoding="utf-8")).get("lines")) or []
    except Exception:
        _display_lines = []
    n_audio = 0
    if manifest and manifest.get("segments"):
        cum = 0.0
        for seg in manifest["segments"]:
            dur = float(seg.get("duration") or 0)
            wav = Path(audio_file.file_path).parent / seg["file"]
            if dur > 0 and wav.exists():
                # manifest duration 与实际 wav 有毫秒级漂移 (实测长 25ms), 直用会触发
                # pyJianYingDraft source_timerange 超长校验 → 导出 400。以素材实测
                # 时长 clamp (校验同源, 永不越界); 时间轴仍按 manifest 推进, 毫秒级
                # 缺口为无声间隙不可感知。
                mat_us = int(draft_mod.AudioMaterial(str(wav)).duration)
                take_us = min(int(round(dur * _US)), mat_us)
                script.add_segment(
                    draft_mod.AudioSegment(
                        str(wav),
                        trange(int(round(cum * _US)), take_us),
                        volume=1.0,
                    ),
                    "voice",
                )
                n_audio += 1
            cum += dur
        audio_mode = f"manifest 分段 ×{n_audio}"
    else:
        dur = float(audio_file.duration or _probe_duration(audio_file.file_path) or 0)
        if dur <= 0:
            raise ValueError("音频时长未知且无 manifest, 无法导出")
        script.add_segment(
            draft_mod.AudioSegment(str(audio_file.file_path), _trange_sec(0, dur)),
            "voice",
        )
        n_audio = 1
        audio_mode = "整段 (无 manifest)"

    # ── video 轨: slot 产物按分配时间窗放轨, 静音 (声音归 TTS 轨) ──
    # 音画同步 (2026-08-15): 素材实际时长普遍略短于分配窗口 (毫秒级漂移累积
    # 曾致画面比音轨短 ~1.9s)。素材短 → 微降速拉满分配窗口 (≤15%, 不可感知);
    # 素材长 → 截取前段。素材实例缓存避免同素材多 slot 重复探测。
    skipped: list[int] = []
    n_fx = 0  # J2 effect_recipe 挂载数 (2026-08-27 契约层消费统计)
    mat_cache: dict[str, draft_mod.VideoMaterial] = {}
    hf_windows: list[tuple[int, int]] = []  # 文字承载 HF 窗 (字幕抑制用, v3 独载分工)
    for s in slots:
        if not Path(s.output_path).exists():
            skipped.append(s.slot_index)
            continue
        mat = mat_cache.get(s.output_path)
        if mat is None:
            mat = draft_mod.VideoMaterial(s.output_path)
            mat_cache[s.output_path] = mat
        alloc_us = int(round(s.duration_sec * _US))
        mat_us = int(mat.duration)
        seg: draft_mod.VideoSegment
        if mat_us < alloc_us and mat_us > 0:
            speed = mat_us / alloc_us
            if speed >= 0.85:
                seg = draft_mod.VideoSegment(
                    mat,
                    trange(int(round(s.start_sec * _US)), alloc_us),
                    source_timerange=Timerange(0, mat_us),
                    speed=speed,
                    volume=0,
                )
            else:  # 缺口过大: 循环铺满分配窗口 (2026-08-26 改) — 旧版钳制留黑,
                # 50s 大 slot 配 20s 素材时尾部 ~30s 黑屏 (实测"尾部画面短缺"根因);
                # 素材重复播完即接续, 远好于黑场。配合 parse 层拆超长 slot, 此分支仅为最后兜底。
                logger.warning("[jy_export] slot %d 素材缺口大 (%.2fs/%.2fs), 循环铺满",
                               s.slot_index, mat_us / _US, alloc_us / _US)
                placed_us = 0
                while placed_us < alloc_us:
                    take = min(mat_us, alloc_us - placed_us)
                    script.add_segment(draft_mod.VideoSegment(
                        mat,
                        trange(int(round((s.start_sec * _US) + placed_us)), take),
                        source_timerange=Timerange(0, take),
                        volume=0,
                    ), "main")
                    placed_us += take
                if s.workflow in _HF_TEXT_FAMILIES:
                    ws = int(round(s.start_sec * _US))
                    hf_windows.append((ws, ws + alloc_us))
                continue
        else:
            seg = draft_mod.VideoSegment(
                mat,
                trange(int(round(s.start_sec * _US)), min(alloc_us, mat_us)),
                volume=0,
            )
        script.add_segment(seg, "main")
        # J2 effect_recipe 消费 (2026-08-27): 镜头契约选定的画面特效挂素材段 —
        # 契约层 (shot_contract.py) 从 J2 目录菜单选的名, 此处枚举直通加挂。
        _recipe = ((s.params_json or {}).get("shot_contract") or {}).get("effect_recipe") or {}
        _ve = _recipe.get("video_effect")
        if _ve:
            from app.services.jy_effect_library import pyjyd
            _member = pyjyd(_ve)
            if _member is not None:
                try:
                    seg.add_effect(_member)
                    n_fx += 1
                except Exception as exc:  # noqa: BLE001 — 特效挂不上不挡导出
                    logger.warning("[jy_export] slot %d 特效 %s 挂载失败: %s",
                                   s.slot_index, _ve, exc)
        if s.workflow in _HF_TEXT_FAMILIES:
            ws = int(round(s.start_sec * _US))
            hf_windows.append((ws, ws + alloc_us))

    # ── HF 边界转场音 (v3): 画面切换同帧挂 title_in 族 whoosh, 纯音频不碰文字 ──
    n_boundary = 0
    _title_in_avail = [s for s in _TITLE_IN_SOUNDS if sound_path(s)]
    for i, (ws, we) in enumerate(hf_windows):
        if not _title_in_avail:
            break
        # v2: whoosh 不超过其文字窗长 — 长音效跨窗会与后续字幕音效抢轨
        if attach_sound(script, "sfx", _title_in_avail[i % len(_title_in_avail)],
                        ws / _US, volume=0.9, max_sec=(we - ws) / _US):
            n_boundary += 1

    # ── text 轨: 逐段字幕 (洗 TTS 读法 + 超长断句, 时长按字数比例分配) ──
    # 横屏每屏上限 30 字 (2026-08-15 用户实测超出横屏); 竖屏画面窄取 18。
    max_chars = 18 if height > width else 30
    n_text = 0
    r9_stats = {"emphasis": 0, "sfx": 0, "sfx_missing": 0, "sfx_density_skip": 0,
                "anim": 0, "caption_suppressed": 0, "sfx_boundary": n_boundary}
    _last_sfx = [None]
    _first_caption = [True]
    if manifest and manifest.get("segments"):
        cum = 0.0
        for _seg_i, seg in enumerate(manifest["segments"]):
            dur = float(seg.get("duration") or 0)
            text = (_display_lines[_seg_i].strip() if _seg_i < len(_display_lines)
                    else (seg.get("text") or "").strip())
            if dur > 0 and text:
                washed = wash_subtitle_text(text)
                if washed != text:
                    logger.info("[jy_export] 字幕洗涤: %r → %r", text, washed)
                total_len = max(len(washed), 1)
                chunks = split_subtitle(washed, max_chars)
                seg_start_us = int(round(cum * _US))
                seg_dur_us = int(round(dur * _US))
                # 整数微秒按字数分配, 末条吃余数 — 保证 Σchunk ≤ seg_dur, 不与下段重叠
                alloc = [seg_dur_us * len(c) // total_len for c in chunks]
                alloc[-1] = seg_dur_us - sum(alloc[:-1])
                for chunk, chunk_us in zip(chunks, alloc):
                    # 字幕抑制已撤 (2026-08-26 用户决策): HF 卡是画面补充不是字幕
                    # 替代 — 抑制导致 HF 段整段无字幕(听不清无兜底), 且 HF 时长被
                    # clamp 后窗口错位会误杀邻近字幕。恢复全篇逐句字幕。
                    # R9 v3: 分类拿内联高亮区间 + 动效(动画名) + 同帧音效
                    gold, red, anim, anim_ms = _auto_choreograph(
                        script, chunk, seg_start_us, r9_stats, _last_sfx,
                        first=_first_caption[0])
                    _first_caption[0] = False
                    # 内联划重点: R9 语义区间 + find_highlight_ranges 数字/专名兜底
                    hl = sorted(set(gold + find_highlight_ranges(chunk)))
                    rr = sorted(set(red))
                    try:
                        # HF 窗内字幕换深蓝灰底色 (2026-09-10): 纸墨系卡底与奶油色 #F9F3C4 太近
                        chunk_end_us = seg_start_us + max(chunk_us, 1000)
                        on_hf = any(ws < chunk_end_us and we > seg_start_us
                                    for ws, we in hf_windows)
                        # HF 窗内单色 (2026-09-10 用户令): 划重点金色高亮与卡面文字
                        # 打架, HF 段去高亮; P 线 (broll/证据图) 高亮照旧
                        seg = _StyledTextSegment(
                            chunk,
                            trange(seg_start_us, max(chunk_us, 1000)),
                            highlight_ranges=[] if on_hf else hl,
                            red_ranges=[] if on_hf else rr,
                            base_color=_SUBTITLE_COLOR_HF if on_hf else None,
                            clip_settings=ClipSettings(transform_y=-0.75),
                        )
                        # 动态字幕 v2→v3: 动画挂字幕段本身 (不加层, 零重合)
                        if anim:
                            seg.add_animation(
                                getattr(TextIntro, anim),
                                duration=anim_ms * 1000 if anim_ms else None,
                            )
                            r9_stats["anim"] += 1
                        script.add_segment(seg, "caption")
                        n_text += 1
                    except Exception as exc:  # 单条字幕失败不阻塞
                        logger.warning("[jy_export] 字幕段失败: %s | %s", chunk[:20], exc)
                    seg_start_us += chunk_us
                cum += dur
            else:
                cum += dur

    script.save()
    draft_dir = _drafts_dir() / name
    result = {
        "draft_name": name,
        "draft_dir": str(draft_dir),
        "canvas": f"{width}x{height}",
        "video_segments": len(slots) - len(skipped),
        "audio_segments": n_audio,
        "audio_mode": audio_mode,
        "text_segments": n_text,
        "emphasis_words": r9_stats["emphasis"],
        "sfx_attached": r9_stats["sfx"],
        "sfx_missing": r9_stats["sfx_missing"],
        "sfx_density_skip": r9_stats["sfx_density_skip"],
        "anim_attached": r9_stats["anim"],
        "caption_suppressed": r9_stats["caption_suppressed"],
        "sfx_boundary": r9_stats["sfx_boundary"],
        "fx_attached": n_fx,
        "skipped_slots": skipped,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    logger.info("[jy_export] %s -> %s (%s)", job_id, name, result)
    return result
