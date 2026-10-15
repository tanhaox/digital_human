# -*- coding: utf-8 -*-
"""Evidence Image 线: 素材包真实证据图 → Ken Burns + 图源角标 mp4 (2026-09-04).

对应 _EXECUTION_PHASES 的 P 线 (与 broll_pexels 同 phase, 无 GPU)。
选图/池逻辑在 evidence_service (管线②); 本模块只管成片:

  池内段级匹配选图 → 缓存图校验/重下 → 单次 ffmpeg:
  等比缩放+黑边 (榜单截图不变形) → zoompan Ken Burns (按 kind 分派动效:
  有字图=B拉远 / 实物图=A推近·C平移交替) → 淡入淡出 →
  render_scale_pad 归一化 (补静音轨+30fps, 与 broll 族一致)。
  (动效矩阵 2026-09-05 风格页定稿; 图源角标同日撤除 — 尾部参考来源卡承载出处;
   D 聚焦圈注二期, 需打标协议加数字 bbox)

失败语义: 池空/无匹配/图损坏 → RuntimeError → slot_executor 走
fallback 链降级 broll_pexels (导演闸门之外的执行期兜底)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.infrastructure import render_scale_pad, run_ffmpeg
from app.models import DirectorJob, DirectorSlot, Script
from app.schemas import get_video_format_spec
from app.services.evidence_service import (
    collect_evidence_pool,
    pick_image_for_slot,
    _download_image,
)
from app.services.slot_workflows.common import ensure_slot_dir

logger = logging.getLogger(__name__)

__all__ = ["execute_evidence_image_slot"]


def _collect_used_evidence_urls(db: Session, slot: DirectorSlot) -> set[str]:
    """同 job 已用证据图 URL (写回 params_json.image_url, 仿 _collect_used_pexels_ids).
    按 completed 过滤 + 排除自身, 不按 slot_index 收紧 (跨 phase 同款防护)."""
    rows = (
        db.query(DirectorSlot)
        .filter(
            DirectorSlot.director_job_id == slot.director_job_id,
            DirectorSlot.workflow == "evidence_image",
            DirectorSlot.status == "completed",
        )
        .all()
    )
    used: set[str] = set()
    for r in rows:
        if r.id == slot.id:
            continue
        url = (r.params_json or {}).get("image_url")
        if url:
            used.add(str(url))
    return used


def _count_prior_photo_slots(db: Session, slot: DirectorSlot) -> int:
    """同 job 已完成的实物图 (kind=photo) 证据 slot 数 — 决定本张 A推/C平移 交替轮次。"""
    rows = (
        db.query(DirectorSlot)
        .filter(
            DirectorSlot.director_job_id == slot.director_job_id,
            DirectorSlot.workflow == "evidence_image",
            DirectorSlot.status == "completed",
        )
        .all()
    )
    return sum(
        1 for r in rows
        if r.id != slot.id and (r.params_json or {}).get("image_kind") == "photo"
    )


def _pick_motion(kind: str, photo_turn: int) -> tuple[str, bool]:
    """动效分派 (2026-09-05 风格页定稿, 用户裁决):
    有字图 (benchmark/leaderboard/price/comparison/screenshot) → B 拉远;
    实物图 (photo) → A 推近 / C 平移 按 job 内序交替, 平移方向隔轮镜像。
    D 聚焦圈注 = 二期 (需打标协议加数字 bbox)。返回 (motion, pan_ltr)。"""
    if kind == "photo":
        if photo_turn % 2 == 0:
            return "push", True
        return "pan", (photo_turn // 2) % 2 == 0
    return "pull", True


def _build_vf(width: int, height: int, duration: float,
              motion: str = "pull", pan_ltr: bool = True) -> str:
    """等比缩放+pad → Ken Burns(按 motion) → fade (单 pass, 无中间文件).

    0909 节奏修复 (用户实锤 00:34-00:41 卡): ① 缩放动画只走前 ~1.2s, 之后
    定格在收尾状态 — 旧版 zoom 线性铺满全时长, 文字截图全文只在最后一帧
    达成 = 一闪而过, 前段放大态读不到字; ② 缩放前 2x 上采样 — zoompan 的
    x/y 整数像素取整在原生分辨率上产生阶梯抖动 (慢速拉远尤其明显)。
    平移 (实物图) 保留全程缓移 — 无文本可读诉求, 漂移本身是呼吸感。"""
    dur = max(1.5, duration)
    fps = 25
    frames = int(dur * fps)
    anim_frames = max(2, min(int(1.2 * fps), frames // 2))
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "push":            # EV-A 推近: 实物图, 动画后定格放大态
        step = 0.08 / max(anim_frames - 1, 1)
        zoom = f"if(lt(on,{anim_frames}),min(1+on*{step:.6f},1.08),1.08)"
        x, y = cx, cy
    elif motion == "pan":           # EV-C 平移: 实物图, 定倍横扫 (全程)
        zoom = "1.08"
        span = "(iw-iw/zoom)"
        x = f"{span}*on/{frames}" if pan_ltr else f"{span}*(1-on/{frames})"
        y = cy
    else:                           # EV-B 拉远: 表格/文字类 — 动画后定格全貌
        step = 0.08 / max(anim_frames - 1, 1)
        zoom = f"if(lt(on,{anim_frames}),max(1.08-on*{step:.6f},1.0),1.0)"
        x, y = cx, cy
    # 2x 上采样 (抗 zoompan 整数取整抖动) — 先等比缩进 2 倍画框再补黑边,
    # 榜单/跑分截图绝不能拉伸变形 (数字会糊); zoompan 输出回落到目标分辨率
    up_w, up_h = width * 2, height * 2
    return (
        f"scale={up_w}:{up_h}:force_original_aspect_ratio=decrease,"
        f"pad={up_w}:{up_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"zoompan=z='{zoom}':d={frames}:x='{x}':y='{y}':s={width}x{height}:fps={fps},"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, dur - 0.4):.2f}:d=0.4"
    )


def execute_evidence_image_slot(db: Session, slot: DirectorSlot) -> str:
    """池内选图 → Ken Burns(按 kind 分派动效) 成片, 返回 mp4 路径.

    选图文本 = slot.text_context + params.claim (导演给的核心事实句,
    数字最全); keywords 参与次级加分。无合格图 raise RuntimeError
    → executor 走 fallback 链 (broll_pexels)。
    """
    root = ensure_slot_dir(slot)
    job: DirectorJob = slot.director_job
    spec = get_video_format_spec(job.video_format)
    duration = round(slot.end_sec - slot.start_sec, 3)

    script = db.get(Script, job.script_id)
    pool = collect_evidence_pool(db, script) if script else []
    if not pool:
        raise RuntimeError("证据图池为空: 素材包无合格证据图 (VLM 未标出 is_chart/photo)")

    params = slot.params_json or {}
    search_text = " ".join(x for x in (slot.text_context, params.get("claim")) if x)
    used = _collect_used_evidence_urls(db, slot)
    cand = pick_image_for_slot(search_text, params.get("keywords") or [], pool, used)
    if cand is None:
        raise RuntimeError(
            f"证据图池 {len(pool)} 张无一匹配段落 (数字/关键词都不沾), 走降级"
        )

    img = Path(cand["local_path"])
    if not img.exists():
        # 缓存被清 → 按 URL 重下 (下载器含 Pillow 校验)
        redl = _download_image(cand["url"], img.parent)
        if not redl:
            raise RuntimeError(f"证据图缓存丢失且重下失败: {img.name}")
        img = Path(redl[0])

    # 动效分派 (2026-09-05 定稿): 有字图→B拉远; 实物图→A推/C平移交替
    kind = str(cand.get("kind") or "other")
    photo_turn = _count_prior_photo_slots(db, slot) if kind == "photo" else 0
    motion, pan_ltr = _pick_motion(kind, photo_turn)

    tmp = root / f"evidence_{slot.slot_index:03d}_raw.mp4"
    run_ffmpeg([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(img),
        "-vf", _build_vf(spec["width"], spec["height"], duration, motion, pan_ltr),
        "-t", f"{max(1.5, duration):.2f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
        str(tmp),
    ], timeout=120)

    out_path = root / f"evidence_{slot.slot_index:03d}.mp4"
    render_scale_pad(
        tmp, out_path,
        width=spec["width"], height=spec["height"], duration=duration,
    )
    tmp.unlink(missing_ok=True)  # 中间产物 (build artifact, 红线例外)

    # 写回选图依据 (可观测 + 同 job 去重), params_json 须整体赋值才触发落库
    new_params = dict(slot.params_json or {})
    new_params["image_url"] = cand["url"]
    new_params["image_kind"] = kind
    new_params["motion"] = motion
    new_params["source_media"] = cand.get("source_media") or ""
    new_params["image_desc"] = cand.get("desc_zh") or ""
    slot.params_json = new_params
    db.commit()
    logger.info(
        "[evidence] slot %d -> %s [%s/%s] (%s | %s)",
        slot.slot_index, out_path.name, kind, motion,
        cand.get("source_media"), cand.get("desc_zh"),
    )
    return str(out_path)
