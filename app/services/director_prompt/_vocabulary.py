"""Director prompt — 关键词词表包 (Vocabulary Pack) 构建/重建/加载.

ID-034: AI 打标完成/素材导入/手动脚本调用 rebuild 后, 导演 prompt 从词表
选词, 本地按硬维度过滤 + 软维度 ≥75% 命中率碰撞。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models import VideoAsset
from app.services.director_prompt._constants import (
    BASE_SHOT_TYPES,
    MAX_WORDS_PER_DIM,
    VOCABULARY_PACK_PATH,
    _CLEANUP_PREFIXES,
    _CLEANUP_WORDS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "build_vocabulary_pack",
    "rebuild_vocabulary_pack",
    "load_vocabulary_pack",
]


def _utc_now_iso() -> str:
    """UTC ISO8601 (naive, 秒级) — 词表包 built_at 时间戳."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _looks_chinese(s: str) -> bool:
    """判断字符串主体为中文 (≥60% 字符在 CJK 区)。"""
    if not s:
        return False
    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    return cjk / len(s) >= 0.6


def _collect_enum_dims(assets: list[Any]) -> dict[str, list[str]]:
    """聚合枚举维度 (scenes/shots/ai_tags_extra 四维), 去重保序."""
    dims: dict[str, list[str]] = {
        "scenes": [], "shot_types": [],
        "tone": [], "motion_level": [], "content_density": [], "time_of_day": [],
        "keywords": [],
    }
    scene_seen: set[str] = set()
    shot_seen: set[str] = set()
    ai_extra_seen: dict[str, set[str]] = {
        "tone": set(), "motion_level": set(), "content_density": set(), "time_of_day": set(),
    }

    for asset in assets:
        # 枚举维度直接去重聚合 (打标枚举已在 video_tagging_service 约束过)
        for scene in asset.scenes or []:
            if scene and scene not in scene_seen:
                scene_seen.add(scene)
                dims["scenes"].append(scene)
        for shot in asset.shot_types or []:
            # "空镜"退役 (2026-09-02): 语义≈无人与 people=none 同义, 聚合时即洗掉
            if shot and shot != "空镜" and shot not in shot_seen:
                shot_seen.add(shot)
                dims["shot_types"].append(shot)

        extra = asset.ai_tags_extra or {}
        for dim in ("tone", "motion_level", "content_density", "time_of_day"):
            val = extra.get(dim)
            if not val:
                continue
            # 防御: LLM 偶发输出 list/dict 等不可哈希值 → list 拆开逐项, 非字符串跳过
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if not isinstance(v, str):
                    continue
                v = v.strip()
                if v and v not in ai_extra_seen[dim]:
                    ai_extra_seen[dim].add(v)
                    dims[dim].append(v)

    # shot_types 基础枚举兜底 (2026-09-02): 纯聚合只能拿到库里已标的值,
    # 新扩充的镜头语言 (中景/全景/跟拍…) 库里没人标过 → 与基础枚举取并集,
    # 导演才选得到; "空镜"已在聚合层洗掉, 不在基础枚举内
    for shot in BASE_SHOT_TYPES:
        if shot not in shot_seen:
            shot_seen.add(shot)
            dims["shot_types"].append(shot)

    return dims


def _collect_text_buckets(
    assets: list[Any],
) -> tuple[list[str], list[str]]:
    """收集 tags / description_zh 双桶, 供后续统一清洗."""
    tags_bucket: list[str] = []
    zh_bucket: list[str] = []
    for asset in assets:
        tags_bucket.extend(asset.tags or [])
        desc = asset.description_zh or ""
        if desc.strip():
            zh_bucket.append(desc)
    return tags_bucket, zh_bucket


def _extract_zh_keywords(zh_bucket: list[str]) -> list[str]:
    """description_zh 简易分词 (无 jieba): 按中文标点/空白切分, 保留 2-8 字中文短语."""
    zh_keywords: list[str] = []
    for desc in zh_bucket:
        for chunk in re.split(r"[,，。；;、！？!?：:\s]+", desc):
            chunk = chunk.strip().strip("""“”"'「」『』、（）()""")
            if not chunk or len(chunk) > 8 or len(chunk) < 2:
                continue
            if not _looks_chinese(chunk):
                continue
            if chunk in _CLEANUP_WORDS or chunk.lower() in _CLEANUP_WORDS:
                continue
            if chunk not in zh_keywords:
                zh_keywords.append(chunk)
    return zh_keywords


