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
4. Respond in json: {"en": "<English translation>"} — the en value is plain text,
   no quotes inside, no explanation. (json key fixed as "en")"""

_BOOK_HEAD: dict[str, tuple[str, str]] = {}   # book → (style_head_sha, en)
_BOOK_HEAD_EMPTY: dict[str, tuple[str, str]] = {}  # book → (empty_head_sha, en) 1108 批A-3
_BOOK_TAIL: dict[str, tuple[str, str]] = {}   # book → (style_tail_sha, en)
_ARC_THESIS: dict[tuple[str, str], str] = {}  # (book, thesis_sha) → en
_BOOK_POLICY: dict[str, tuple[float, str]] = {}  # book → (bible_mtime, policy_en)
_STANDIN_EN: dict[str, str] = {}  # zh_look sha → en (替身 look 英译, 零身份镜 EN 路兜底)
_LOOK_EN: dict[tuple[str, str], tuple[str, str]] = {}  # (name_sha, look_sha) → (name_en, look_en)


def standin_look_en(zh_look: str) -> str:
    """观众替身 look 英译 (1026: fallback 镜 keyframe_en 模板零身份 → 随机人群;
    EN 路不跑 zh 注入, 需英译前置。sha 进程缓存, fail-open 退中文原串)。"""
    if not zh_look:
        return ""
    sha = _sha(zh_look)
    hit = _STANDIN_EN.get(sha)
    if hit:
        return hit
    try:
        en = _chat(zh_look)
        if len(en) < 8:
            raise ValueError("empty")
    except Exception:  # noqa: BLE001 — 翻译失败退中文 (名词串混排仍有效)
        return zh_look
    _STANDIN_EN[sha] = en
    return en


def _sha(s: str) -> str:
    return hashlib.md5(str(s).encode("utf-8")).hexdigest()[:10]


def char_look_en(name: str, zh_look: str) -> tuple[str, str]:
    """圣经角色 look 英译 (1108 批A: K2 EN 路 look 前置用 — v3 writer keyframe_en
    实测零外观词, EN 路"look 已内联"假设断裂 = K2 图层面部漂移根因 s6 胡茬实锤)。

    返回 (name_en, look_en); sha 缓存 per 角色 (改一卡只重翻一卡);
    fail-open 退 (中文名, 中文 look) — 混排仍比零注入强。"""
    if not name or not zh_look:
        return (name, zh_look)
    key = (_sha(name), _sha(zh_look))
    hit = _LOOK_EN.get(key)
    if hit:
        return hit
    try:
        from .llm import chat_json
        from .config import load as _anim_load
        out = chat_json(
            _anim_load().llm, _SYS,
            "Translate for an image-generation prompt. Output JSON "
            '{"name_en": "<pinyin or standard English name>", '
            '"look_en": "<the look description in English, keep ALL visual '
            'details including hair, glasses presence, clean-shaven face, '
            'outfit colors>"}. Input:\n'
            f"Name: {name}\nLook: {zh_look}")
        ne = str(out.get("name_en") or "").strip() or name
        le = str(out.get("look_en") or "").strip() or zh_look
    except Exception:  # noqa: BLE001 — 翻译失败退中文 (名词串混排仍有效)
        return (name, zh_look)
    _LOOK_EN[key] = (ne, le)
    return (ne, le)


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
    """镜级 EN 提示词缓存 (image_prompt_en, 源串 sha 失效; LLM fail-open 退中文原串).

    1103 Y2 (用户令: 规划原生英文, 翻译层只做残镜兜底): keyframe_en 非空 →
    _scene_eff 走 EN 路, 入参已是英文 — 直存缓存零 LLM。旧实现把英文串再过一遍
    "翻译" (ep2 s09 实锤: 出入仅个别冠词, 纯英→英 round-trip: 烧配额+拖 GPU 间隙
    +重述漂移风险, s37/s39 的"翻译失败"也是它自造)。"""
    sha = _sha(zh_scene)
    cached = shot.get("image_prompt_en")
    if isinstance(cached, dict) and cached.get("sha") == sha and cached.get("en"):
        return str(cached["en"])
    if str(shot.get("keyframe_en") or "").strip():
        shot["image_prompt_en"] = {"sha": sha, "en": zh_scene, "native": True}
        logger.info("[k2-en] %s native ✓ 规划英文直用 (%d chars)",
                    shot.get("shot_id"), len(zh_scene))
        return zh_scene
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


# ── 1103 Y1 批前预翻译 (用户令: 批量压上, GPU 零等待 — 出一张停一下再 llm 的
# 串行排布废除; 每次调用带一小批, 坏的走单发补一次) ─────────────────────
_BATCH_SYS = (
    "你是图像提示词批量翻译器。输入 {\"items\":[{\"id\":\"镜号\",\"zh\":\"中文提示词\"}]}, "
    "输出严格 JSON {\"t\":[{\"id\":\"镜号\",\"en\":\"完整英译\"}]} — 每条都要完整翻译, "
    "不省略不截断不加注释, 风格词/镜头语言/人物 look 原样保留。"
)


def translate_batch(items: list[tuple[dict, str]], group: int = 5) -> dict:
    """批翻译未缓存镜 [(shot, zh_source)] → 写 image_prompt_en 缓存。

    每组一次 LLM 调用 (group 镜/调); 组内漏译/组失败的单发补一次
    (ensure_en_prompt 单条通道); 单发也坏 → 留空, 渲染循环内退中文兜底 (现状行为)。"""
    import json as _json
    from .config import load as _anim_load
    ok = 0
    retry: list[tuple[dict, str]] = []
    for i in range(0, len(items), group):
        chunk = items[i:i + group]
        got: dict[str, str] = {}
        try:
            from .llm import chat_json
            raw = chat_json(_anim_load().llm, _BATCH_SYS,
                            _json.dumps({"items": [{"id": s["shot_id"], "zh": zh}
                                                   for s, zh in chunk]}, ensure_ascii=False))
            got = {str(t.get("id")): str(t.get("en") or "")
                   for t in ((raw or {}).get("t") or [])}
        except Exception as exc:  # noqa: BLE001 — 组失败进单发补
            logger.warning("[k2-en] 批翻译组失败 (%s 组%d镜): %s",
                           chunk[0][0].get("shot_id"), len(chunk), str(exc)[:80])
        for s, zh in chunk:
            en = got.get(s["shot_id"], "")
            if en and len(en) >= 20:
                s["image_prompt_en"] = {"sha": _sha(zh), "en": en}
                ok += 1
            else:
                retry.append((s, zh))
    fail = 0
    rescued = 0
    for s, zh in retry:  # 坏的单发补一次 (有需要的才走一次)
        before = s.get("image_prompt_en")
        ensure_en_prompt(s, zh)
        after = s.get("image_prompt_en")
        if after and after != before:
            rescued += 1
        else:
            fail += 1
    ok += rescued
    logger.info("[k2-en] 预翻译批: ✓%d (批%d+单发补%d) / 退中文%d (共%d)",
                ok, ok - rescued, rescued, fail, len(items))
    return {"pending": len(items), "ok": ok, "fail": fail}


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


def style_head_empty_en(book_title: str) -> str:
    """空镜变体风格头 EN 投影 (1108 批A-3; 镜像 style_head_en, 进程缓存+sha 失效)."""
    from .config import resolve_style
    head = str(resolve_style(book_title).get("style_prompt_head_empty") or "")
    if not head:
        return ""
    sha = _sha(head)
    hit = _BOOK_HEAD_EMPTY.get(book_title)
    if hit and hit[0] == sha:
        return hit[1]
    try:
        en = _chat(head)
        if len(en) < 10:
            raise ValueError("empty")
    except Exception:  # noqa: BLE001
        return head
    _BOOK_HEAD_EMPTY[book_title] = (sha, en)
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
    "hair is styled in large defined sections with sharp tapered tips, a clean "
    "windswept direction and crisp edges, a few flyaway strands, matte finish "
    "(no dreadlocks, no braids or beaded hair strands, no smooth plastic shell, "
    "no mirror highlights, no soft fluffy velvet fuzz-ball, no solid sculpted "
    "helmet, no mushy detail-less hair)")
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
