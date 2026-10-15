# -*- coding: utf-8 -*-
"""TTS 合成后处理栈 (0930 重构: 自 audio.py _do_tts 外置, 逻辑逐字节照抄迁移)。

顺序铁律: 语速筛(重合成) → 五分区节拍器(模块级 atempo) → 锚定协调(行级) —
筛管重合成 / 节拍管分区档 / 锚管听感微调; 锚最后跑 = 用户锚 (行级最高意志) 后跑后赢。
每段只在自身有变更时落盘 (persist_manifest 条件语义, 禁合并成无条件写)。
scripts.* 一律函数内延迟导入 (app.services 不为 TTS 库付启动成本)。
"""
from __future__ import annotations

import json
import logging
import wave
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

Publish = Callable[[str, dict], None]


def persist_manifest(result: dict, od: Path) -> None:
    """manifest 落盘 + wav 拼接 + 总时长回填 (原三份重复块合一, 条件语义在调用方)。"""
    (od / "manifest.json").write_text(
        json.dumps(result.get("manifest"), ensure_ascii=False, indent=2), encoding="utf-8")
    from scripts.tts_client import _concat_wavs_with_ffmpeg
    _concat_wavs_with_ffmpeg(sorted(od.glob("[0-9][0-9][0-9].wav")), od / "full_paragraph.wav")
    ci = result.get("combined_file") or {}
    try:
        with wave.open(str(od / "full_paragraph.wav"), "rb") as w:
            ci["duration"] = round(w.getnframes() / w.getframerate(), 3)
    except Exception:
        pass


def zone_of_module(name: str, zone_map: list[dict] | None) -> str:
    """模块名 → 功能分区 (Host.pacing.zone_map 数据驱动); 未匹配默认 value。"""
    for r in (zone_map or []):
        if isinstance(r, dict) and any(m in name for m in (r.get("match") or [])):
            return str(r.get("zone"))
    return "value"


def slow_line_filter(result: dict, od: Path, voice, backend: str,
                     publish: Publish, job_tag: str) -> int:
    """行级语速筛 (0912): 字/s 过慢的行自动 0.95 档重合成 (≤6 行)。

    纯确定性 (字数÷时长, 不用 ASR); 0929 筛线锚化 — 有同 zone 锚的行改 锚×0.70
    (真拖行才重合成), 无锚保持 3.5 绝对线。返回重合成行数。
    """
    import subprocess as sp
    from app.config import get_config
    from app.services.pinyin_fix import tts_adapt
    from scripts.tts_lib.orchestrator import _synthesize_single
    from scripts.tts_lib.text import _PAUSE_MARK_ANY_RE
    from app.services.speech_anchor import read_anchor, zone_of

    # 1001 修: 重合成路径剥停顿标记 — manifest 行文本带 -Xs- (拍点层), 语速筛直接
    # 喂引擎会把标记念出来 ("-0.8s-" 被读成语音实锤); reroll 路径有 _split_by_pause_marks
    # 分片不受影响, 唯独此处裸调 _synthesize_single 绕过了 _tts_text 的剥标记步骤
    def _clean_for_synth(t: str) -> str:
        return tts_adapt(_PAUSE_MARK_ANY_RE.sub("", t))

    anchors = read_anchor(voice) if voice else {}
    vp = ((voice.config_json or {}).get("params") or {}) if voice and voice.config_json else {}
    fixed = 0
    off = 0
    for seg in ((result.get("manifest") or {}).get("segments") or []):
        txt = str(seg.get("text") or "")
        if fixed >= 6:
            off += len(txt)
            continue
        han = sum(1 for c in txt if "一" <= c <= "鿿")
        # 1001 修: 速率分母扣气口垫时 — 金句行带 0.8s 拍点后含气口语速必低,
        # 不扣会把每颗金句误判成拖行触发重合成 (标记被念出来实锤的同源病)
        dur = float(seg.get("duration") or 0) - float(seg.get("breath_pause") or 0)
        lim = 3.5
        za = anchors.get(zone_of(off))
        if za and za.get("rate"):
            lim = float(za["rate"]) * 0.70
        if han < 10 or dur <= 0 or han / dur >= lim:
            off += len(txt)
            continue
        wav = od / str(seg.get("file"))
        if not wav.exists():
            continue
        p = dict(vp)
        p["duration_factor"] = 0.95
        p.setdefault("emo_audio_prompt", getattr(get_config().defaults, "indextts25_emo_ref", ""))
        p.setdefault("do_sample", True)
        p.setdefault("top_p", 0.8); p.setdefault("top_k", 30)
        p.setdefault("temperature", 0.8)
        p.setdefault("max_text_tokens_per_segment", 300)
        _synthesize_single(
            text=_clean_for_synth(txt), output_path=wav,
            backend=backend, voice_id=(voice.name if voice else ""),
            reference_audio=None, reference_text="",
            base_url_fish="", base_url_f5="",
            base_url_indextts=(voice.base_url_indextts if voice and voice.base_url_indextts else "http://127.0.0.1:7862"),
            master_audio=(Path(voice.reference_audio_path) if voice and voice.reference_audio_path else None),
            master_text=(voice.master_text or "") if voice else "",
            master_style="calm", params=p)
        tmp = wav.with_suffix(".rs.wav")
        sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(tmp)], check=True)
        tmp.replace(wav)
        with wave.open(str(wav), "rb") as w:
            nd = round(w.getnframes() / w.getframerate(), 3)
        seg["duration"] = nd
        for af in result.get("audio_files") or []:
            if getattr(af, "filename", None) == seg.get("file"):
                af.duration = nd
        fixed += 1
        publish(job_tag, {"type": "tts_service",
                          "message": f"行{seg.get('index', '?')} 语速过慢 ({han/dur:.1f}字/s) → 0.95档重合成 → {han/nd:.1f}字/s ✓"})
    return fixed