def _extract_en_keywords(tags_bucket: list[str]) -> list[str]:
    """tags 英文词清洗 (只保留有画面价值的词, 中文残句整类丢弃)."""
    en_keywords: list[str] = []
    for word in tags_bucket:
        w = word.strip()
        if not w or w.startswith(_CLEANUP_PREFIXES):
            continue
        if _looks_chinese(w):
            continue  # tags 中文 = 脚本残句, 无画面价值
        wl = w.lower()
        if len(wl) <= 1 or wl in _CLEANUP_WORDS or w in _CLEANUP_WORDS:
            continue
        if wl not in en_keywords:
            en_keywords.append(w)
    return en_keywords


def _merge_keywords(
    zh_keywords: list[str], en_keywords: list[str],
) -> list[str]:
    """中文视觉词优先, 英文补充只填剩余空位."""
    keywords = zh_keywords
    for w in en_keywords:
        if len(keywords) >= MAX_WORDS_PER_DIM:
            break
        if w not in keywords:
            keywords.append(w)
    return keywords[:MAX_WORDS_PER_DIM]


def build_vocabulary_pack(db: Session) -> dict[str, Any]:
    """从全库构建关键词词表包 (ID-034).

    纯聚合, 不写文件。维度来源:
      - scenes / shot_types / tone / motion_level / content_density / time_of_day
        优先取打标枚举值 (video_tagging_service 的 ALL_* 枚举), 保证词在枚举内;
      - tags          → 清洗 (`_CLEANUP_WORDS` + 前缀 + 长度>1) 后聚合;
      - description_zh → 简易分词 (按中文标点/空白切分, 保留 2-8 字中文短语)。

    tags / description_zh 每个维度最多取 MAX_WORDS_PER_DIM 个。
    DB 查询失败 / 库为空 → 返回空包 (调用方 fallback)。
    """
    pack: dict[str, Any] = {
        "type": "vocabulary_pack",
        "version": 1,
        "built_at": _utc_now_iso(),
        "dimensions": {},
        "stats": {},
        "_note": "关键词词表包 — 由 AI 打标完成/素材导入/手动脚本自动重建。"
                 "DeepSeek 按维度从词表选词, 本地按硬维度过滤 + 软维度≥75% 命中率碰撞。",
    }

    try:
        assets = (
            db.query(VideoAsset)
            .filter(VideoAsset.file_path.isnot(None))
            .order_by(VideoAsset.ai_tagged_at.desc().nullslast())
            .all()
        )
    except Exception:
        logger.exception("build_vocabulary_pack: DB query failed")
        return pack

    if not assets:
        logger.warning("build_vocabulary_pack: no VideoAsset found, returning empty pack")
        return pack

    dims = _collect_enum_dims(assets)
    tags_bucket, zh_bucket = _collect_text_buckets(assets)

    # ── 视觉词来源分层 (实测 2026-08-07) ──
    #   description_zh = 高质量中文视觉词 (十字路口/车流人流/东方明珠塔/金色圆顶教堂…)
    #   tags 中文      = 全是脚本残句 (29 去重无一有画面价值) → 整类丢弃
    #   tags 英文      = 大量政治/分析抽象词 (strategy/diplomacy/sanctions…) 已由
    #                   _CLEANUP_RAW 定向剔除, 残余少量画面词作补充
    # 顺序: 先中文视觉词, 英文 tags 只在不足时补位 → 中文视觉词不被英文噪音挤掉。
    zh_keywords = _extract_zh_keywords(zh_bucket)
    en_keywords = _extract_en_keywords(tags_bucket)
    dims["keywords"] = _merge_keywords(zh_keywords, en_keywords)

    pack["dimensions"] = dims
    pack["stats"] = {
        "assets": len(assets),
        "total_words": sum(len(v) for v in dims.values()),
        "per_dim": {k: len(v) for k, v in dims.items()},
    }
    logger.info(
        "build_vocabulary_pack: %d assets, %s",
        len(assets),
        pack["stats"]["per_dim"],
    )
    return pack


def rebuild_vocabulary_pack(db: Session) -> dict[str, Any]:
    """构建词表包并写盘 (ID-034)。打标完成 / 素材导入 / 手动脚本调用。

    返回写盘结果, 失败时 raise 由调用方处理。
    """
    pack = build_vocabulary_pack(db)
    VOCABULARY_PACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = VOCABULARY_PACK_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(VOCABULARY_PACK_PATH)
    logger.info(
        "rebuild_vocabulary_pack: wrote %s (%d words)",
        VOCABULARY_PACK_PATH, pack["stats"].get("total_words", 0),
    )
    return pack


def load_vocabulary_pack() -> dict[str, Any] | None:
    """读取词表包 JSON。文件不存在 / 解析失败 → None (调用方 fallback)。"""
    if not VOCABULARY_PACK_PATH.exists():
        logger.warning("load_vocabulary_pack: %s missing", VOCABULARY_PACK_PATH)
        return None
    try:
        return json.loads(VOCABULARY_PACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("load_vocabulary_pack: parse failed")
        return None
