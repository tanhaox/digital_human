# -*- coding: utf-8 -*-
"""beats_enrich — K2 批尾 beats 多拍化 pass (1017 W3 S2, 用户令后处理固化).

治"贴首帧但动画单一"与"beats 与图冻结瞬间错位"两族病 (s04 干晃 / s09 重排
实锤)。输入吃**已渲 K2 图的 VLM 冻结瞬间描述** (图真锚; VLM 不可用退画面句
动作描述), LLM 产多拍 beats。军规内嵌:
  ID-100 锁构图动画密度律: 相机全程固定禁急推重构图; 每拍 ≥2 画框内可见变化;
    第一拍必须从图的冻结姿势续写, 禁发明前置动作。
  ID-101 跨镜上下文: 前镜尾口播 + 后镜头口播随行。
GPU 相位写 doc 的显式豁免 (受控 pass: beats_enriched 标幂等 + 全程日志 +
fail-open 保单拍) — 与被取缔的自治抽词性质不同。"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM = """你是短视频动画节拍设计师。给你一张已生成的定格画面(首帧图)的冻结瞬间
描述, 为它设计 2-4 秒一拍的动画节拍。你只动画里已有的东西, 绝不发明新物件,
绝不改变取景。只输出 JSON。"""

_TPL = """# 镜头数据
口播(本镜): {narration}
前镜尾口播(上下文): {prev}
后镜头口播(上下文): {next}
画面句(规划描述): {keyframe}
首帧图冻结瞬间(VLM 实看): {frozen}
本镜时长: {duration:.1f} 秒
书级运动律: {motion}

# 任务
从**冻结瞬间**续写, 设计 {duration:.1f} 秒的 2-5 个节拍(beats), 每拍 1.5-3 秒,
时间轴从 0 连续铺满 {duration:.1f} 秒 (最后一拍 t_end = {duration:.1f})。

# 硬律 (违者作废)
1. 锁构图: 相机全程固定在 Picture 1 取景 — 禁急推/zoom/重构图/离场特写
   (quick zoom-in burst 是病, s09 实锤)。镜头零运动, 动效全在画框内。
2. 冻结瞬间续写: 第一拍必须从"首帧图冻结瞬间"描述的动作状态**向前**演,
   禁回卷禁前置 (图里卡已举在手上就不能写"伸手去拿")。
3. 每拍 ≥2 个画框内可见变化: 光带扫过/边缘反光滑动/粒子/微表情/物件微动/
   台灯光呼吸。禁止 "stays still / nothing moves" 纯静止。
4. 首拍 1 秒内见变化; 只动图里已有的物件人物; 人物禁原地行进, 可走出走入画面。
5. ending 动感落点但镜头不动 (光束定格/粒子沉降), 不写 "calm hold"。
6. motion 英文为主, 引用图里具体物件; 不描述任何文字/字卡 (文字另有通道)。

