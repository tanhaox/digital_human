"""视频打标 — LLM 输出 JSON 解析与规则引擎回退。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.video_tagging_service.constants import (
    ALL_SCENES,
    ALL_SHOT_TYPES,
    DENSITY_VALUES,
    MOTION_VALUES,
    TIME_OF_DAY_VALUES,
    TONE_VALUES,
)

logger = logging.getLogger(__name__)

__all__ = ["frame_tagging_result"]


_JSON_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """多层尝试从 LLM 输出中提取 JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_PATTERN.search(text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def _validate_tag(parsed: dict, key: str, valid_set: set[str]) -> list[str] | str | None:
    """校验并过滤单个标签维度的值."""
    val = parsed.get(key)
    if val is None:
        return None
    if isinstance(val, list):
        filtered = [v for v in val if v in valid_set]
        return filtered if filtered else None
    if isinstance(val, str) and val in valid_set:
        return val
    return None


def _build_fallback_result(fallback_tags: dict, raw_text: str) -> dict[str, Any]:
    """构造规则引擎回退标签字典 (LLM 解析失败/无有效标签时)."""
    return {
        **fallback_tags,
        "_fallback": True,
        "_ai_raw": raw_text[:500],
    }


def _apply_validated_tags(
    parsed: dict,
    fallback_tags: dict,
    raw_text: str,
    *,
    scenes: list[str] | str | None,
    shot_types: list[str] | str | None,
    source_type: list[str] | str | None,
    location: list[str] | str | None,
    people: list[str] | str | None,
    tone: list[str] | str | None,
    density: list[str] | str | None,
    motion: list[str] | str | None,
    time_of_day: list[str] | str | None,
) -> dict[str, Any]:
    """组装校验通过后的标签字典 (缺失维度回退 fallback_tags)."""
    result: dict[str, Any] = {
        "scenes": scenes if scenes is not None else fallback_tags.get("scenes", []),
        "shot_types": shot_types if shot_types is not None else fallback_tags.get("shot_types", []),
        "source_type": source_type if source_type is not None else fallback_tags.get("source_type", "footage"),
        "location": location if location is not None else fallback_tags.get("location", "foreign"),
        "people": people if people is not None else fallback_tags.get("people", "none"),
        "description_zh": (parsed.get("description_zh", "") or fallback_tags.get("description_zh", "")),
    }

    extra: dict[str, Any] = {}
    for key, val in (("tone", tone), ("content_density", density), ("motion_level", motion), ("time_of_day", time_of_day)):
        if val:
            extra[key] = val

    confidence = parsed.get("confidence", None)
    result["_ai_extra"] = extra if extra else None
    result["_ai_confidence"] = confidence if isinstance(confidence, dict) else None
    result["_fallback"] = False
    result["_ai_raw"] = raw_text[:500]

    return result


def frame_tagging_result(raw_text: str, fallback_tags: dict) -> dict[str, Any]:
    """解析 LLM 输出为结构化标签, 失败时回退到规则引擎."""
    parsed = _try_parse_json(raw_text)

    if parsed is None:
        logger.warning("LLM JSON parse failed, falling back to rule engine. raw=%s", raw_text[:200])
        return _build_fallback_result(fallback_tags, raw_text)

    scenes = _validate_tag(parsed, "scenes", set(ALL_SCENES))
    shot_types = _validate_tag(parsed, "shot_types", set(ALL_SHOT_TYPES))
    source_type = _validate_tag(parsed, "source_type", {"footage", "creative"})
    location = _validate_tag(parsed, "location", {"domestic", "foreign"})
    people = _validate_tag(parsed, "people", {"people", "none"})
    tone = _validate_tag(parsed, "tone", set(TONE_VALUES))
    density = _validate_tag(parsed, "content_density", set(DENSITY_VALUES))
    motion = _validate_tag(parsed, "motion_level", set(MOTION_VALUES))
    time_of_day = _validate_tag(parsed, "time_of_day", set(TIME_OF_DAY_VALUES))

    if scenes is None and shot_types is None and source_type is None:
        logger.warning("LLM returned no valid tags, falling back. raw=%s", raw_text[:200])
        return _build_fallback_result(fallback_tags, raw_text)

    return _apply_validated_tags(
        parsed,
        fallback_tags,
        raw_text,
        scenes=scenes,
        shot_types=shot_types,
        source_type=source_type,
        location=location,
        people=people,
        tone=tone,
        density=density,
        motion=motion,
        time_of_day=time_of_day,
    )
