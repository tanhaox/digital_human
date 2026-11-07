# -*- coding: utf-8 -*-
"""v3 ⑧ H3 批生视频 (V6b) — v2 h3.run_batch 全量复用 + ⑥ 拼合收口.

盖章后 doc.shots 已是 v2 形状 (h3 直接吃), 本层只做三件事:
1. **渲染前幂等重拼** (1107 批2): ⑥ 后 ④ 手改 → 盖章快照过期 — 用 ⑥ 同一拼合
   函数 stamp.render_fields 重算渲染字段 (camera/station/beats 事件×镜头融合行/
   v3_cam_* 注入参数)。旧私有注入循环 (五字段 attach + camera 词) 退役 —
   拼合点唯一在 ⑥, 镜头句嵌事件行 (官方形态), instruction 顶孤立句废除。
2. 硬闸: ⑦ 已盖章 (gates.stamp) — 时长真相在, 不烧估时视频。
3. GPU 纪律: v3_h3 入 GPU_PHASES, comfy session + 前后 free_memory + 镜间取消。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _reproject(doc: dict[str, Any]) -> int:
    """⑧ 渲染前幂等重拼 (1107 批2): 与 ⑥ 盖章共用 stamp.render_fields —
    拼合点唯一且 ④ 手改后快照不过期 (时效性补遗 2)。品牌镜直通短路不重拼。"""
    from .stamp import render_fields
    direct = (doc.get("v3") or {}).get("direct") or {}
    n = 0
    for s2 in doc.get("shots") or []:
        d = direct.get(str(s2.get("shot_id")))
        if not d or s2.get("brand_card"):
            continue
        anim = s2.setdefault("anim", {})
        for k, v in render_fields(d, float(anim.get("duration_s") or 0)).items():
            if k == "beats":
                anim["beats"] = v
            else:
                s2[k] = v
        n += 1
    return n


def run(job: dict[str, Any], only: set[str] | None = None,
        retry_failed: bool = False) -> None:
    """v3_h3 相位本体: 盖章闸 → 运镜注入 → v2 h3.run_batch。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline import h3 as h3_mod
    from app.services.job_events import _publish

    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.get("v3") or {}
    if not v3.get("stamp"):
        raise SystemExit("⑦ 未盖章 — H3 吃实测时长, 先在 ⑦ 页盖章 (时长真相源)")


    # ⑥ 拼合收口 (1107 批2): 旧注入循环退役 — 渲染字段由 stamp.render_fields
    # 单源拼合 (⑥ 盖章写入 + ⑧ 渲染前幂等重拼, ④ 手改后快照不再过期)
    n_rp = _reproject(doc)
    if n_rp:
        logger.info("[v3:h3] 渲染前重拼 %d 镜 (⑥ 拼合函数同源, 镜头句已嵌事件行)", n_rp)
    # 状态现场同步: ⑥ 页过审写在 v3 槽 (k2.status), 盖章快照可能滞后 —
    # 实锤: 过审后启动批生仍 0 approved (doc.shots.status 停在 img_done)。
    # 1106 四修: 只升不降且不碰视频态 — 旧版按 ⑥ k2 态把 anim_done 打回
    # approved → 每批重烧已成片镜 (GPU 浪费 + 用户"重掷没效果"困惑同源)。
    v3_by_sid = {str(x["sid"]): x for x in (v3.get("shots") or [])}
    n_sync = 0
    for s2 in doc.get("shots") or []:
        v3s = v3_by_sid.get(str(s2.get("shot_id")))
        if not v3s:
            continue
        k2st = str((v3s.get("k2") or {}).get("status") or "")
        want = {"approved": "approved", "img_done": "img_done"}.get(k2st)
        if (want and s2.get("status") in ("planned", "img_done")
                and s2.get("status") != want):
            s2["status"] = want
            n_sync += 1
        if v3s.get("audio_muted") and s2.get("status") in ("approved", "img_done"):
            s2["status"] = "planned"      # 屏弃槽不烧 H3
            n_sync += 1
    if n_sync:
        logger.info("[v3:h3] 状态同步 %d 镜 (⑥过审→快照)", n_sync)

    # 点名重掷语义 (1106 用户实锤: 品牌镜 anim_done 后无重置通道): run_batch
    # 只吃 approved — anim_done/anim_fail 不先失效就是静默空转 (job 秒完 0 镜,
    # 🎲 点了没效果)。点名镜先失效视频 (keep_image 保图) 换 h3_seed 回 approved
    # 再进批; 品牌镜同路 → 批内零 GPU 直通重插定稿资产。
    if only:
        n_reset = 0
        for s2 in doc.get("shots") or []:
            if (str(s2.get("shot_id")) in only
                    and s2.get("status") in ("anim_done", "anim_fail")):
                shots_mod.audit_reroll(doc, s2, "v3点名重掷")
                shots_mod.retry_anim(s2)
                shots_mod.invalidate_media(s2, keep_image=True, to_status="approved",
                                           reason="v3点名重掷")
                n_reset += 1
        if n_reset:
            shots_mod.save(doc)
            logger.info("[v3:h3] 点名重掷: %d 镜失效视频回 approved (换seed)", n_reset)
    _n_ok = sum(1 for x in (doc.get("shots") or []) if x.get("status") == "approved")
    if _n_ok == 0:
        raise SystemExit("无已过审镜 — H3 只吃 approved; 先在 ⑥ K2 页过审 (✅ 或全部过审)")

    logger.info("[v3:h3] ⑥ 拼合字段就绪: %d 镜 (station/beats 融合行/v3_cam_*)", n_rp)

    from app.services.gpu_service_manager import get_gpu_service_manager
    from app.services.anim_pipeline import comfy as comfy_mod
    stop = lambda: job.get("cancel")
    with get_gpu_service_manager().session(
            "comfyui",
            status_callback=lambda msg: _publish(
                job["id"], {"type": "anim_log", "message": str(msg)[:500]})):
        comfy_mod.free_memory()
        try:
            res = h3_mod.run_batch(doc, only=only, retry_failed=retry_failed,
                                   on_shot_done=lambda sid, done, total: job.update(
                                       progress={"done": done, "total": total,
                                                 "current": sid}),
                                   stop_check=stop)
        finally:
            comfy_mod.free_memory()
    job["result"] = res
    logger.info("[v3:h3] %s ep%d: %s → doc.shots ✓", job["book_title"], job["ep"], res)


def patch_clause_prompt(prompt: str, clause: str) -> str:
    """v3 运镜句注入点: instruction 前置官方句 (h3 run_shot 拼装后调用)。

    与 B-CAM1 三补丁正交: 那套替换军规句 (六词通道); 这里在 instruction 顶部
    加 direct.clause (带幅/速的完整官方句), 两句并存不冲突 (同一动作的两种表述,
    H3 以显式句为准)。"""
    if not clause:
        return prompt
    return f"{clause}\n{prompt}"
