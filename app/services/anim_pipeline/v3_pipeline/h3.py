# -*- coding: utf-8 -*-
"""v3 ⑧ H3 批生视频 (V6b) — v2 h3.run_batch 全量复用 + v3 运镜句注入.

盖章后 doc.shots 已是 v2 形状 (h3 直接吃), 本层只做三件事:
1. v3 运镜三元组 → 镜级 camera 词 (v2 枚举) + 运镜英文句 (官方措辞) 注入:
   direct.clause 是官方句 ("The camera pans left with large amplitude at slow
   speed.") — 原样拼进 I2VA instruction, 与 B-CAM1 三补丁同路但带幅/速。
2. 硬闸: ⑦ 已盖章 (gates.stamp) — 时长真相在, 不烧估时视频。
3. GPU 纪律: v3_h3 入 GPU_PHASES, comfy session + 前后 free_memory + 镜间取消。

运镜实现选择 (1106 拍板): 摇/移/跟/升降/弧 等新类型 → camera 词映射到最近的
v2 运镜词 (B-CAM1 通道, 已实测); 幅/速差异靠 direct.clause 官方句进 prompt
(instruction 首行), 与 v2 三补丁机制正交 — v2 镜 (无 clause) 零变化。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 1106 治根: 运镜执行映射单源 (direct.CAM_EXEC) — 旧 _CAM/_CAM_DIR 本地拷贝
# 与 stamp 已漂 (推→缓推 vs 拉近), 两处投影谁后跑谁说了算。
from .direct import cam_family, v2_camera_word


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


    # v3 运镜 → 镜级 camera (仅当 v2 camera 为空时; 盖章已投影基础词, 这里补
    # 精确方向词) + clause 挂到 shot 供 prompt 注入
    direct = v3.get("direct") or {}
    n_clause = 0
    for s in doc.get("shots") or []:
        d = direct.get(str(s.get("shot_id"))) or {}
        cam = d.get("cam") or {}
        w = v2_camera_word(cam)
        if w:
            s["camera"] = w
        clause = str(d.get("clause") or "").strip()
        if clause and str(cam.get("kind") or "固定") not in ("", "固定", "待定"):
            s["v3_cam_clause"] = clause       # run_shot prompt 拼接点 (instruction 首行)
            s["v3_cam_speed"] = str(cam.get("speed") or "")  # 运镜令速度词参数化
            s["v3_cam_amp"] = str(cam.get("amp") or "")      # 运镜令幅度词参数化
            s["v3_cam_family"] = cam_family(cam)             # 执行族 (track/arc 对症句)
            s["v3_cam_dir"] = str(cam.get("dir") or "")      # 跟镜行进方向词
            n_clause += 1
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

    logger.info("[v3:h3] 运镜注入: %d 镜带官方句", n_clause)

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
