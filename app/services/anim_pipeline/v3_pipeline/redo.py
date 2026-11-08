# -*- coding: utf-8 -*-
"""v3 列队重做 (1106 用户令: v2 购物篮 cartRedo/cartRedoImg 对应位).

流程: LLM 重设计画面句 (带弧上下文+重做原因) → 文字层质检 → 单镜重译英文
(冻结戳按镜保鲜) → 清 K2 状态换 seed → 转 v3_k2 重生图 → (非 to_image) 自动过审。

GPU 纪律: v3_redo 注册进 GPU_PHASES (LLM 段+GPU 段同 job, 与 v2 redo 同构)。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_REDO_SYS = """你是动画分镜导演。给定若干镜 (镜号|当前画面句|当前主体运动|视觉世界|本弧讲什么|重做原因),
重写每镜的画面句 keyframe_zh 与主体运动 motion — 两者必须同源 (画面句画什么, motion 只动什么)。

铁律 (与 ③ 生成时同尺, 违反即打回):
1. 画面句 80-200 字: 主体 + 动作前一刻静态构图 + 环境/光源/景深。
2. 动画起点帧, 禁终态词。
3. 画面零文字: 载体只写「空白XX」; 禁「大字/字幕」。
4. 零引号; 继承弧的视觉世界; 与旧句构图不同 (重做=换方案, 不是微调)。
5. 零运镜词 (1106 s2 大乱套实锤): 禁 镜头/机位/跟拍/平移/推近/拉远/摇镜/摇摄/
   升降/航拍/俯拍/仰拍/变焦/慢动作 — 运镜归导演层, 画师只画定格; 景别词可用。
6. 单场景一瞬: 一镜只画一个场所的一个定格瞬间, 禁场景串联。
7. 高续写引力姿势不进画面句 (③ 规则12 同尺): 禁捏笔欲写/持械欲击 — 选中性稳定态,
   动作交给 motion 正向描述。
8. 角色外观继承 (③ 规则13 同尺, 1108 批A-5): 旧句里的角色外观词 (服装色/发型/
   眼镜有无) 原样保留不换不漂; 不得给角色新增五官特征 (旧句无眼镜就不画眼镜,
   无胡须就不画胡茬) — 外观漂移=废片级不一致。
9. 手部镜袖口锚 (③ 规则14 同尺, 1108 批A-5): 画面句出现手/手部特写时必须写明
   袖口或前臂来处 (颜色接旧句角色服装, 如: 深红色冲锋衣的袖口与前臂), 禁孤立
   悬空手 — 图上腕部无来处=视频全程断手。
