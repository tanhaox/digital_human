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

# v3 运镜型 → v2 camera 词 (B-CAM1 六词实测通道; 盖章投影同表)
_CAM = {"固定": "", "推": "拉近", "变焦推": "拉近", "拉": "拉远", "变焦拉": "拉远",
        "弧": "拉近"}
_CAM_DIR = {("摇", "左"): "左摇", ("摇", "右"): "右摇", ("摇", "上"): "上摇",
            ("摇", "下"): "下摇", ("移", "左"): "左摇", ("移", "右"): "右摇",
            ("跟", "左"): "左摇", ("跟", "右"): "右摇",
            ("升降", "上"): "上摇", ("升降", "下"): "下摇"}


def _cam_word(cam: dict[str, Any]) -> str:
    kind = str((cam or {}).get("kind") or "固定")
    if kind in _CAM:
        return _CAM[kind]
    return _CAM_DIR.get((kind, str((cam or {}).get("dir") or "")), "")


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
        w = _cam_word(cam)
        if w:
            s["camera"] = w
        clause = str(d.get("clause") or "").strip()
        if clause and str(cam.get("kind") or "固定") not in ("", "固定", "待定"):
            s["v3_cam_clause"] = clause       # run_shot prompt 拼接点 (instruction 首行)
            n_clause += 1
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
