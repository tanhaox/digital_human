# -*- coding: utf-8 -*-
"""J 线原生 HF 试验 — 纯文字卡不再渲染视频, 导出时直接生成剪映文字层 (2026-08-27).

试验对象: job e024500a (小米玄戒, 横屏 250s, 37 slots, 14 个 HF 槽位).
方案 ("分层移交" 第一步):
  hf_opening/hf_title/hf_quote (8 槽) → 暗底 PNG + J 线原生文字拍
      (字号/颜色查 jy_style_palette, 行宽查 jy_layout_constraints,
       动画↔音效查 jy_anim_sfx_cooccurrence — 三 config 首次实战消费)
  hf_chart (6 槽) → 照旧 HF 渲染视频 (图表绘制留在代码层)
  broll/字幕/TTS → 照旧 (export_job_draft 同款逻辑)

对比: 导演台原版导出草稿 (DH_*) vs 本草稿《J线原生HF_e02450》。
用法: python scripts/demo_jy_native_hf.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config, set_config

set_config(load_config())
from app.database import init_db, get_session_maker

init_db("sqlite:///data/pipeline.db")
db = get_session_maker()()

import pyJianYingDraft as d
from pyJianYingDraft import ClipSettings, TextSegment, trange

from app.models.director import DirectorJob
from app.services.jy_draft_service import (
    _CREDIT_BG,
    _US, _probe_duration, _StyledTextSegment, _auto_choreograph, attach_sound,
    find_highlight_ranges, sound_path, split_subtitle, wash_subtitle_text,
)

JOB_ID = "e024500a-f472-404a-a342-c4c3145fd504"
DRAFT_NAME = "J线原生HF_e02450_v10"

# 字号体系 v5 (2026-08-27): 字幕 5 是产线用户定稿标准 (2026-08-15 "美观字号5"), 不动.
# 标题以字幕为参照系收敛: 主标 = 字幕×2 档出头, 开场钩子×2.5 强调.
SZ = {"hero": 13, "hot": 8, "sub": 7,            # 开场卡
      "title": 11, "subtitle": 7.5,              # 标题卡
      "quote": 9, "credit": 6}                   # 引用卡

CARD_ANIMS = ["放大", "开幕", "折叠", "跃进", "轻微放大", "向上滑动", "雪光模糊", "模糊发光"]
SUB_ANIMS = ["向上滑动", "渐显", "向右露出", "轻微放大"]

import app.services.jy_draft_service.subtitle_style as _subtitle_style
_subtitle_style._SUBTITLE_SIZE = 5.0   # 回滚产线标准 (v3/v4 误改; 拆包后须改到定义模块)
_CAP_MAX_CHARS = 30
W, H = 1920, 1080  # 横屏
ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config"
SFX = ROOT / "data" / "jy_sounds"

# ── 三 config (挖掘产物首次消费) ──
PAL = json.loads((CFG / "jy_style_palette.json").read_text(encoding="utf-8"))
LAY = json.loads((CFG / "jy_layout_constraints.json").read_text(encoding="utf-8"))
OCC = json.loads((CFG / "jy_anim_sfx_cooccurrence.json").read_text(encoding="utf-8"))
# HF 边界转场音 (产线 export_job_draft 同款): title_in 族轮换, 视频类 HF 卡换入时挂
_TITLE_IN = json.loads((CFG / "jy_sound_semantics.json").read_text(encoding="utf-8")
                       ).get("categories", {}).get("title_in", {}).get("sounds", [])

GOLD = (1.0, 0.96, 0.54)
WHITE = (1.0, 1.0, 1.0)
F_TITLE = d.FontType.得意黑  # 挖掘主力字体 ×1051

DRAFTS = Path(r"C:\Users\tanhaox\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft")
DARK_BASE = ROOT / "data" / "jy_mining" / "dark_base_ls.png"

# 三色板 (style_palette 消费示例: 引文金/正文白)
MAX_DENSITY = 0.114  # landscape p99 (layout_constraints)


def split_by_width(text: str, size: float) -> str:
    """按行宽约束拆行: 每行字数 ≤ 画布宽×p99/字号."""
    max_chars = max(4, int(W * MAX_DENSITY / size))
    if len(text) <= max_chars:
        return text
    lines, cur = [], ""
    for ch in text:
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def stack_ys(sizes_lines: list[tuple[float, int]], gap: float = 0.05,
             center: float = 0.05) -> list[float]:
    """按行高精确堆叠: [(字号, 行数)] → 各块中心 y (半屏高单位, 整体居中于 center).

    行高 ≈ 字号×11px (中文); y 单位 = 半屏高 540px。首版 y 手排致主副标相叠的修复。
    """
    heights = [sz * 11.0 * lines / 540.0 for sz, lines in sizes_lines]
    total = sum(heights) + gap * (len(heights) - 1)
    ys, cursor = [], center + total / 2
    for h in heights:
        ys.append(round(cursor - h / 2, 3))
        cursor -= h + gap
    return ys


def pick_sfx(anim: str | None) -> str | None:
    """共现矩阵: 动画 → 音效族里第一个本地存在的."""
    if not anim:
        return None
    for cand in OCC["anims"].get(anim, []):
        if sound_path(cand["sfx"]):
            return cand["sfx"]
    return None


def ensure_dark_base() -> Path:
    if DARK_BASE.exists():
        return DARK_BASE
    DARK_BASE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i",
        "gradients=s=1920x1080:c0=#151a22:c1=#05070c:x0=0:y0=0:x1=1919:y1=1079",
        "-frames:v", "1", str(DARK_BASE),
    ], check=True)
    return DARK_BASE


def _tr(sec: float, dur: float):
    return trange(int(round(sec * _US)), int(round(dur * _US)))


def main() -> None:
    job = db.query(DirectorJob).filter(DirectorJob.id == JOB_ID).first()
    if not job:
        raise SystemExit(f"job 不存在: {JOB_ID}")
    slots = sorted([s for s in job.slots if s.status == "completed" and s.output_path],
                   key=lambda s: s.slot_index)
    audio = job.audio_file
    manifest = json.loads((Path(audio.file_path).parent / "manifest.json").read_text(encoding="utf-8"))

    dark = ensure_dark_base()
    folder = d.DraftFolder(str(DRAFTS))
    script = folder.create_draft(DRAFT_NAME, W, H, allow_replace=True)
    script.append_tracks([
        d.TrackSpec(d.TrackType.audio, "voice"),
        d.TrackSpec(d.TrackType.audio, "sfx"),
        d.TrackSpec(d.TrackType.video, "main"),
        d.TrackSpec(d.TrackType.text, "card0"),  # HF 卡主文字层
        d.TrackSpec(d.TrackType.text, "card1"),  # HF 卡副文字层
        d.TrackSpec(d.TrackType.text, "card2"),
        d.TrackSpec(d.TrackType.text, "caption"),
    ])

    # ── voice 轨: TTS 分段 (照旧) ──
    cum = 0.0
    for seg in manifest["segments"]:
        dur = float(seg.get("duration") or 0)
        wav = Path(audio.file_path).parent / seg["file"]
        if dur > 0 and wav.exists():
            script.add_segment(d.AudioSegment(str(wav), _tr(cum, dur)), "voice")
        cum += dur

    def add_card(text, at, until, *, sz, c, y, anim, ams=500, sfx_on_entry=True, font=F_TITLE,
                 bg=None):
        """一拍卡文字: 动画 + 共现矩阵同帧音效. bg=TextBackground (如署名条 _CREDIT_BG)."""
        try:
            seg = TextSegment(split_by_width(text, sz), _tr(at, until - at),
                              font=font, style=d.TextStyle(size=sz, color=c),
                              background=bg,
                              clip_settings=ClipSettings(transform_y=y))
            if anim:
                seg.add_animation(getattr(d.TextIntro, anim), duration=ams * 1000)
            script.add_segment(seg, f"card{n_card[0] % 3}")
            n_card[0] += 1
        except Exception as exc:
            print(f"[卡文字失败] {text[:12]}: {exc}")
        if sfx_on_entry and anim:
            sfx = pick_sfx(anim)
            if sfx and anim in OCC.get("typewriter_anims", []):
                attach_sound(script, "sfx", sfx, at, volume=0.85, max_sec=ams / 1000 + 0.15)
            elif sfx:
                attach_sound(script, "sfx", sfx, at, volume=0.85)

    n_card = [0]
    _hf_win = [0]
    stats = {"native": 0, "chart_kept": 0, "broll": 0, "refs_kept": 0}
    dark_mat = d.VideoMaterial(str(dark))
    for s in slots:
        rc = (s.params_json or {}).get("render_config") if isinstance(s.params_json, dict) \
            else (json.loads(s.params_json or "{}").get("render_config") or {})
        start, end = s.start_sec, s.start_sec + s.duration_sec
        wf = s.workflow
        # R20 尾卡识别: 内容来源/长列表 → 列表排版走 HF 渲染 (分层移交边界, 与图表同类)
        is_refs = wf == "hf_title" and (
            rc.get("style") == "references" or "来源" in (rc.get("title") or "")
            or len(rc.get("subtitle") or "") > 60)
        if wf in ("hf_opening", "hf_title", "hf_quote") and not is_refs:
            anim = CARD_ANIMS[stats["native"] % len(CARD_ANIMS)]
            sub_anim = SUB_ANIMS[stats["native"] % len(SUB_ANIMS)]
            # ── J 线原生: 暗底 video 段 + 文字拍 ──
            script.add_segment(d.VideoSegment(dark_mat, _tr(start, s.duration_sec), volume=0), "main")
            if wf == "hf_opening":
                ys = stack_ys([(SZ["hero"], 1), (SZ["hot"], 1), (SZ["sub"], 1)], center=0.1)
                add_card(rc.get("hero", ""), start + 0.3, end, sz=SZ["hero"], c=WHITE, y=ys[0],
                         anim=anim, ams=600)
                add_card(rc.get("hot", ""), start + 1.0, end, sz=SZ["hot"], c=GOLD, y=ys[1],
                         anim=sub_anim)
                add_card(rc.get("sub", ""), start + 1.5, end, sz=SZ["sub"], c=WHITE, y=ys[2],
                         anim="渐显", sfx_on_entry=False)
            elif wf == "hf_title":
                ys = stack_ys([(SZ["title"], 1), (SZ["subtitle"], 1)])
                add_card(rc.get("title", ""), start + 0.3, end, sz=SZ["title"], c=WHITE, y=ys[0],
                         anim=anim)
                add_card(rc.get("subtitle", ""), start + 1.0, end, sz=SZ["subtitle"], c=GOLD, y=ys[1],
                         anim=sub_anim)
            elif wf == "hf_quote":
                q = rc.get("quote", "")
                n_lines = len(split_by_width(q, SZ["quote"]).split("\n"))
                ys = stack_ys([(SZ["quote"], n_lines), (SZ["credit"], 1)])
                # 引文全程在场 (v6: 提前消失致"引文/署名分两屏"), 署名压轴同屏 1.2s
                add_card(q, start + 0.3, end, sz=SZ["quote"], c=GOLD, y=ys[0],
                         anim="打字光标", ams=int(min(2000, (s.duration_sec - 1.5) * 1000)))
                add_card(f"—— {rc.get('name','')} · {rc.get('role','')}", start + s.duration_sec - 1.2,
                         end, sz=SZ["credit"], c=(0.05, 0.05, 0.05), y=ys[1], anim="渐显",
                         sfx_on_entry=False, bg=_CREDIT_BG)
            stats["native"] += 1
        else:
            # ── broll / hf_chart / references尾卡: 照旧放素材 ──
            if not Path(s.output_path).exists():
                continue
            mat = d.VideoMaterial(s.output_path)
            mat_us, alloc_us = int(mat.duration), int(s.duration_sec * _US)
            if mat_us < alloc_us and mat_us > 0 and mat_us / alloc_us >= 0.85:
                seg = d.VideoSegment(mat, _tr(start, s.duration_sec),
                                     source_timerange=d.Timerange(0, mat_us),
                                     speed=mat_us / alloc_us, volume=0)
            else:
                # 注意: min() 已是微秒, 直接传 (勿再换算) — 同产线 export_job_draft
                take_us = min(alloc_us, mat_us or alloc_us)
                seg = d.VideoSegment(mat, trange(int(round(start * _US)), max(take_us, 500000)),
                                     volume=0)
            script.add_segment(seg, "main")
            if is_refs:
                stats["refs_kept"] += 1
            elif wf == "hf_chart":
                stats["chart_kept"] += 1
            else:
                stats["broll"] += 1
            # HF 边界转场音 (v7: 产线同款, 视频类 HF 卡换入 whoosh — 原生卡不挂, 防双声)
            if is_refs or wf == "hf_chart":
                avail = [x for x in _TITLE_IN if sound_path(x)]
                if avail:
                    attach_sound(script, "sfx", avail[_hf_win[0] % len(avail)], start,
                                 volume=0.9, max_sec=s.duration_sec)
                    _hf_win[0] += 1

    # ── caption 轨: 全篇字幕 + R9 编排 (照旧, 与产线 export_job_draft 同款) ──
    n_text = 0
    r9 = {"emphasis": 0, "sfx": 0, "sfx_missing": 0, "sfx_density_skip": 0, "anim": 0,
          "caption_suppressed": 0}
    _last_sfx = [None]
    _first = [True]
    cum = 0.0
    for seg in manifest["segments"]:
        dur = float(seg.get("duration") or 0)
        text = (seg.get("text") or "").strip()
        if dur > 0 and text:
            washed = wash_subtitle_text(text)
            chunks = split_subtitle(washed, _CAP_MAX_CHARS)
            total = max(len(washed), 1)
            seg_us = int(dur * _US)
            alloc = [seg_us * len(c) // total for c in chunks]
            alloc[-1] = seg_us - sum(alloc[:-1])
            t0 = int(cum * _US)
            for chunk, cu in zip(chunks, alloc):
                gold, red, anim, ams = _auto_choreograph(script, chunk, t0, r9, _last_sfx,
                                                         first=_first[0])
                _first[0] = False
                hl = sorted(set(gold + find_highlight_ranges(chunk)))
                try:
                    ts = _StyledTextSegment(chunk, trange(t0, max(cu, 1000)),
                                            highlight_ranges=hl, red_ranges=sorted(set(red)),
                                            clip_settings=ClipSettings(transform_y=-0.75))
                    if anim:
                        ts.add_animation(getattr(d.TextIntro, anim),
                                         duration=ams * 1000 if ams else None)
                        r9["anim"] += 1
                    script.add_segment(ts, "caption")
                    n_text += 1
                except Exception as exc:
                    print(f"[字幕失败] {chunk[:16]}: {exc}")
                t0 += cu
        cum += dur

    script.save()
    print(f"\n✅ 《{DRAFT_NAME}》→ {DRAFTS}")
    print(f"   HF 原生文字卡 {stats['native']} | hf_chart 照旧 {stats['chart_kept']} | "
          f"broll {stats['broll']} | 字幕 {n_text} | 尾卡照旧 {stats["refs_kept"]}")
    print("   对比看: 打开草稿, HF 标题段 = 暗底+原生动画文字 (剪映里可直接改字)")


if __name__ == "__main__":
    main()