# 输出 JSON
{{"beats": [{{"t_start": 0, "t_end": 2.5, "motion": "English motion ..."}}, ...],
 "ending": "English ending, camera fixed"}}"""

_STATIC_RE = re.compile(r"stays still|nothing moves|no object moves", re.I)


# 1021 用户令: 图真锚撤 — 读图锚 (1328px 过本地 VLM) 慢且要与 ComfyUI 抢大卡,
# ep7 全集 VLM 不可用退画面句锚照样每镜 ✓3-4拍, 实况证明退路质量够用;
# 再小的图和纯文字比都大 → 恒走画面句锚。翻回 True 即恢复读图锚。
_VLM_ANCHOR = False


def _frozen_desc(img_path: Path, vlm_ok: list[bool]) -> str:
    """VLM 看图取冻结瞬间 (一次失败即熔断退画面句, 免批内反复等超时).

    1021 起 _VLM_ANCHOR=False 恒退画面句锚 (用户令, 见上方开关注释)。"""
    if not _VLM_ANCHOR:
        return ""
    if not vlm_ok or not vlm_ok[0]:
        return ""
    try:
        from tools.vision import analyze
        return analyze([str(img_path)],
                       "描述这张定格画面的人物姿势/道具状态/构图取景, 作为动画"
                       "起始瞬间。两行内, 客观只述可见。")
    except Exception as exc:  # noqa: BLE001 — VLM 不可用退画面句
        vlm_ok[0] = False
        logger.warning("[beats-enrich] VLM 不可用 (本批退画面句锚): %s", str(exc)[:80])
        return ""


def _validate(out: dict[str, Any], dur: float) -> str | None:
    beats = out.get("beats")
    if not isinstance(beats, list) or not beats:
        return "beats 空"
    t = 0.0
    for b in beats:
        if not isinstance(b, dict):
            return "beat 非法"
        m = str(b.get("motion") or "").strip()
        if len(m) < 25:
            return f"motion 太薄: {m[:30]}"
        if _STATIC_RE.search(m):
            return f"静止拍: {m[:40]}"
        try:
            ts, te = float(b["t_start"]), float(b["t_end"])
        except (KeyError, TypeError, ValueError):
            return "t 非法"
        if abs(ts - t) > 0.3:
            return f"时间轴断档 @{ts}"
        if te - ts > 3.6 or te - ts < 1.0:
            return f"拍长越界 {ts}-{te}"
        t = te
    if abs(t - dur) > 0.4:
        return f"未铺满: {t:.1f}/{dur:.1f}"
    return None


def enrich_beats(doc: dict[str, Any], only: set[str] | None = None) -> int:
    """批量多拍化: img_done 且未 enrich 的镜。返回成功数 (失败保单拍不计数)."""
    from . import shots as shots_mod
    from .config import load as _anim_load
    from .h3 import motion_style
    from .llm import chat_json

    llm_cfg = _anim_load().llm
    _ms = motion_style(str(doc.get("book_title") or ""))
    shots = doc.get("shots") or []
    vlm_ok = [True]
    n = 0
    for i, s in enumerate(shots):
        if s.get("beats_enriched") or s.get("brand_card"):
            continue
        if s.get("status") not in ("img_done", "approved", "anim_done", "anim_fail"):
            continue
        if only is not None and s["shot_id"] not in only:
            continue
        anim = s.get("anim") or {}
        dur = float(anim.get("duration_s") or 0)
        if dur <= 0:
            continue
        img = shots_mod.ep_dir(str(doc.get("book_title")), int(doc.get("ep") or 0)) \
            / str(s.get("image_file") or "")
        frozen = _frozen_desc(img, vlm_ok) if img.exists() else ""
        anchor = frozen or f"(按画面句推断起始瞬间) {str(s.get('keyframe_zh') or '')[:120]}"
        prev = (shots[i - 1].get("narration") or "")[-24:] if i > 0 else "(全集开头)"
        nxt = (shots[i + 1].get("narration") or "")[:24:] if i + 1 < len(shots) else "(全集结尾)"
        user = _TPL.format(narration=(s.get("narration") or "").strip(),
                           prev=prev.strip(), next=nxt.strip(),
                           keyframe=(s.get("keyframe_zh") or "").strip()[:160],
                           frozen=anchor, duration=dur, motion=_ms)
        out, err = None, None
        for attempt in range(2):
            try:
                out = chat_json(llm_cfg, _SYSTEM, user)
            except Exception as exc:  # noqa: BLE001
                err = f"LLM: {str(exc)[:80]}"
                time.sleep(2)
                continue
            err = _validate(out, dur)
            if not err:
                break
            out = None
            user += f"\n\n# 上次输出被拒: {err} — 重出全包 JSON, 修正此问题。"
        if out is None:
            logger.warning("[beats-enrich] %s 失败保单拍: %s", s["shot_id"], err)
            s["beats_enriched"] = True  # 失败也打标, 免批批重试烧 token
            continue
        anim["beats"] = [
            {"t_start": round(float(b["t_start"]), 2),
             "t_end": round(float(b["t_end"]), 2),
             "motion": str(b["motion"]).strip()}
            for b in out["beats"]]
        anim["ending"] = str(out.get("ending") or "").strip()[:200]
        s["beats_enriched"] = True
        n += 1
        logger.info("[beats-enrich] %s ✓ %d拍%s", s["shot_id"], len(anim["beats"]),
                    "(VLM锚)" if frozen else "(画面句锚)")
    return n


def batch_enrich_beats(doc: dict[str, Any], only: set[str] | None = None) -> int:
    """anim_service 三 pass 链后的批入口 (同 batch_face_fix 口径)."""
    return enrich_beats(doc, only)
