# -*- coding: utf-8 -*-
"""k2_lang — K2 产线纯英化 (1017 W7, 用户令).

语言架构定案: K2 生图 = 纯英文正/负提示词 (零中文, 含花字禁入); 二段式
第二段 (face_fix/erase/text_pass 图编辑) 可中文; H3 = 中文正 + 英文负。

本模块: ZH→EN 翻译层 (LLM 一次, 镜级缓存 image_prompt_en + 源串 sha 失效),
K2 组装层消费 EN 缓存 — 单一插入点, 圣经/风格卡/器层中文事实源不动
(单一源字段保持中文主权, 英文是渲染投影)。"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYS = """You are a prompt translator for an image-generation pipeline. Translate
the Chinese scene description into ONE fluent English prompt segment. Rules:
1. Keep every visual element, position, lighting and mood — nothing added, nothing lost.
2. OMIT any text/writing/glyph/lettering instructions entirely (this pipeline draws no
   text; if the source mentions text on signs/labels, translate it as a blank surface).
3. No style words, no quality words, no camera jargon beyond what the source says.
4. Output ONLY the English translation, no quotes, no explanation."""

_BOOK_HEAD: dict[str, tuple[str, str]] = {}   # book → (style_head_sha, en)
_BOOK_TAIL: dict[str, tuple[str, str]] = {}   # book → (style_tail_sha, en)
_ARC_THESIS: dict[tuple[str, str], str] = {}  # (book, thesis_sha) → en
_BOOK_POLICY: dict[str, tuple[float, str]] = {}  # book → (bible_mtime, policy_en)


def _sha(s: str) -> str:
    return hashlib.md5(str(s).encode("utf-8")).hexdigest()[:10]


def _chat(user: str) -> str:
    from .config import load as _anim_load
    from .llm import chat_json
    out = chat_json(_anim_load().llm, _SYS,
                    f"Translate to English:\n{user}")
    # chat_json 强制 JSON — 约定 {"en": "..."}; 兜底拼回
    if isinstance(out, dict):
        return str(out.get("en") or " ".join(str(v) for v in out.values() if v)).strip()
    return str(out).strip()


def ensure_en_prompt(shot: dict[str, Any], zh_scene: str) -> str:
    """镜级 EN 提示词缓存 (image_prompt_en, 源串 sha 失效; LLM fail-open 退中文原串)."""
    sha = _sha(zh_scene)
    cached = shot.get("image_prompt_en")
    if isinstance(cached, dict) and cached.get("sha") == sha and cached.get("en"):
        return str(cached["en"])
    try:
        en = _chat(zh_scene)
        if not en or len(en) < 20:
            raise ValueError(f"translation too short: {len(en)}")
    except Exception as exc:  # noqa: BLE001 — 翻译失败退中文 (fail-open, 记日志)
        logger.warning("[k2-en] %s 翻译失败 (退中文原串): %s",
                       shot.get("shot_id"), str(exc)[:80])
        return zh_scene
    shot["image_prompt_en"] = {"sha": sha, "en": en}
    logger.info("[k2-en] %s ✓ %d chars", shot.get("shot_id"), len(en))
    return en


def style_head_en(book_title: str) -> str:
    """风格头 EN 投影 (进程缓存 + sha 失效; 圣经/风格卡中文主权不动)."""
    from .config import resolve_style
    head = str(resolve_style(book_title).get("style_prompt_head") or "")
    if not head:
        return ""
    sha = _sha(head)
    hit = _BOOK_HEAD.get(book_title)
    if hit and hit[0] == sha:
        return hit[1]
    try:
        en = _chat(head)
        if len(en) < 10:
            raise ValueError("empty")
    except Exception:  # noqa: BLE001
        return head
    _BOOK_HEAD[book_title] = (sha, en)
    return en


def style_tail_en(book_title: str) -> str:
    """风格尾 EN 投影 (进程缓存 + sha 失效)。"""
    from .config import resolve_style
    tail = str(resolve_style(book_title).get("style_prompt_tail") or "")
    if not tail:
        return ""
    sha = _sha(tail)
    hit = _BOOK_TAIL.get(book_title)
    if hit and hit[0] == sha:
        return hit[1]
    try:
        en = _chat(tail)
        if len(en) < 5:
            raise ValueError("empty")
    except Exception:  # noqa: BLE001
        return tail
    _BOOK_TAIL[book_title] = (sha, en)
    return en


def thesis_en(book_title: str, thesis: str) -> str:
    """弧主旨 EN 投影 (sha 缓存; 空串直通)。"""
    if not thesis:
        return ""
    key = (book_title, _sha(thesis))
    if key in _ARC_THESIS:
        return _ARC_THESIS[key]
    try:
        en = _chat(thesis)
    except Exception:  # noqa: BLE001 — 翻译失败退英文占位 (不引中文进 K2)
        return "the scene serves the episode thesis"
    _ARC_THESIS[key] = en
    return en


def character_policy_en(book_title: str) -> str:
    """圣经角色纪律 EN 投影 (mtime 守卫, 同 h3.character_policy 的英文化)."""
    from .h3 import character_policy
    zh = character_policy(book_title)
    if not zh:
        return ""
    try:
        from .director2 import _bible_cache_path
        mt = _bible_cache_path(book_title).stat().st_mtime
    except Exception:  # noqa: BLE001
        mt = 0.0
    hit = _BOOK_POLICY.get(book_title)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        en = _chat(zh)
    except Exception:  # noqa: BLE001
        return ""
    _BOOK_POLICY[book_title] = (mt, en)
    return en


# ── K2 军规常量 EN 版 (与中文版语义逐条对齐; 中文版保留给 H3/编辑 pass) ──
SKIN_HAIR_LAW_EN = (
    ". Character skin is solid matte cartoon skin (opaque, no mirror highlights); "
    "hair is sculpted grouped locks with clear highlight separation and gaps, "
    "readable strand direction (no smooth plastic shell, no mushy detail-less hair)")
FACE_LAW_EN = (
    ". Every character's facial features are complete and clear: distinct eyes, "
    "nose and mouth with a readable expression (never blank, featureless, missing "
    "mouth or eyes); the face is turned toward the camera (frontal or three-quarter "
    "view — never a pure back view or back silhouette); the full figure stays in "
    "frame with the head uncropped")
PROTECT_TAIL_EN = (
    ". The image contains no text, no numbers, no logos; screens show only clean "
    "blank graphic areas; signs and plaques are blank. Buildings, objects and "
    "clothing are everyday civilian styles")
COMPOSITION_BOOST_EN = (
    ". The frame shows a complete environmental space; the subject occupies a "
    "fitting proportion; the background extends into the distance; foreground, "
    "midground and background layers are clearly separated; light comes from deep "
    "in the scene; the overall composition is open and expansive")
CHAR_POLICY_PREFIX_EN = "Character law (book bible, mandatory): "
