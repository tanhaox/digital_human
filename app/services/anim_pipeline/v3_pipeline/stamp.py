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

from .direct import (cam_clause, cam_family, station_for,  # 1106 治根+1107 批2 拼合单源
                     v2_camera_word)

logger = logging.getLogger(__name__)

_PAUSE = re.compile(r"\s*-\d+(?:\.\d+)?s-")


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


def render_fields(d: dict[str, Any], real: float) -> dict[str, Any]:
    """镜渲染段拼合 — **⑥ 唯一拼合点** (1107 批2): ⑥ 盖章写入与 ⑧ 渲染前幂等
    重拼 (v3_pipeline/h3._reproject) 共用此函数, 拼合点唯一且快照不过期。
    吃 ④ 四元组 (+motion_en), 吐 v2 镜渲染字段:
    - camera: v2 机位词 (608 预裁+三补丁链入口)
    - station: 工位执法 (运镜镜锁 h3 — ⑧ _pick_video_station 读此字段)
    - beats: 事件×镜头**融合行** (官方嵌动作流形态, 孤立镜头句=官方证伪形态退役):
      "as {motion_en}, the camera {四要素句}" — 固定镜零融合 (纯 motion_en)
    - v3_cam_*: ⑧ 注入参数 (幅速参数化/族选择, LAW 单源派生)
    """
    cam = d.get("cam") or {}
    motion_en = str(d.get("motion_en") or "")
    clause = cam_clause(cam, motion_en)
    fused = motion_en
    if clause and str(cam.get("kind") or "固定") not in ("", "固定", "待定"):
        fused = clause[:1].lower() + clause[1:].rstrip(".")   # As→as, 去句号进 beats 行
    return {
        "camera": v2_camera_word(cam) or "固定",
        "station": station_for(cam),
        "v3_cam_clause": clause,
        "v3_cam_speed": str(cam.get("speed") or ""),
        "v3_cam_amp": str(cam.get("amp") or ""),
        "v3_cam_family": cam_family(cam),
        "v3_cam_dir": str(cam.get("dir") or ""),
        "beats": [{"t_start": 0, "t_end": real, "motion": fused}],
    }


def project_v2(doc: dict[str, Any], blocks: list[dict]) -> dict[str, Any]:
    """v3 doc + 对齐块 → 回填摘要 (写 doc['shots']/doc['arcs'] 为 v2 投影)。

    幂等: 重复盖章重算覆盖。返回 {slots, muted, total_s, missing_audio}。
    """
    v3 = doc["v3"]
    shots = v3.get("shots") or []
    direct = v3.get("direct") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    # 批3 vocab: 弧词表 (空表 = 零执法, 存量集零扰动)
    arc_vocab = {str(a.get("arc_id")): (a.get("vocab") or [])
                 for a in (v3.get("arcs") or [])}
    vocab_bad: dict[str, list[str]] = {}
    aligned = align_blocks(shots, lines, blocks)
    # 1106 五修 (redo 实锤): 投影重建 doc.shots 丢 video_file → 一次列队重做把
    # 整本成片引用抹光 (54 mp4 成孤儿)。搬运规则: 图未变 (旧 image_file == 新)
    # 的镜携带 H3 生命周期 (video_file/anim 态/video_station/attempts.h3);
    # 图变了 (重做换图) = 视频作废, 从 k2 态重新投影。
    prev = {str(x.get("shot_id")): x for x in (doc.get("shots") or [])}

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
        rf = render_fields(d, real)      # ⑥ 拼合: camera/station/beats 融合行/注入参数
        # ⑥ 契约闸 (批3): zh 词 ∈ keyframe_zh ⇒ en 词 ∈ keyframe_en (弧词表
        # 非空才执法; 品牌镜豁免 — 冻结骨架不经 LLM)
        if not s.get("brand_card"):
            for v in arc_vocab.get(str(s.get("arc_id") or "")) or []:
                if (v["zh"] in str(s.get("keyframe_zh") or "")
                        and v["en"] not in str(s.get("keyframe_en") or "")):
                    vocab_bad.setdefault(sid, []).append(v["zh"])
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
            "h3_seed": s.get("h3_seed") or __import__(
                "app.services.anim_pipeline.shots", fromlist=["new_seed"]).new_seed(),
            "camera": rf["camera"],
            # ⑥ 拼合收口 (1107 批2): 工位执法 + ⑧ 注入参数随镜固化 (渲染器无关契约)
            "station": rf["station"],
            "v3_cam_clause": rf["v3_cam_clause"],
            "v3_cam_speed": rf["v3_cam_speed"],
            "v3_cam_amp": rf["v3_cam_amp"],
            "v3_cam_family": rf["v3_cam_family"],
            "v3_cam_dir": rf["v3_cam_dir"],
            "register": s.get("register"),
            "brand_card": bool(s.get("brand_card")),
            "political_flag": None, "gate_rejects": [], "gate_exempt": False,
            "image_file": k2.get("image"),
            "status": status,
            "attempts": {"k2": 1 if k2.get("image") else 0, "h3": 0},
            "anim": {"opening_desc": s.get("keyframe_en") or "",
                     # v2 h3.py build_prompt 硬索引此键 (scene_writer.py:668 同值)
                     "physical_lock": "the camera viewpoint, the main structural "
                                      "elements and layout",
                     "duration_s": real,
                     # 事件×镜头融合行 (⑥ 拼合): "as {motion_en}, the camera …" —
                     # 官方嵌动作流形态, ⑧ build_prompt 的 From…to 行原样投影
                     "beats": rf["beats"]},
        })
        # 视频生命周期搬运 (图未变才搬; 图变=重做, 旧视频作废)
        old = prev.get(sid)
        if (old and old.get("video_file") and old.get("image_file")
                and old["image_file"] == k2.get("image")):
            v2_shots[-1]["video_file"] = old["video_file"]
            if old.get("status") in ("anim_done", "anim_fail"):
                v2_shots[-1]["status"] = old["status"]
            if old.get("video_station"):
                v2_shots[-1]["video_station"] = old["video_station"]
            v2_shots[-1]["attempts"]["h3"] = int(
                (old.get("attempts") or {}).get("h3") or 0)
    doc["shots"] = v2_shots
    # v2 弧投影 (arc_thesis 吃 metaphor_core)
    doc["arcs"] = [{"arc_id": a.get("arc_id"),
                    "metaphor_core": a.get("metaphor") or "",
                    "visual_metaphor": a.get("world") or ""}
                   for a in (v3.get("arcs") or [])]
    return {"slots": stamped,
            "muted": sum(1 for s in shots if s.get("audio_muted")),
            "total_s": round(t, 1),
            "vocab_mismatch": vocab_bad,
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
    # 批3 vocab 契约闸: 实体 zh 上桌而 en 没上桌 = 同实体跨镜漂词 → 打回补译
    # (空词表零执法 — 存量集不受扰; 品牌镜在 project_v2 内已豁免)
    if out["vocab_mismatch"]:
        bad = "; ".join(f"{sid}:{','.join(zs[:2])}" for sid, zs
                        in list(out["vocab_mismatch"].items())[:4])
        raise SystemExit(f"⑥ 词表闸: {len(out['vocab_mismatch'])} 镜实体译名漂移 "
                         f"({bad}) — ④ 页补译 (词表 zh 已入画, en 未入译) 后再盖章")
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