10. motion = 主体+动作+起承节奏 一句 (只动画面句里已有的实体, 禁添画面外动物/器物)。
严格 JSON: {"shots":[{"sid":"s1","keyframe_zh":"...","motion":"..."}]}"""


def redesign(doc: dict[str, Any], sids: list[str], note: str = "") -> dict[str, dict]:
    """LLM 重设计选中镜的画面句+事件层 (1107 批5: 两者同源 — 只换画面句不换
    motion = 图与事件脱节, s4 新图无笔而事件层仍抬笔实锤)。
    返回 {sid: {"zh": 新画面句, "motion": 新主体运动}} (只含成功镜)。"""
    from app.services.llm_service import get_llm_service, _parse_json
    v3 = doc.get("v3") or {}
    shots = {s["sid"]: s for s in (v3.get("shots") or [])}
    arcs = {a["arc_id"]: a for a in (v3.get("arcs") or [])}
    direct = v3.get("direct") or {}
    rows = []
    for sid in sids:
        s = shots.get(sid)
        if not s:
            continue
        a = arcs.get(str(s.get("arc_id") or "")) or {}
        why = note or str((s.get("k2") or {}).get("error") or "")[:80] or "画面不满意, 换构图方案"
        bd = " [卡底:口播叠大字卡,必须无人物空镜/简单静物]" if s.get("card_backdrop") else ""
        rows.append(f'{sid}|{str(s.get("keyframe_zh") or "")[:160]}'
                    f'|{str((direct.get(sid) or {}).get("motion") or "")[:80]}'
                    f'|{str(a.get("world") or "")[:24]}'
                    f'|{str(a.get("narrative") or "")[:30]}|{why}{bd}')
    if not rows:
        return {}
    raw = get_llm_service().chat(
        _REDO_SYS, "\n".join(rows), model="pro", temperature=0.7,
        response_format={"type": "json_object"}, tag="v3_redo")
    out: dict[str, dict] = {}
    for r in (_parse_json(raw).get("shots") or []):
        sid = str(r.get("sid") or "")
        zh = str(r.get("keyframe_zh") or "").strip()
        mv = str(r.get("motion") or "").strip()
        if sid in shots and len(zh) >= 60 and mv:
            out[sid] = {"zh": zh[:600], "motion": mv[:120]}
    return out


def retranslate(doc: dict[str, Any], sids: list[str]) -> int:
    """单镜批重译英文 (冻结数据按镜保鲜): keyframe_en/opening_desc/motion_en/light_en。"""
    from app.services.anim_pipeline.v3_pipeline.direct import _EN_SYS
    from app.services.llm_service import get_llm_service, _parse_json
    v3 = doc.get("v3") or {}
    shots = {s["sid"]: s for s in (v3.get("shots") or [])}
    direct = v3.get("direct") or {}
    todo = [shots[sid] for sid in sids if sid in shots]
    if not todo:
        return 0
    # 批3 vocab: 弧词表映射同列注入 (与 finalize 同口径, 单镜重译不漂词)
    arc_vocab = {str(a.get("arc_id")): (a.get("vocab") or [])
                 for a in (v3.get("arcs") or [])}
    rows = [f'{s["sid"]}|{str(s.get("keyframe_zh") or "")[:400]}'
            f'|{str((direct.get(s["sid"]) or {}).get("motion") or "")[:100]}'
            f'|{str((direct.get(s["sid"]) or {}).get("light") or "")[:60]}'
            f'|{"、".join(v["zh"] + "=" + v["en"] for v in arc_vocab.get(str(s.get("arc_id") or "")) or [])}'
            for s in todo]
    raw = get_llm_service().chat(_EN_SYS, "\n".join(rows), model="pro", temperature=0.2,
                                 response_format={"type": "json_object"}, tag="v3_en")
    got = {str(r.get("sid")): r for r in (_parse_json(raw).get("shots") or [])}
    n = 0
    for s in todo:
        r = got.get(s["sid"])
        if r and r.get("keyframe_en"):
            s["keyframe_en"] = str(r["keyframe_en"])[:800]
            s["opening_desc"] = s["keyframe_en"]
            d = direct.get(s["sid"])
            if d is not None:
                if r.get("motion_en"):
                    d["motion_en"] = str(r["motion_en"])[:400]
                # 批4 光影槽: 与 finalize 同口径 (h3 opening 行尾软接线)
                if r.get("light_en"):
                    d["light_en"] = str(r["light_en"])[:200]
            n += 1
    return n


def _mark_redo_review_v3(job: dict[str, Any], sids: list[str], want: str) -> None:
    """1108 v2 机制继承 (用户令「重渲标记 v3 没继承」): 列队重做成功镜打 R 标。

    v2 语义 (anim_service._mark_redo_review): to_image 线认 img_done (图是新的),
    全链线认 anim_done (视频是新的); 独立文件 redo_review.json 不进 shots.json,
    不吃集锁 — 看片/过审随时可清。"""
    from app.services import anim_service
    from app.services.anim_pipeline import shots as shots_mod
    try:
        doc = shots_mod.load_doc(job["book_title"], job["ep"])   # 只读, 批已收队
        v3s = {s["sid"]: s for s in (doc.get("v3") or {}).get("shots") or []}
        v2s = {str(s.get("shot_id")): s for s in doc.get("shots") or []}
        flags = anim_service._load_review_flags(job["book_title"], job["ep"])
        hit = 0
        for sid in sids:
            if want == "img_done":
                okk = (v3s.get(sid) or {}).get("k2", {}).get("status") == "img_done"
            else:
                okk = (v2s.get(sid) or {}).get("status") == "anim_done"
            if okk:
                flags[sid] = True
                hit += 1
        if hit:
            anim_service._save_review_flags(job["book_title"], job["ep"], flags)
        logger.info("[v3:redo] R 标: %d/%d 镜打待复查 (认 %s)", hit, len(sids), want)
    except Exception as exc:  # noqa: BLE001 — 打标失败不污染任务结果
        logger.warning("[v3:redo] R 标失败 (忽略): %s", exc)


def run(job: dict[str, Any], sids: list[str], to_image: bool = True,
        note: str = "", to_video: bool = False) -> None:
    """v3_redo 相位本体: 重设计 → 重译 → 清K2状态 → k2.run(only, 换seed) →
    默认停 img_done; to_video (⑧页列队重做) = 自动过审 + 重投影 + 转 H3 全链。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.v3_pipeline import k2 as v3k2
    from app.services.anim_pipeline.v3_pipeline.shots import (keyframe_camera_issues,
                                                              text_layer_issues)

    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    shots = v3.get("shots") or []
    if not shots:
        raise SystemExit("未切镜 — 先跑 ③ 分镜文字")

    job["progress"] = {"done": 0, "total": 3, "current": "LLM 重设计画面句"}
    new_zh = redesign(doc, sids, note)
    if not new_zh:
        raise SystemExit("重设计未产出 (LLM 全拒) — 检查镜号")
    by_sid = {s["sid"]: s for s in shots}
    direct = v3.get("direct") or {}

    def _apply(got: dict[str, dict]) -> None:
        """画面句+事件层同源落库 (批5: motion 随画面句走)。"""
        for sid, g in got.items():
            by_sid[sid]["keyframe_zh"] = g["zh"]
            d = direct.get(sid)
            if d is not None:
                d["motion"] = g["motion"]

    _apply(new_zh)

    # 文字层质检 + 运镜词闸 (改动镜): 不过 → 带反馈补投一次
    iss = text_layer_issues(shots)
    cam_hit = {sid: keyframe_camera_issues(by_sid[sid].get("keyframe_zh"))
               for sid in new_zh}
    bad = [sid for sid in new_zh if iss.get(sid) or cam_hit[sid]]
    if bad:
        job["progress"] = {"done": 1, "total": 3, "current": f"质检补投 {len(bad)} 镜"}
        parts = []
        for sid in bad:
            if iss.get(sid):
                parts.append(f"{sid}: {str(iss[sid][0])[:40]}")
            if cam_hit[sid]:
                parts.append(f"{sid}: 画面句混运镜词 {'/'.join(cam_hit[sid])}"
                             " — 只画定格, 运镜归导演层")
        fb = "; ".join(parts)
        fix = redesign(doc, bad, note=f"上一版不达标: {fb}")
        _apply(fix)

    job["progress"] = {"done": 1, "total": 3, "current": f"重译英文 {len(new_zh)} 镜"}
    n_en = retranslate(doc, list(new_zh))

    # 清 K2 状态 (k2.run 只补缺图 → 清了才重生) + 抛弃旧 seed
    for sid in new_zh:
        by_sid[sid].pop("k2", None)
        by_sid[sid].pop("seed", None)
    shots_mod.save(doc)
    logger.info("[v3:redo] 重设计 %d 镜, 重译 %d → 转 K2", len(new_zh), n_en)

    v3k2.run(job, only=set(new_zh), force_new_seed=True)

    if not to_video:
        # 1106 用户令: ⑤ 页只到生图 (视频归 ⑧ 页) — 重做完停 img_done 走人检,
        # 不自动过审; to_image 参数保留兼容但本页语义恒为真。
        _mark_redo_review_v3(job, list(new_zh), "img_done")   # 1108 R 标
        job["result"] = {"redesigned": len(new_zh), "retranslated": n_en,
                         "to_image": True}
        return

    # ── ⑧ 页列队重做全链 (用户令: 对齐 v2 购物篮 cartRedo 语义) ──
    # 1106 六修 (实锤): k2.run 自载 doc 自存盘 — 本函数开工时的对象已陈旧,
    # 直接续写会把 k2 成果覆掉 (s37: 23:08:58,867 k2 存盘 → ,879 被陈旧覆写
    # 丢引用)。全链腿必须重载后操作。
    from app.services.anim_draft import fetch_episode_audio
    from app.services.anim_pipeline.v3_pipeline import h3 as v3h3
    from app.services.anim_pipeline.v3_pipeline import stamp as v3stamp

    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    by_sid = {s["sid"]: s for s in (v3.get("shots") or [])}
    job["progress"] = {"done": 2, "total": 3, "current": "重盖章投影 → 自动过审"}
    audio = fetch_episode_audio(job["book_id"], job["ep"])
    blocks = [b for b in (audio.get("files") or [])]
    for sid in new_zh:
        sk = by_sid.get(sid)
        if sk is not None and (sk.get("k2") or {}).get("image"):
            sk["k2"]["status"] = "approved"      # 全链: 重生完即过审, 同 v2 cartRedo
    proj = v3stamp.project_v2(doc, blocks)       # 重投影: 新图/新seed/新画面句灌 v2
    if proj["missing_audio"]:
        raise SystemExit(f"锚对齐失配 {len(proj['missing_audio'])} 槽 — ⑤ 页检查后再试")
    if proj["vocab_mismatch"]:                   # 批3 词表闸同口径 (⑥ 唯一拼合点执法)
        raise SystemExit(f"⑥ 词表闸: {len(proj['vocab_mismatch'])} 镜实体译名漂移 "
                         "— ④ 页补译后再列队重做")
    shots_mod.save(doc)
    logger.info("[v3:redo] 列队重做全链: %d 镜重投影过审 → 转 H3", len(new_zh))
    v3h3.run(job, only=set(new_zh))
    _mark_redo_review_v3(job, list(new_zh), "anim_done")      # 1108 R 标 (全链)
    job["result"] = {"redesigned": len(new_zh), "retranslated": n_en,
                     "to_video": True}
