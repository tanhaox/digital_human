# -*- coding: utf-8 -*-
"""从爆品改造稿提取"视觉意图" — 解决导演盲盒.

爆品改造稿 (boosted_text) 里含结构标记:
  P1 新开头(4句冲击) / P2 预埋【争议预埋】【关注预埋】【争议回收】【关注回收】/
  P3 呼吸点(加粗) / [calm][serious][confident] 情绪标签
这些标记 → 每段的视觉意图 → 传给导演 LLM 配画面.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 情绪标签 → 视觉情绪
_EMOTION_VISUAL = {
    "calm": "沉稳/冷峻",
    "serious": "紧张/严肃",
    "confident": "升华/坚定",
}

# 结构标记 → 视觉意图
_STRUCTURE_INTENT = {
    "争议预埋": ("张力/暗调", "埋伏笔, 画面暗示, 配合站队冲突"),
    "关注预埋": ("悬疑/深水", "暗示还有更深的, 画面留悬念"),
    "争议回收": ("收束/对比", "二选一站队, 画面强化对立"),
    "关注回收": ("期待/续篇", "暗示下期, 画面留钩子"),
}


def _parse_seconds(text: str) -> int | None:
    """从文本里解析秒数标记 (如 "[24.7s]"), 无则 None."""
    m = re.search(r"\[(\d+(?:\.\d+)?)s\]", text)
    if m:
        return int(float(m.group(1)))
    return None


def extract_visual_intent(
    boosted_text: str,
    segment_timings: list[dict[str, Any]] | None = None,
    emotion_annotations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """从爆品改造稿提取每段的视觉意图.

    Args:
        boosted_text: 爆品改造后的全文 (含结构标记)
        segment_timings: 可选, 每段起止时间 (用于给意图定位到时间轴)
        emotion_annotations: 可选 (2026-08-13, P5): 段落情绪标注 [{text, emotion, strength}].
            优先用它 (情绪在统一字典查视觉意图); 缺省回退从 boosted_text 解析 [情绪] 标记.

    Returns:
        [{start_sec, intent, desc, keyword}, ...] — 传给 build_director_prompt 的 visual_intent
    """
    # ── 优先 P5 情绪标注 (统一字典) ──
    if emotion_annotations:
        from ..emotion_dict import EMOTIONS, EMOTION_CN

        intents: list[dict[str, Any]] = []
        for ann in emotion_annotations:
            emo = ann.get("emotion", "calm")
            strength = ann.get("strength", "中")
            emo_def = EMOTIONS.get(emo)
            if not emo_def:
                continue
            cn = EMOTION_CN.get(emo, emo)
            intents.append({
                "start_sec": len(intents),
                "intent": f"情绪:{emo_def.visual_intent}",
                "desc": f"此段口播情绪是{cn}(强度:{strength}), 画面应配合: {emo_def.visual_intent}",
                "keyword": emo,
                "text": (ann.get("text") or "")[:50],
            })
        if segment_timings and intents:
            try:
                _apply_timings(intents, segment_timings)
            except Exception:
                pass
        return intents

    # ── 回退: 从 boosted_text 解析 (旧稿/无标注) ──
    if not boosted_text:
        return []

    intents = []
    lines = boosted_text.splitlines()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # 1) 情绪标签 → 视觉情绪
        emo_m = re.match(r"\[(calm|serious|confident)\]", line)
        if emo_m:
            emo = emo_m.group(1)
            intents.append({
                "start_sec": i,
                "intent": f"情绪:{_EMOTION_VISUAL[emo]}",
                "desc": f"此段口播情绪是{_EMOTION_VISUAL[emo]}, 画面应配合",
                "keyword": emo,
            })
            continue

        # 2) 结构标记 【争议预埋】【关注预埋】【争议回收】【关注回收】
        for marker, (intent, desc) in _STRUCTURE_INTENT.items():
            if f"【{marker}" in line or marker in line:
                intents.append({
                    "start_sec": i,
                    "intent": f"结构:{marker}",
                    "desc": f"{marker}段 — {desc}. 禁止配蓝天白云/风景空镜",
                    "keyword": marker,
                })
                break

        # 3) P1 新开头 (前4句, 冲击钩子)
        if i < 4 and "开" not in line:
            pass  # 开头钩子由情绪标签覆盖, 不重复

        # 4) 呼吸点 (加粗 **...**)
        if line.startswith("**") and line.endswith("**"):
            intents.append({
                "start_sec": i,
                "intent": "呼吸点:放松/留白",
                "desc": "这里是节奏呼吸点, 画面应放松留白, 让观众缓口气",
                "keyword": "呼吸点",
            })

    # 用真实时间轴校准 (若提供)
    if segment_timings and intents:
        _apply_timings(intents, segment_timings)

    return intents


def _apply_timings(intents: list[dict[str, Any]], segment_timings: list[dict[str, Any]]) -> None:
    """把意图的 start_sec (行号近似) 用真实时间轴校准.

    segment_timings 格式: [{segment_id, text, start, end, ...}]
    用行号→文本匹配近似映射到时间.
    """
    # 简化: 意图里的 start_sec 是行号, 这里不做严格校准 (由下游 alignment 负责对齐)
    # 保留原样, 导演会用补充输入3的时间轴对齐.
    pass


def summarize_intent(intents: list[dict[str, Any]]) -> str:
    """把意图列表压缩成一行摘要, 便于日志."""
    if not intents:
        return "无结构意图"
    kinds = {}
    for it in intents:
        k = it["intent"].split(":")[0]
        kinds[k] = kinds.get(k, 0) + 1
    return ", ".join(f"{k}×{v}" for k, v in kinds.items())
