# -*- coding: utf-8 -*-
"""shot_contract — slot 镜头契约层 .

来源: docs/refs/hell-grind-aigc-skill-zh 的 12 段镜头契约, 按新闻线口播+B-roll
形态裁成 7 段 (角色/表演/对白/空间轴线段裁掉 — 无剧情角色; continuity 并入 risk_focus)。

产线位置: 规划落库后独立 flash 二段跑 (不碰规划提示词 — 其 reasoning 曾实测
失控到 24K, 不能再塞字段)。契约存 DirectorSlot.params_json["shot_contract"],
失败静默 — 契约是增强层, 不阻塞 reviewing 流程。

消费方:
- J2 effect_recipe (本文件 derive + 导草稿时 add_effect) — 设计方案 J2 最后一根线
- 素材匹配语义增强 / i2v 提示词 (未来 #2 直接消费 first_frame/motion/camera)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models import DirectorJob, DirectorSlot

logger = logging.getLogger(__name__)

# 需要契约的 visual_type (host 纯口播无画面语义)
_CONTRACT_TYPES = {"broll_pexels", "broll_local", "hf_chart", "hf_title", "mixed_host_broll"}

SHOT_CONTRACT_PROMPT = """你是短视频的【镜头契约师】。下面是口播视频的若干画面槽位（slot），每个槽位已有关键词和口播上下文。为每个槽位产出一份精简镜头契约，只输出 JSON，不要其他文字。

# 契约字段（7 段, Hell Grind 裁剪版）
- visual_goal: 一句话 — 本画面让观众理解/感受到什么（删掉风格词后这句仍然成立）
- first_frame: 首帧画面 — 写"画面里能看到什么"（视角范围+景别+主体），禁止写镜头毫米数/摄影术语
- motion: 单主运动 — 物理行为描述（重量/惯性/重力有因果，如"光点从高处沿重力倾泻，落到平面溅起颗粒"），禁止形容词堆砌（"震撼/真实感/科技感"）
- camera: 运镜 — 从[起构图]以[速度]做[一个主运动]到[落构图]；只有一个主运动
- light_material: 主光源来自哪 + 关键材质怎么响应光（高光随光源移动等）
- risk_focus: 本画面最可能翻车的 ≤3 点（如: 文字乱码/人物变形/运动无因果）
- tone_words: 2-4 个基调词（冷静/紧张/温暖/恢弘…）
- brightness: 亮度基调三选一 — "暗调/中调/亮调"。默认**中调**；只有 tone_words 明确阴郁/沉重/压抑（如葬礼/危机/深夜戏）才允许暗调。⚠ Wan2.2 t2v 暗调默认，写暗调画面会黑成一团
- style: 风格定位三选一 — "示意/抽象/写实"。默认**示意**（卡通手办美学：圆润形体、磨砂塑料树脂材质、微缩摆放感、明快色彩——AI 意象主场，真实画面另有素材API管线，写实是 Wan2.2 打不赢的仗）。写实仅当口播明确要求"真实画面感"且无素材可用时
- effect_recipe: 从下方菜单选（选不出合适的就 null，禁止编菜单外的名字）

# 特效菜单（J2 效果目录实测高频池, 名字必须逐字照抄; 运行时由目录 top 池填充）
{effect_menu}

