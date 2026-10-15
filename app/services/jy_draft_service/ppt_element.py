# -*- coding: utf-8 -*-
"""元素级剪映多轨草稿 — base 层 + 逐元素透明层 + 免责/角标/字幕轨.

每页 = 1 base 层 (背景+装饰, 无动画) + 逐元素透明 PNG 层 (文字/前景图).
每元素独立 video 轨, 按角色序错峰渐显入场. base 轨页间加转场.
拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft_mod
from pyJianYingDraft import ClipSettings, TextIntro, trange

from app.services.jy_draft_service.auto_choreo import _auto_choreograph
from app.services.jy_draft_service.common import _US, _drafts_dir, _trange_sec
from app.services.jy_draft_service.ppt_timing import _ROLE_PRIORITY
from app.services.jy_draft_service.sfx import _probe_duration
from app.services.jy_draft_service.subtitle_style import (
    _SUBTITLE_SIZE,
    _StyledTextSegment,
)
from app.services.jy_draft_service.subtitle_text import (
    find_highlight_ranges,
    split_subtitle,
    wash_subtitle_text,
)

logger = logging.getLogger(__name__)

__all__ = ["export_element_draft"]


def _caption_bounds_snapped(
    audio_file: str | Path | None,
    chunks: list[str],
    page_dur: float,
) -> list[float] | None:
    """页内字幕时间边界吸附真实语音停顿 (2026-09-02).

    长页 (60-95s) 内字幕按字数均分, 与真实语音停顿不均 → 后半段字幕漂移
    数秒 (拆书 P5 94.8s 页实测)。用 ffmpeg silencedetect 找句间停顿 (≥0.25s),
    把字数比例边界吸附到最近停顿中点 — 句界处字幕与语音严格同步; 吸附不到
    (如逗号断句处无停顿) 保持比例值。页边界 (wav 时长) 本就精确, 不动。
    Returns: 页内相对边界 [0, b1, …, 页末] (len(chunks)+1 个, 严格递增),
    无音频/检测失败/单块 → None (调用方回退字数比例)。
    """
    n = len(chunks)
    if n <= 1 or not audio_file:
        return None
    p = Path(audio_file)
    if not p.exists():
        return None

    import sys
    _root = Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from scripts.tts_lib.silence_split import (
        _detect_silences,
        _silence_candidates,
        _wav_duration,
    )

    dur = _wav_duration(p)
    if dur <= 0:
        return None
    cands = _silence_candidates(_detect_silences(p), dur)
    end = min(page_dur if page_dur > 0 else dur, dur)
    if not cands or end <= 0.3:
        return None

    total = sum(len(c) for c in chunks) or 1
    # 字数比例期望内部边界 (与旧 alloc 同口径)
    exp_bounds: list[float] = []
    cum = 0
    for c in chunks[:-1]:
        cum += len(c)
        exp_bounds.append(cum / total * dur)

    tol = max(0.5, 0.30 * dur / n)
    picked = [0.0]
    prev = 0.0
    for exp in exp_bounds:
        best = None
        for cd in cands:
            if cd <= prev + 0.1:
                continue
            if best is None or abs(cd - exp) < abs(best - exp):
                best = cd
        pick = best if best is not None and abs(best - exp) <= tol else exp
        pick = min(max(pick, prev + 0.15), end - 0.1)
        picked.append(pick)
        prev = pick
    picked.append(max(end, picked[-1] + 0.05))
    return picked

_ELEMENT_ENTRANCE = draft_mod.IntroType.渐显
_BASE_TRANSITION = draft_mod.TransitionType.上移
# 首帧免责字幕最小停留时长 (豆包统一约束: 右上角常驻≥20秒)
_MIN_DISCLAIMER_SEC = 20.0


def _jy_font(resource_id: str):
    """自定义剪映字体 (pyJianYingDraft FontType 枚举没有的, 如思源黑体 ID 6740439840254333443)."""
    _meta = type("FM", (), {"resource_id": resource_id})()
    return type("CF", (), {"value": _meta})()


# 剪映商用字体 (2026-08-21): pyJianYingDraft FontType 覆盖 797 个剪映授权字体, 配置化可随时换
_JY_FONT_BADGE = draft_mod.FontType.孤月体       # 系列角标 (2026-08-22 用户定稿: 孤月体/5/70%)
_JY_FONT_CAPTION = draft_mod.FontType.孤月体      # 台词字幕 (2026-08-21 用户定稿)
_JY_FONT_DISCLAIMER = draft_mod.FontType.孤月体   # 免责 (2026-08-22 用户定稿: 孤月体/5/70%)

# 口播字幕位置 (2026-08-21 v3): 以 director 页【导出剪映草稿】的位置为准 —
#   transform_y=-0.75 (与 export_job_draft 一致, 用户认可该底部字幕位)。
#   v2 曾按错误符号推断改 +0.477 → 字幕跑屏顶; 本版以 director 实测值定稿。
_CAPTION_TRANSFORM_Y = -0.75
# 免责: 剪映面板读数(498, 961) 右上 → transform = 读数/画布全尺寸(1920, 1080)
# = (0.259, 0.890)。2026-08-22 v2: 此前误除剪映显示面板尺寸(2474×1958),
# 读数仍按 transform×画布(1920,1080) 显示 → 免责跑偏到(386,530)。实测校准:
# transform(0.201,0.491) → 剪映读数(386,530) = transform×(1920,1080)。
_DISCLAIMER_TRANSFORM = (0.259, 0.890)
# 角标: 左上. 2026-09-03 实测: 长角标 (~30字×字号5×0.7缩放 ≈ 1000px 宽)
# 以中心点 (-0.501) 锚定时左缘出画布贴边界 → 右移约两字 (~82px = 0.085).
_BADGE_TRANSFORM = (-0.416, 0.890)
# 免责/角标缩放 (2026-08-22 用户定稿: 剪映缩放 70%)
_BADGE_SCALE = 0.7
_DISCLAIMER_SCALE = 0.7

# 白字可读性机制 (2026-08-21): 字幕/角标/免责是白字, 白底页面会看不见 →
# 加深色描边 + 阴影, 任何底色都清晰. 描边/阴影可独立调.
_CAPTION_BORDER = draft_mod.TextBorder(alpha=0.85, color=(0.0, 0.0, 0.0), width=12)
_CAPTION_SHADOW = draft_mod.TextShadow(alpha=0.55, color=(0.0, 0.0, 0.0), diffuse=12, distance=3)
_BADGE_BORDER = draft_mod.TextBorder(alpha=0.80, color=(0.0, 0.0, 0.0), width=10)
_BADGE_SHADOW = draft_mod.TextShadow(alpha=0.50, color=(0.0, 0.0, 0.0), diffuse=10, distance=3)
_DISCLAIMER_BORDER = draft_mod.TextBorder(alpha=0.60, color=(0.0, 0.0, 0.0), width=8)


def _build_caption_track(
    script: Any,
    pages: list[dict],
    width: int,
    height: int,
) -> dict[str, int]:
    """元素草稿字幕轨 (2026-08-21): 每页口播稿 → 底部字幕, 复用 R9 v3.

    逐页 narration 洗读法 → 断句 (横屏≤30字) → 时长按字数比例分到页窗口 →
    每条: 内联划重点(金/红) + TextIntro 动效 + 同帧音效 (与 director 草稿一致).
    """
    stats = {"emphasis": 0, "sfx": 0, "sfx_missing": 0, "sfx_density_skip": 0,
             "anim": 0, "caption_suppressed": 0}
    _last_sfx = [None]
    _first_caption = [True]
    _last_cap_end_us = [0]  # 跨页链: 下页首条字幕起点钳制 (防页起点取整差 1µs 重叠)
    n_text = 0
    max_chars = 18 if height > width else 30

    for pg in pages:
        narration = (pg.get("narration") or "").strip()
        start_sec = float(pg["start_sec"])
        dur_sec = float(pg["duration_sec"])
        if not narration or dur_sec <= 0:
            continue
        washed = wash_subtitle_text(narration)
        chunks = split_subtitle(washed, max_chars)
        total_len = max(len(washed), 1)
        seg_dur_us = int(round(dur_sec * _US))
        # 字幕边界: 优先吸附页音频的真实句间停顿 (2026-09-02, 修后半段漂移),
        # 无页音频/检测失败回退字数比例均分 (旧行为)
        snapped = _caption_bounds_snapped(pg.get("audio_file"), chunks, dur_sec)
        if snapped is not None:
            bounds = snapped
        else:
            alloc = [seg_dur_us * len(c) // total_len for c in chunks]
            alloc[-1] = seg_dur_us - sum(alloc[:-1])
            bounds = []
            cum_us = 0
            for c_us in alloc:
                bounds.append(cum_us / _US)
                cum_us += c_us
            bounds.append(cum_us / _US)
        # 放段: 链式推起点 (start=上段末), 杜绝 start/duration 各自取整产生
        # 1µs 重叠 → SegmentOverlap 整条字幕被静默丢弃 (2026-09-02 实测踩中);
        # 跨页同样防重叠 (页起点取整与上页末尾可能差 1µs)
        page_start_us = int(round(start_sec * _US))
        page_end_us = page_start_us + seg_dur_us
        cursor_us = max(page_start_us, _last_cap_end_us[0])
        for i, chunk in enumerate(chunks):
            want_end_us = (page_start_us + int(round(bounds[i + 1] * _US))
                           if i + 1 < len(chunks) else page_end_us)
            seg_start_us = cursor_us
            chunk_us = want_end_us - cursor_us
            if chunk_us <= 0:
                chunk_us = 1000  # 取整边界重合: 让位 1ms, 链式保证不重叠
            gold, red, anim, anim_ms = _auto_choreograph(
                script, chunk, seg_start_us, stats, _last_sfx, first=_first_caption[0])
            _first_caption[0] = False
            hl = sorted(set(gold + find_highlight_ranges(chunk)))
            rr = sorted(set(red))
            try:
                seg = _StyledTextSegment(
                    chunk,
                    trange(seg_start_us, chunk_us),
                    font=_JY_FONT_CAPTION,
                    border=_CAPTION_BORDER,
                    shadow=_CAPTION_SHADOW,
                    highlight_ranges=hl,
                    red_ranges=rr,
                    clip_settings=ClipSettings(transform_y=_CAPTION_TRANSFORM_Y),
                )
                if anim:
                    seg.add_animation(getattr(TextIntro, anim),
                                      duration=anim_ms * 1000 if anim_ms else None)
                    stats["anim"] += 1
                script.add_segment(seg, "caption")
                n_text += 1
            except Exception as exc:
                logger.warning("[jy_export] 元素稿字幕段失败: %s | %s", chunk[:20], exc)
            cursor_us = seg_start_us + chunk_us
        _last_cap_end_us[0] = max(_last_cap_end_us[0], cursor_us)
    return {"text": n_text, "sfx": stats["sfx"], "emphasis": stats["emphasis"]}


def _add_disclaimer(script: Any, pages: list[dict], disclaimer_text: str) -> int:
    """首帧免责字幕 (2026-08-21): 口播不念, 画面右上角小字, 停留≥20秒 (豆包统一约束).

    字号比字幕(_SUBTITLE_SIZE=5)小两号 (~2.8), 白字半透明, 右上角.
    时长 = max(第一页时长, 20s) — 第一页过短时延续到第二页.
    """
    if not pages or not disclaimer_text:
        return 0
    p1 = pages[0]
    start_us = int(round(float(p1["start_sec"]) * _US))
    dur_us = int(round(max(float(p1["duration_sec"]), _MIN_DISCLAIMER_SEC) * _US))
    try:
        seg = draft_mod.TextSegment(
            disclaimer_text,
            trange(start_us, max(dur_us, 1000)),
            font=_JY_FONT_DISCLAIMER,
            border=_DISCLAIMER_BORDER,
            style=draft_mod.TextStyle(size=5.0, color=(1.0, 1.0, 1.0), alpha=0.85),
            clip_settings=ClipSettings(
                transform_x=_DISCLAIMER_TRANSFORM[0], transform_y=_DISCLAIMER_TRANSFORM[1],
                scale_x=_DISCLAIMER_SCALE, scale_y=_DISCLAIMER_SCALE,
            ),
        )
        script.add_segment(seg, "disclaimer")  # 独立轨, 防与底部字幕同轨重叠
        return 1
    except Exception as exc:
        logger.warning("[jy_export] 首帧免责字幕失败: %s", exc)
        return 0


def _add_series_badge(script: Any, pages: list[dict], badge_text: str, page_indices: list[int]) -> int:
    """系列角标 (2026-08-21): 左上角, '静读书:《书名》第X集，更多请主页观看'.

    在指定页(第2页/末页)全程亮起, 字号=字幕(_SUBTITLE_SIZE=5), 呼吸闪烁(闪烁 循环动画).
    """
    if not badge_text or not pages:
        return 0
    n = 0
    for pi in page_indices:
        if pi < 0 or pi >= len(pages):
            continue
        pg = pages[pi]
        start_us = int(round(float(pg["start_sec"]) * _US))
        dur_us = int(round(float(pg["duration_sec"]) * _US))
        try:
            seg = draft_mod.TextSegment(
                badge_text,
                trange(start_us, max(dur_us, 1000)),
                font=_JY_FONT_BADGE,
                border=_BADGE_BORDER,
                shadow=_BADGE_SHADOW,
                style=draft_mod.TextStyle(size=_SUBTITLE_SIZE, color=(1.0, 1.0, 1.0), alpha=0.95),
                clip_settings=ClipSettings(
                    transform_x=_BADGE_TRANSFORM[0], transform_y=_BADGE_TRANSFORM[1],  # 左上角
                    scale_x=_BADGE_SCALE, scale_y=_BADGE_SCALE,
                ),
            )
            seg.add_animation(draft_mod.TextLoopAnim.闪烁)  # 呼吸闪烁
            script.add_segment(seg, "badge")
            n += 1
        except Exception as exc:
            logger.warning("[jy_export] 系列角标失败: %s", exc)
    return n


def export_element_draft(
    draft_name: str,
    pages: list[dict],
    audio_path: str | Path | None = None,
    *,
    canvas: tuple[int, int] = (1920, 1080),
    stagger: float = 0.30,
    disclaimer: str | None = None,
    book_title: str | None = None,
    ep_index: int | None = None,
    watermark: str | Path | None = None,
    book_brand: str = "静读书",
) -> dict[str, Any]:
    """元素级剪映草稿: 每页 base 层 + 逐元素透明层, 各自 video 轨, 渐显错峰.

    pages: [{
        start_sec: float, duration_sec: float,
        layers: [ {kind: 'base'|'text'|'image', file: str, text?: str, role?: str} ]
    }]
    - base 层 → 'main' 轨 (无入场动画), 页间 上移 转场
    - text/image 层 → e0..eK 轨, 按角色序错峰, 渐显入场 (全画布透明层, 动画安全)
    - 轨道按全稿最大元素数建, 跨页复用 (时序不重叠)
    - watermark (2026-09-03): 透明 PNG 路径 → 'logo' 轨右下角间歇台标
    """
    width, height = canvas
    folder = draft_mod.DraftFolder(str(_drafts_dir()))
    script = folder.create_draft(draft_name, width, height, allow_replace=True)

    # 轨道: voice/sfx 底, main(base) 中, e0..eK 元素, logo 台标, caption 最上 (后来居上)
    max_elements = max((len([l for l in pg.get("layers", []) if l["kind"] != "base"]) for pg in pages), default=0)
    track_specs = [draft_mod.TrackSpec(draft_mod.TrackType.audio, "voice"),
                   draft_mod.TrackSpec(draft_mod.TrackType.audio, "sfx"),
                   draft_mod.TrackSpec(draft_mod.TrackType.video, "main")]
    track_specs += [draft_mod.TrackSpec(draft_mod.TrackType.video, f"e{i}") for i in range(max_elements)]
    track_specs.append(draft_mod.TrackSpec(draft_mod.TrackType.video, "logo"))  # 静姐读书台标 (元素层上, 字幕下)
    track_specs.append(draft_mod.TrackSpec(draft_mod.TrackType.text, "caption"))
    track_specs.append(draft_mod.TrackSpec(draft_mod.TrackType.text, "badge"))  # 系列角标(左上角)
    track_specs.append(draft_mod.TrackSpec(draft_mod.TrackType.text, "disclaimer"))  # 免责(右上角, 独立轨防与字幕重叠)
    script.append_tracks(track_specs)

    # audio 轨: 整段 TTS (若顶层给 audio_path), 或逐页 audio_file 段
    if audio_path and Path(audio_path).exists():
        dur = float(_probe_duration(audio_path) or 0)
        if dur > 0:
            script.add_segment(draft_mod.AudioSegment(str(audio_path), _trange_sec(0, dur)), "voice")

    # 尾卡入场音效 (2026-09-05 拆书线): sfx=title_in 的页 (B-Q1/B-08 尾卡)
    # 挂 title_in 族 whoosh 同帧 — 效仿财经线末卡 (job_draft _TITLE_IN_SOUNDS)
    _card_sfx_i = 0
    try:
        from app.services.jy_draft_service.common import _TITLE_IN_SOUNDS
        from app.services.jy_draft_service.sfx import attach_sound, sound_path
        _title_in_avail = [s for s in _TITLE_IN_SOUNDS if sound_path(s)]
    except Exception:
        _title_in_avail = []

    mat_cache: dict[str, draft_mod.VideoMaterial] = {}
    n_base = n_elem = 0
    n_audio = 0

    def _photo(file: str) -> draft_mod.VideoMaterial:
        m = mat_cache.get(file)
        if m is None:
            m = draft_mod.VideoMaterial(file)
            mat_cache[file] = m
        return m

    for pg in pages:
        start_us = int(round(float(pg["start_sec"]) * _US))
        dur_us = int(round(float(pg["duration_sec"]) * _US))
        layers = pg.get("layers", [])

        # 尾卡入场 whoosh (2026-09-05): 同帧挂 sfx 轨
        if _title_in_avail and pg.get("sfx") == "title_in":
            try:
                attach_sound(script, "sfx", _title_in_avail[_card_sfx_i % len(_title_in_avail)],
                             start_us / _US)
                _card_sfx_i += 1
            except Exception as exc:
                logger.warning("[jy_export] 尾卡音效失败: %s", exc)

        # 逐页音频段 (如有) → voice 轨
        pg_audio = pg.get("audio_file")
        if pg_audio and Path(pg_audio).exists():
            a_dur = float(_probe_duration(pg_audio) or 0)
            if a_dur > 0:
                # 2026-08-22: 实测 a_dur 与 timings 窗口微差 (采样率/舍入) 逐页累积,
                # 靠后的页 a_dur 超出本页窗口 → 与下一段重叠 → 草稿导出崩
                # (SegmentOverlap)。段长钳制到本页窗口 min(a_dur, dur_us), 绝不重叠。
                seg_us = min(int(round(a_dur * _US)), dur_us)
                if seg_us >= 100000:  # ≥0.1s 才放 (防 0 时长段)
                    script.add_segment(
                        draft_mod.AudioSegment(str(pg_audio), trange(start_us, seg_us)),
                        "voice")
                    n_audio += 1

        # base 层 → main 轨
        base_layer = next((l for l in layers if l["kind"] == "base"), None)
        if base_layer:
            mat = _photo(base_layer["file"])
            seg = draft_mod.VideoSegment(mat, trange(start_us, dur_us), volume=0)
            if n_base > 0 and _BASE_TRANSITION is not None:
                seg.add_transition(_BASE_TRANSITION)
            script.add_segment(seg, "main")
            n_base += 1

        # 元素层 → e0..eK: 有口播时序(start_sec)用之, 否则按角色序错峰
        # 2026-09-02: tie-break 从 shape_id 改阅读序 (top,left); 角色由
        # compute_page_timing 入口注入 (_ROLE_PRIORITY 不再空转)
        elems = [l for l in layers if l["kind"] != "base"]
        elems.sort(key=lambda l: (l.get("start_sec", 1e9),
                                  _ROLE_PRIORITY.get(l.get("role", "other"), 4),
                                  float(l.get("top", 0)), float(l.get("left", 0))))
        page_dur_s = max(1.5, float(pg["duration_sec"]))
        n = max(len(elems), 1)
        step = min(stagger, page_dur_s / (n + 1.5))  # 自适应: 页短时压缩错峰
        for idx, l in enumerate(elems):
            if l.get("start_sec") is not None:
                delay_us = int(round(max(0.0, float(l["start_sec"]) - pg["start_sec"]) * _US))
            else:
                delay_us = int(round(idx * step * _US))
            remain_us = dur_us - delay_us
            if remain_us < 200000:  # 至少留 0.2s 动画窗口
                break
            mat = _photo(l["file"])
            seg = draft_mod.VideoSegment(
                mat, trange(start_us + delay_us, max(remain_us, 200000)), volume=0)
            if _ELEMENT_ENTRANCE is not None:
                seg.add_animation(_ELEMENT_ENTRANCE)
            script.add_segment(seg, f"e{idx}")
            n_elem += 1

    # 字幕轨 (每页口播稿, R9 v3 动态字幕 + 同帧音效)
    cap_stats = _build_caption_track(script, pages, width, height)
    # 静姐读书 logo 台标 (2026-09-03): 右下角间歇出现, 防伪+品牌识别
    n_logo = 0
    if watermark:
        from app.services.jy_draft_service.watermark import add_watermark
        total_us = max((int(round((float(pg["start_sec"]) + float(pg["duration_sec"])) * _US))
                        for pg in pages), default=0)
        n_logo = add_watermark(script, total_us, Path(watermark), width, height)
    # 首帧免责字幕 (视觉化, 口播不念)
    n_disclaimer = 0
    if disclaimer:
        n_disclaimer = _add_disclaimer(script, pages, disclaimer)
    # 系列角标 (左上角, 第2页+末页, 呼吸闪烁)
    n_badge = 0
    if book_title:
        badge_text = f"{book_brand}：《{book_title}》第{ep_index or '?'}集，更多请主页观看。"
        if len(pages) > 2:
            n_badge = _add_series_badge(script, pages, badge_text, [1, len(pages) - 1])
        elif pages:
            n_badge = _add_series_badge(script, pages, badge_text, [0])

    script.save()
    draft_dir = _drafts_dir() / draft_name
    result = {
        "draft_name": draft_name,
        "draft_dir": str(draft_dir),
        "canvas": f"{width}x{height}",
        "base_segments": n_base,
        "element_segments": n_elem,
        "audio_segments": n_audio,
        "caption_segments": cap_stats["text"],
        "sfx_segments": cap_stats["sfx"],
        "disclaimer": n_disclaimer,
        "series_badge": n_badge,
        "logo_watermark": n_logo,
        "emphasis_words": cap_stats["emphasis"],
        "max_tracks": 4 + max_elements,
        "audio": bool(audio_path),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    logger.info("[jy_export] 元素级草稿 %s -> %s (base=%d elem=%d caption=%d sfx=%d tracks=%d)",
                draft_name, draft_dir, n_base, n_elem, cap_stats["text"], cap_stats["sfx"],
                4 + max_elements)
    return result