def zone_tempo(result: dict, od: Path, mods: list[dict], pacing: dict,
               zone_map: list[dict] | None, publish: Publish, job_tag: str) -> int:
    """五分区节拍器 (0930): 检查语速→对照五分区→atempo 自动调控, 不依赖 TTS。

    模块级整体微调保段内韵律; 基准=收藏点赞区实测语速中位数 (自校准,
    换 voice/换稿不用改数); 偏差<3% 不动, 钳 0.92~1.10。返回调档包数。
    """
    from statistics import median
    from app.services.speech_anchor import apply_atempo

    segs = ((result.get("manifest") or {}).get("segments") or [])
    acc: dict[int, list[float]] = {}
    for s in segs:
        mid = s.get("module_id")
        if mid is None:
            continue
        h = sum(1 for c in str(s.get("text") or "") if "一" <= c <= "鿿")
        d = float(s.get("duration") or 0)
        if h <= 0 or d <= 0:
            continue
        a = acc.setdefault(mid, [0.0, 0.0])
        a[0] += h
        a[1] += d
    rates = {m: h / d for m, (h, d) in acc.items() if d > 0}
    zmap = {m.get("idx"): zone_of_module(str(m.get("name") or ""), zone_map) for m in mods}
    val_r = [r for m, r in rates.items() if zmap.get(m) == "value"]
    base = median(val_r) if val_r else (median(list(rates.values())) if rates else None)
    if not base:
        return 0
    mult = (pacing.get("tempo") or {})  # 分区变速档 (pacing 数据)
    adjusted = 0
    for mid, cur in sorted(rates.items()):
        zone = zmap.get(mid)
        tgt = base * float(mult.get(zone, 1.0))
        f = tgt / cur
        if abs(f - 1) < 0.03:
            continue
        f = max(0.92, min(1.10, f))
        for s in segs:
            if s.get("module_id") != mid:
                continue
            wav = od / str(s.get("file"))
            if not wav.exists():
                continue
            nd = apply_atempo(wav, f)
            s["duration"] = nd
            for af in result.get("audio_files") or []:
                if getattr(af, "filename", None) == s.get("file"):
                    af.duration = nd
            adjusted += 1
        nm = next((str(m.get("name")) for m in mods if m.get("idx") == mid), mid)
        publish(job_tag, {"type": "tts_service",
                          "message": f"五分区节拍: {nm} ×{f:.2f} ({cur:.2f}→{tgt:.2f}字/s, {zone})"})
    return adjusted


def anchor_harmonize(result: dict, od: Path, anchors: dict,
                     publish: Publish, job_tag: str) -> int:
    """批量锚定协调 (0929): 逐行对同 zone 锚 atempo 微调 (带内不动, 只动超带宽行)。

    必须最后跑 — 用户锚是行级最高意志, 后跑后赢。返回调整行数。
    """
    from app.services.speech_anchor import zone_of, harmonize

    harm = 0
    off = 0
    for seg in ((result.get("manifest") or {}).get("segments") or []):
        txt = str(seg.get("text") or "")
        za = anchors.get(zone_of(off))
        if za and za.get("rate"):
            wav = od / str(seg.get("file"))
            if wav.exists():
                v = harmonize(wav, txt, float(seg.get("duration") or 0), float(za["rate"]))
                if v.get("changed"):
                    seg["duration"] = v["duration"]
                    for af in result.get("audio_files") or []:
                        if getattr(af, "filename", None) == seg.get("file"):
                            af.duration = v["duration"]
                    harm += 1
                    publish(job_tag, {"type": "tts_service",
                                      "message": f"行{seg.get('index', '?')} 锚定协调 {v.get('dev', '')} → {float(za['rate']):.2f}字/s ✓"})
        off += len(txt)
    return harm
