# -*- coding: utf-8 -*-
"""v3 ⑦ 盖章 (V5) — 实测时长灌入 + v2 字段回填 (确定性, 零 LLM 零 GPU).

槽位机制 (1106) 后的简化: 块[i]↔槽[i] 1:1 (锚对齐), 盖章不再按字数分摊 —
每槽真实时长 = 其音频块时长, 直接查表。

产物:
1. v3.shots[sid] 增 t_start/t_end/dur_real (实测秒数)。
2. **v2 字段回填** doc["shots"] = v3 投影 (可退回通道: v2 工坊/K2/H3 直接接管)。
3. doc["arcs"] 投影 metaphor_core (v2 arc_thesis 同构)。
4. v3.stamp 戳 + gates.stamp 开闸 (⑧ H3 前置)。

硬闸: ④ 已放行 + 音频块数 == 未屏弃槽数 (锚对齐失配即拒, 不猜)。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

_PAUSE = re.compile(r"\s*-\d+(?:\.\d+)?s-")

# v3 运镜型 → v2 camera 枚举 (CAMERA_ENUM: 空拍|平视|低角度|俯瞰|特写|缓推|
# 固定|左摇|右摇|上摇|下摇|拉近|拉远 — v2 H3 壳的词表)
_CAM_MAP = {
    "固定": "固定", "推": "缓推", "变焦推": "缓推", "拉": "拉远", "变焦拉": "拉远",
    "弧": "缓推",
}
_CAM_DIR = {("摇", "左"): "左摇", ("摇", "右"): "右摇", ("摇", "上"): "上摇",
            ("摇", "下"): "下摇", ("移", "左"): "左摇", ("移", "右"): "右摇",
            ("跟", "左"): "左摇", ("跟", "右"): "右摇",
            ("升降", "上"): "上摇", ("升降", "下"): "下摇"}


def _cam_of(cam: dict[str, Any]) -> str:
    kind = str((cam or {}).get("kind") or "固定")
    if kind in _CAM_MAP:
        return _CAM_MAP[kind]
    return _CAM_DIR.get((kind, str((cam or {}).get("dir") or "")), "固定")


def _first_line(text: str) -> str:
    t = str(text or "").strip()
    return t.splitlines()[0].strip() if t else ""


def align_blocks(shots: list[dict], lines: dict[int, dict],
                 blocks: list[dict]) -> dict[str, dict]:
    """槽↔块锚对齐 (块首行=槽首行, 剥气口标记)。返回 {sid: block}。"""
    def key(text: str) -> str:
        return _PAUSE.sub("", _first_line(text)).strip()[:12]
    bmap = {}
    for b in blocks:
        k = key(b.get("text"))
        if k:
            bmap[k] = b
    out: dict[str, dict] = {}
    for s in shots:
        refs = s.get("line_refs") or []
        first = lines.get(refs[0], {}) if refs else {}
        k = key(first.get("text"))
        blk = bmap.get(k)
        if blk is not None:
            out[str(s["sid"])] = blk
    return out


def project_v2(doc: dict[str, Any], blocks: list[dict]) -> dict[str, Any]:
    """v3 doc + 对齐块 → 回填摘要 (写 doc['shots']/doc['arcs'] 为 v2 投影)。

    幂等: 重复盖章重算覆盖。返回 {slots, muted, total_s, missing_audio}。
    """
    v3 = doc["v3"]
    shots = v3.get("shots") or []
    direct = v3.get("direct") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    aligned = align_blocks(shots, lines, blocks)

    t = 0.0
    v2_shots: list[dict[str, Any]] = []
    stamped = 0
    for s in shots:
        sid = str(s["sid"])
        blk = aligned.get(sid)
        real = round(float(blk.get("dur_s") or 0), 2) if blk else None
        s["t_start"] = round(t, 2)
        if s.get("audio_muted") or real is None:
            s["t_end"] = s["t_start"]
            s["dur_real"] = None
            continue
        s["dur_real"] = real
        s["t_end"] = round(t + real, 2)
        t = s["t_end"]
        stamped += 1
        d = direct.get(sid) or {}
        k2 = s.get("k2") or {}
        motion_en = str(d.get("motion_en") or "") or ""
        status = "approved" if k2.get("status") == "approved" else (
            "img_done" if k2.get("status") == "img_done" else "planned")
        # 字幕源 (⑧ 装配 caption): 槽内各行按时长占比切片, 镜内相对时间
        # (循环变量命名避开外层累计时间 t — 曾用 t 作循环变量把它覆写成 str 实锤)
        line_txts = [str(lines.get(no, {}).get("text") or "").strip()
                     for no in (s.get("line_refs") or [])
                     if str(lines.get(no, {}).get("text") or "").strip()]
        _w = [len(x) for x in line_txts] or [1]
        _tw = sum(_w)
        text_layer, _acc = [], 0.0
        for _txt, _wi in zip(line_txts, _w):
            dur_i = real * _wi / _tw
            text_layer.append({"kind": "caption", "text": _txt,
                               "t_start": round(_acc, 2),
                               "t_end": round(_acc + dur_i, 2)})
            _acc += dur_i
        v2_shots.append({
            "shot_id": sid, "arc_id": s.get("arc_id"),
            "t_start": s["t_start"], "t_end": s["t_end"],
            "narration": "\n".join(str(lines.get(no, {}).get("text") or "").strip()
                                   for no in (s.get("line_refs") or [])
                                   if str(lines.get(no, {}).get("text") or "").strip()),
            "keyframe_zh": s.get("keyframe_zh") or "",
            "keyframe_en": s.get("keyframe_en") or "",
            "in_image_text": [], "text_layer": text_layer,
            "seed": s.get("seed") or k2.get("seed"),
            "camera": _cam_of(d.get("cam")),
            "register": s.get("register"),
            "brand_card": bool(s.get("brand_card")),
            "political_flag": None, "gate_rejects": [], "gate_exempt": False,
            "image_file": k2.get("image"),
            "status": status,
            "attempts": {"k2": 1 if k2.get("image") else 0, "h3": 0},
            "anim": {"opening_desc": s.get("keyframe_en") or "",
                     "duration_s": real,
                     "beats": [{"t_start": 0, "t_end": real, "motion": motion_en}]},
        })
    doc["shots"] = v2_shots
    # v2 弧投影 (arc_thesis 吃 metaphor_core)
    doc["arcs"] = [{"arc_id": a.get("arc_id"),
                    "metaphor_core": a.get("metaphor") or "",
                    "visual_metaphor": a.get("world") or ""}
                   for a in (v3.get("arcs") or [])]
    return {"slots": stamped,
            "muted": sum(1 for s in shots if s.get("audio_muted")),
            "total_s": round(t, 1),
            "missing_audio": [str(s["sid"]) for s in shots
                              if not s.get("audio_muted")
                              and str(s["sid"]) not in aligned]}


def run(job: dict[str, Any]) -> None:
    """v3_stamp 相位本体: 硬闸 → 对齐 → 回填 → 戳。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_draft import fetch_episode_audio

    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    shots = v3.get("shots") or []
    if not shots:
        raise SystemExit("未切镜 — 先跑 ③ 分镜文字")
    if not v3.get("frozen_at"):
        raise SystemExit("④ 未放行 — 盖章锁结构, 先在 ④ 页确认放行")
    n_live = sum(1 for s in shots if not s.get("audio_muted"))
    try:
        audio = fetch_episode_audio(job["book_id"], job["ep"])
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"⑤ TTS 未就位 (无音频包: {exc}) — 先按槽重生成")
    blocks = [b for b in (audio.get("files") or [])]
    if len(blocks) != n_live:
        raise SystemExit(f"⑤ TTS 未定稿 (音频块 {len(blocks)} ≠ 未屏弃槽 {n_live}) — "
                         "屏弃/编辑/重生成完成后再盖章")

    job["progress"] = {"done": 0, "total": 1, "current": "盖章: 查表灌秒 + v2 回填"}
    out = project_v2(doc, blocks)
    if out["missing_audio"]:
        raise SystemExit(f"锚对齐失配 {len(out['missing_audio'])} 槽 "
                         f"({','.join(out['missing_audio'][:5])}) — ⑤ 页检查改字漂移")
    try:
        from app.services.anim_draft import audio_stamp
        doc["audio_stamp"] = audio_stamp(doc, audio)
    except Exception:  # noqa: BLE001 — 老函数签名不合不阻断
        pass
    v3["stamp"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "slots": out["slots"], "muted": out["muted"],
                   "total_s": out["total_s"], "job_id": audio.get("job_id")}
    v3.setdefault("gates", {})["stamp"] = True
    shots_mod.save(doc)
    job["result"] = out
    logger.info("[v3:stamp] %s ep%d: %d 槽盖章, 总时长 %.1fs, 屏弃跳过 %d → v2 回填 ✓",
                job["book_title"], job["ep"], out["slots"], out["total_s"], out["muted"])
