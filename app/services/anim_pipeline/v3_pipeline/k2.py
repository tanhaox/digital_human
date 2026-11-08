# -*- coding: utf-8 -*-
"""v3 ⑤ K2 批生图 (V6 薄壳) — v3 镜投影成 v2 形状, 复用 k2.run_shot 全套闸.

不 fork 产线: 单镜渲染/文字守门/判决回流/主色注入/OCR 检字 全在 k2.run_shot 里,
这里只做三件事:
1. 投影: v3 shot → v2 形状 ({shot_id, keyframe_zh/en, seed, arc_id, brand_card,
   attempts, status}); doc.arcs ← v3.arcs (metaphor_core ← metaphor, arc_thesis 同构)。
2. seed 落盘 (ID-133): 首跑随机+持久化; 单镜重掷强制换 seed。
3. 状态回写 v3 命名空间 (k2: {image, status, seed, text_suspect}) — v2 的
   doc["shots"] 不动 (双轨零扰动)。

GPU 纪律: 与 v2 相位同款 — comfyui session + 前后 free_memory + 镜间 stop_check。
前置闸: ④ 放行冻结 (keyframe_en 只在 finalize 后存在; 未冻结跑 = 白烧 GPU 出废图)。
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger(__name__)


def _project_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """v3 doc → k2 视图: arcs 投影 (arc_thesis 吃 metaphor_core), 其余原样。"""
    v3 = doc.get("v3") or {}
    import copy
    d = copy.copy(doc)                       # 浅拷贝 — 不污染真 doc 的顶层键
    d["arcs"] = [{"arc_id": a.get("arc_id"),
                  "metaphor_core": a.get("metaphor") or a.get("narrative")}
                 for a in (v3.get("arcs") or [])]
    d["_ledger_trigger"] = "v3_k2"
    return d


def _project_shot(s: dict[str, Any], seed: int) -> dict[str, Any]:
    """v3 镜 → v2 形状 (k2.run_shot 的输入契约)。"""
    return {
        "shot_id": s["sid"],
        "arc_id": s.get("arc_id"),
        "keyframe_zh": s.get("keyframe_zh") or "",
        "keyframe_en": s.get("keyframe_en") or "",
        "in_image_text": [],                 # v3 停用图内字通道 (用户令)
        "seed": seed,
        "brand_card": bool(s.get("brand_card")),
        "register": s.get("register"),
        "political_flag": None,
        "gate_rejects": [],
        "gate_exempt": False,
        "extra_loras": s.get("extra_loras"),
        "attempts": {"k2": 0, "h3": 0},
        "status": "planned",
    }


def run(job: dict[str, Any], only: set[str] | None = None,
        force_new_seed: bool = False) -> None:
    """v3_k2 相位本体: 批跑 (或 only 单镜重掷) v3 镜的 K2 生图。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline import k2 as k2_mod
    from app.services.anim_pipeline import brand as brand_mod
    from app.services.job_events import _publish

    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    shots = v3.get("shots") or []
    if not shots:
        raise SystemExit("未切镜 — 先跑 ③ 分镜文字")
    if not v3.get("frozen_at"):
        raise SystemExit("④ 未放行 — K2 吃 keyframe_en, 先在 ④ 页确认放行 (英文化批)")
    # 1106 用户令 (⑤TTS→⑥K2): 音频包按槽就位才烧卡 — TTS 改动便宜先定稿,
    # 屏弃/改字在 GPU 前定型 (块数==未屏弃槽数; 旧段落包/编辑中不就位)
    if not job.get("_k2_audio_checked"):
        from app.services.anim_draft import fetch_episode_audio
        _n_slots = sum(1 for x in shots if not x.get("audio_muted"))
        try:
            _blocks = len(fetch_episode_audio(job["book_id"], job["ep"]).get("files") or [])
        except Exception as _exc:  # noqa: BLE001
            raise SystemExit(f"⑤ TTS 未就位 (无音频包: {_exc}) — 先按槽重生成")
        if _blocks != _n_slots:
            raise SystemExit(f"⑤ TTS 未定稿 (音频块 {_blocks} ≠ 未屏弃槽 {_n_slots}) — "
                             "屏弃/编辑/按槽重生成完成后再烧 K2")
    if only is not None:
        force_new_seed = True          # 点名 = 重掷语义, 强制换 seed (实锤: 旧 seed 复用 → 同图)

    # 显式点名 (only) = 重掷语义: 已有图也要跑 (换 seed);
    # 无 only = 批跑语义: 只补缺图。实锤: 旧过滤把点名镜也按"只补缺"排除 → 重掷静默空转。
    todo = [s for s in shots
            if not s.get("audio_muted")          # 屏弃槽不生图 (音频没了, 图白烧)
            and ((only is not None and s["sid"] in only)
                 or (only is None and not ((s.get("k2") or {}).get("image"))))]
    _brand_only = [s["sid"] for s in todo if s.get("brand_card")]
    if _brand_only and only is not None:
        raise SystemExit(f"{'/'.join(_brand_only)} 是品牌位 (冻结资产直通, 零 LLM 零 GPU) — 不可重掷")
    total = len(todo)
    job["progress"] = {"done": 0, "total": total, "current": "K2 批生图 (GPU 独占)"}

    from app.services.gpu_service_manager import get_gpu_service_manager
    from app.services.anim_pipeline import comfy as comfy_mod
    pdoc = _project_doc(doc)
    # 1108 用户令 (Y1「GPU 零等待」收口): 书级 EN 串批前预热 — v3 镜 keyframe_en
    # 全原生零镜级 LLM, 但书级配方串 (look/风格头尾/角色纪律/弧主旨) 惰性首译
    # 曾在 s1 gate→comfy 间停 26s (ep6 实锤) → 全压批前, GPU 循环零 LLM
    try:
        _nw = k2_mod._warm_book_en(
            pdoc, {str(s.get("arc_id") or "") for s in todo if s.get("arc_id")})
        if _nw:
            logger.info("[k2-en] 书级串预热 ×%d — GPU 循环零 LLM (Y1 收口)", _nw)
    except Exception:  # noqa: BLE001 — 预热失败回退渲染时惰性首译 (旧行为)
        logger.warning("[k2-en] 书级串预热失败 (回退惰性)", exc_info=True)
    ok = fail = 0
    with get_gpu_service_manager().session(
            "comfyui",
            status_callback=lambda msg: _publish(
                job["id"], {"type": "anim_log", "message": str(msg)[:500]})):
        comfy_mod.free_memory()              # 相位边界卫生 (v2 同款)
        try:
            for i, s in enumerate(todo):
                if job.get("cancel"):
                    logger.info("[v3:k2] 协作取消: %d/%d 已处理", ok + fail, total)
                    break
                sid = s["sid"]
                job["progress"] = {"done": i, "total": total, "current": sid}
                # seed 落盘 (ID-133): 首跑随机持久化; 重掷换新
                prev = (s.get("k2") or {}).get("seed") or s.get("seed")
                seed = random.randint(1, 2 ** 31 - 1) if (force_new_seed or not prev) \
                    else int(prev)
                try:
                    v2shot = _project_shot(s, seed)
                    if v2shot["brand_card"]:
                        v2shot["image_file"] = brand_mod.resolve(
                            doc["book_title"], doc["ep"], "keyframe")
                        k2_done = True
                    else:
                        k2_mod.run_shot(v2shot, pdoc)
                        k2_done = bool(v2shot.get("image_file"))
                    if k2_done:
                        s["k2"] = {"image": v2shot["image_file"], "status": "img_done",
                                   "seed": seed,
                                   "text_suspect": bool(v2shot.get("text_suspect"))}
                        s["seed"] = seed
                        # 1108 v2 机制继承: 生图次数镜级计数 (redo 清 k2 键不清它
                        # → 跨重做累计; ⑥ 页卡底「N 试」)
                        s["k2_runs"] = int(s.get("k2_runs") or 0) + 1
                        ok += 1
                        logger.info("[v3:k2] %s ✓ (%d/%d)", sid, ok, total)
                    else:
                        s["k2"] = {"status": "failed", "seed": seed,
                                   "error": str(v2shot.get("error") or "图未产出")[:300]}
                        fail += 1
                except Exception as exc:  # noqa: BLE001 — 单镜失败不断批 (v2 同款)
                    s["k2"] = {"status": "failed", "seed": seed, "error": f"k2: {exc}"[:300]}
                    fail += 1
                    logger.error("[v3:k2] %s 失败: %s", sid, exc)
                shots_mod.save(doc)         # 每镜落盘 (断点续跑)
        finally:
            comfy_mod.free_memory()          # 0923 用户令: 批完释放显存

    job["result"] = {"ok": ok, "fail": fail, "total": total,
                     "suspect": sum(1 for s in todo if (s.get("k2") or {}).get("text_suspect"))}
    logger.info("[v3:k2] %s ep%d: %d ok / %d fail → shots.json v3 ✓",
                job["book_title"], job["ep"], ok, fail)