# 输出格式
{"contracts": [{"slot_index": <槽位号>, "visual_goal": "...", "first_frame": "...",
  "motion": "...", "camera": "...", "light_material": "...",
  "risk_focus": ["..."], "tone_words": ["..."], "brightness": "中调", "style": "示意",
  "effect_recipe": {"video_effect": "发光 或 null", "anim_in": "渐显 或 null"}}]}"""


def _effect_menu() -> str:
    """J2 目录实时 top 池注入提示词 (目录重跑后菜单自动更新)."""
    try:
        from .jy_effect_library import top
        ve = "、".join(r["name"] for r in top("video_effect", 5))
        ai = "、".join(r["name"] for r in top("anim_in", 4))
        return f"video_effect 候选: {ve}\nanim_in 候选（给文字卡）: {ai}"
    except Exception:
        return "video_effect 候选: 发光、模糊、边缘发光\nanim_in 候选: 向上露出、渐显"


def generate_shot_contracts(db: Session, job_id: str) -> dict[str, Any]:
    """规划落库后跑: 批量生成 broll/hf slot 的镜头契约 + 校验 effect_recipe.

    Returns: {"contracts": n, "recipes": n, "failed": reason?} — 失败只 log 不抛。
    """
    job = db.query(DirectorJob).filter(DirectorJob.id == job_id).first()
    if not job:
        return {"failed": "job not found"}
    slots = (db.query(DirectorSlot)
             .filter(DirectorSlot.director_job_id == job_id)
             .order_by(DirectorSlot.slot_index).all())
    targets = [s for s in slots if s.visual_type in _CONTRACT_TYPES]
    if not targets:
        return {"contracts": 0}

    payload = [{
        "slot_index": s.slot_index,
        "visual_type": s.visual_type,
        "口播上下文": (s.text_context or "")[:200],
        "关键词": {k: v for k, v in (s.params_json or {}).items()
                    if k in ("scenes", "shot_types", "tone", "keywords")},
    } for s in targets]

    prompt_tpl = SHOT_CONTRACT_PROMPT.replace("{effect_menu}", _effect_menu())
    try:
        from .boost_service import _call, _extract_json
    except Exception as exc:  # pragma: no cover
        return {"failed": str(exc)[:120]}

    # 分批 (每批 10): flash 空响应自动 thinking 重试时, thinking 会吃掉输出预算
    # 致正文截断 (实测 28 slot 单批 → 701 字符断在半句); 小批输出 ~2K 字符安全。
    by_idx = {s.slot_index: s for s in targets}
    valid_ve = {r["name"] for r in _menu_names("video_effect")}
    valid_ai = {r["name"] for r in _menu_names("anim_in")}
    n_c = n_r = 0
    for i in range(0, len(payload), 10):
        chunk = payload[i : i + 10]
        prompt = prompt_tpl + "\n\n【槽位列表】\n" + json.dumps(chunk, ensure_ascii=False)
        try:
            raw = _call(prompt, json_mode=True, max_tokens=9000, temperature=0.3)
            data = _extract_json(raw)
            items = (data or {}).get("contracts") or []
        except Exception as exc:
            logger.warning("[shot_contract %s] 批 %d LLM 失败(跳过): %s",
                           job_id[:8], i // 10, exc)
            continue
        for item in items:
            s = by_idx.get(item.get("slot_index"))
            if not s:
                continue
            contract = {k: item.get(k) for k in
                        ("visual_goal", "first_frame", "motion", "camera",
                         "light_material", "risk_focus", "tone_words",
                         "brightness", "style")}
            recipe = item.get("effect_recipe") or {}
            # 菜单校验: 编造的效果名直接丢 (J2 目录里不存在 = 剪映加不上)
            if not valid_ve or recipe.get("video_effect") not in valid_ve:
                recipe["video_effect"] = None
            if not valid_ai or recipe.get("anim_in") not in valid_ai:
                recipe["anim_in"] = None
            contract["effect_recipe"] = recipe
            params = dict(s.params_json or {})
            params["shot_contract"] = contract
            s.params_json = params  # JSON 列整体重赋值才触发更新
            n_c += 1
            n_r += bool(recipe.get("video_effect") or recipe.get("anim_in"))
    db.commit()
    logger.info("[shot_contract %s] 契约 %d/%d, 带配方 %d", job_id[:8], n_c, len(targets), n_r)
    return {"contracts": n_c, "recipes": n_r}


def _menu_names(category: str) -> list[dict]:
    try:
        from .jy_effect_library import top
        return top(category, 6)
    except Exception:
        return []


def derive_effect_recipe(slot: DirectorSlot) -> dict:
    """导草稿侧取配方: params_json.shot_contract.effect_recipe (无契约返回空)."""
    return ((slot.params_json or {}).get("shot_contract") or {}).get("effect_recipe") or {}
