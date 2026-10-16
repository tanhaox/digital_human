# -*- coding: utf-8 -*-
"""品牌卡随书生成 (0915 用户令: 品牌卡风格必须与书的动画风格一致).

配方骨架冻结 (同打字卡教义: 骨架不变两槽随书), 风格随书:
  - 骨架: 登山客背影 + 山脊群山 + 三拍动画 (合书入包→起身望山→走向群山)
  - 风格: 书级风格配方 (k2.build_prompt 自动注入 head/tail/LoRA) + 圣经色板
  - 文字: **不烧进视频** (v3 纪律) — 落款仪式句走后期字幕 (草稿层金字),
    旧固定资产把"拆一本书翻一座山"烤在画面里 = 末集句污染每集卡, 一并根治
产物: outputs/动画/_资产/品牌卡_{书}.png / .mp4 (一次生成, 全系列复用);
      brand_asset_rel 优先解析本书资产, 无则回退全局固定资产.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import comfy, h3, k2
from . import shots as shots_mod

logger = logging.getLogger(__name__)

# 冻结骨架: 角色三件套 + 场景 (0923 用户令: 坐山顶看书=首帧; 红色冲锋衣; H3: 收书→站→走)
SKELETON_SCENE_ZH = (
    "山顶草地的一块平坦岩石上，一位穿红色冲锋衣、棕色登山靴的登山客正坐在岩石上"
    "专注地读一本摊开的书，书页微风吹动，他背上背着一只橄榄绿徒步背包（只此一只，地面无其他包），"
    "背景是层叠的远山与蜿蜒向远峰的小路，午后暖光洒在山顶"
)
# 三拍动画 (H3, 5s; 无文字事件 — "读一本书，多一个硬本事"走后期字幕)
SKELETON_BEATS: list[dict[str, Any]] = [
    {"t_start": 0, "t_end": 2.0,
     "motion": "He closes the book with one hand and slides it into the olive-green backpack, "
               "wind gently flutters his red jacket; very slight push-in."},
    {"t_start": 2.0, "t_end": 3.5,
     "motion": "Continuing from the previous beat — he stands upright from the rock, "
               "tightens one shoulder strap and gazes at the distant layered mountains."},
    {"t_start": 3.5, "t_end": 5.0,
     "motion": "Continuing from the previous beat — he walks along the winding path "
               "toward the mountains, his back slowly getting smaller; camera stays fixed."},
]
BRAND_SPAN = 5.0
BRAND_SEED = 20250914  # 配方冻结 seed (品牌卡复现性优先)


def brand_paths(book_title: str) -> dict[str, Path]:
    """/_资产/品牌卡_{书}.{png,mp4} 绝对路径."""
    base = shots_mod.anim_output_root() / "_资产"
    return {"keyframe": base / f"品牌卡_{shots_mod._safe(book_title)}.png",
            "video": base / f"品牌卡_{shots_mod._safe(book_title)}.mp4"}


def generate(book_title: str, bible: dict[str, Any] | None = None) -> dict[str, str]:
    """生成本书品牌卡 (K2 首帧 → H3 5s, 一次性, ~2min GPU). 返回产物路径.

    scene = 骨架 + 圣经色板 (书定色彩以名称进画面, K2 中文场景原生);
    风格头尾/LoRA 由 k2.build_prompt 按书级配方自动注入 (单一事实源).
    """
    palette = str((bible or {}).get("color_palette") or "")
    scene = SKELETON_SCENE_ZH + (f"；全片色彩基调：{palette[:120]}" if palette else "")
    paths = brand_paths(book_title)
    safe = shots_mod._safe(book_title)

    # 1) K2 首帧 (1006 用户令"不要液态花边": page="k2" 用干净场景覆写, 不走 base 的抽象流体 LoRA)
    wf = k2.build_workflow(scene, BRAND_SEED, f"anim/{safe}/brand/{safe}",
                           book_title=book_title, page="k2")
    comfy.run_workflow(wf, dest=paths["keyframe"], timeout_sec=300, label=f"brand-k2 {safe}")
    logger.info("[brand] %s 首帧 ✓ (书级风格: %s)", safe,
                __import__("app.services.anim_pipeline.config", fromlist=["resolve_style"])
                .resolve_style(book_title).get("name"))

    # 2) H3 视频 (5s 三拍, 无文字)
    image_name = comfy.upload_image(paths["keyframe"])
    anim = {
        "duration_s": BRAND_SPAN,
        "opening_desc": "a hiker seen from behind on a grassy ridge path, putting a book "
                        "into the olive backpack on his back (only one backpack, none on the ground), layered mountains ahead",
        "physical_lock": "the camera viewpoint, the ridge path and layered mountains",
        "screen_exception": "nothing",
        "beats": [dict(b) for b in SKELETON_BEATS],
        "ending": "his figure keeps walking as the scene gently settles",
    }
    vwf = h3.build_workflow(image_name, anim, BRAND_SEED,
                            f"video/anim/{safe}/brand/{safe}", book_title=book_title)
    comfy.run_workflow(vwf, dest=paths["video"], timeout_sec=600, label=f"brand-h3 {safe}")
    logger.info("[brand] %s 视频 ✓ (5s 三拍, 无文字 — 仪式句走后期字幕)", safe)
    return {"keyframe": str(paths["keyframe"]), "video": str(paths["video"])}


def resolve(book_title: str, ep: int, kind: str) -> str:
    """品牌资产解析 (ep 相对路径): 本书资产优先, 无则回退全局固定资产."""
    p = brand_paths(book_title).get(kind)
    if p is not None and p.exists():
        import os
        return Path(os.path.relpath(p, shots_mod.ep_dir(book_title, ep))).as_posix()
    return shots_mod.brand_asset_rel(book_title, ep, kind)


def shot_design(span: float, bible: dict[str, Any] | None = None) -> dict[str, Any]:
    """品牌镜逐镜生成设计 (0916 用户令: 弃固定资产, 冻结提示词+槽长自由).

    返回 {keyframe_zh, anim}: 骨架场景+圣经色板 + 三拍 beats 等比拉伸到 span
    (5s 骨架 → 任意槽长, 节奏不变). 风格由 k2.build_prompt 按书注入 (单一事实源).
    品牌镜 keyframe/motion 由本函数确定性注入, LLM 不参与 (品牌 DNA 不许随机).
    """
    palette = str((bible or {}).get("color_palette") or "")
    scene = SKELETON_SCENE_ZH + (f"；全片色彩基调：{palette[:120]}" if palette else "")
    beats = [dict(b) for b in SKELETON_BEATS]
    total = sum(float(b["t_end"]) - float(b["t_start"]) for b in beats)
    scale = max(span, 1.0) / total
    t = 0.0
    for b in beats:
        d = round((float(b["t_end"]) - float(b["t_start"])) * scale, 2)
        b["t_start"], b["t_end"] = t, round(t + d, 2)
        t = b["t_end"]
    beats[-1]["t_end"] = round(span, 2)  # 抹平累计误差
    anim = {
        "duration_s": round(span, 2),
        "opening_desc": "a hiker seen from behind on a grassy ridge path, putting a book "
                        "into the olive backpack on his back (only one backpack, none on the ground), layered mountains ahead",
        "physical_lock": "the camera viewpoint, the ridge path and layered mountains",
        "screen_exception": "nothing",
        "beats": beats,
        "ending": "his figure keeps walking as the scene gently settles",
    }
    return {"keyframe_zh": scene, "anim": anim}
