# -*- coding: utf-8 -*-
"""jy_effect_library — J2 效果目录查询封装 (2026-08-27).

设计: docs/design/剪映草稿产线-设计方案.md §6 J2。
数据: data/jy_effect_catalog.json (scripts/build_jy_effect_catalog.py 生成, 快照更新可重跑)
      + config/jy_effect_aliases.json (人工对号, baseline 预设标记)
      + pyJianYingDraft 注册表 (3576 条, 生成草稿时取枚举成员直接用)

消费方:
- R 配方 (scripts/demo_r_recipes.py) → find("渐显", "anim_in") 拿 resource_id
- 导演台 slot effect_recipe 字段 (J2 后续) → recommend(category) 高频池
- 口播字幕/强调位 → top("anim_in") 语义挑选

坑: site-packages 里 pyJianYingDraft 双大小写目录并存, 只认混合大小写导入名。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_CATALOG = _ROOT / "data" / "jy_effect_catalog.json"
_ALIASES = _ROOT / "config" / "jy_effect_aliases.json"

CATEGORIES = ("effect", "video_effect", "sticker", "transition", "anim_in", "anim_out", "anim_loop")


@lru_cache(maxsize=1)
def _load() -> dict:
    """catalog + aliases 合并视图: {category: [ {key,name,count,baseline?}, ... ]}."""
    if not _CATALOG.exists():
        logger.warning("[jy_effect_lib] catalog 缺失, 先跑 scripts/build_jy_effect_catalog.py")
        return {}
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    aliases = {}
    if _ALIASES.exists():
        aliases = json.loads(_ALIASES.read_text(encoding="utf-8"))
    view: dict = {"_meta": catalog.get("_meta", {}), "anim_dur_ms": catalog.get("anim_dur_ms", {}),
                  "per_template": catalog.get("per_template", {})}
    for cat, rows in (catalog.get("aggregated") or {}).items():
        merged = []
        for r in rows:
            if r["key"] in aliases:
                r = {**r, **{k: v for k, v in aliases[r["key"]].items()
                             if k in ("name", "baseline")}}
            merged.append(r)
        view[cat] = merged
    return view


def top(category: str, n: int = 20, include_baseline: bool = False) -> list[dict]:
    """该类别按使用模板数降序的头部效果 (推荐池).

    include_baseline=False 默认滤掉卖家基线预设 (智能调色等每模板必带项)。
    """
    rows = _load().get(category) or []
    if not include_baseline:
        rows = [r for r in rows if not r.get("baseline")]
    return rows[:n]


def find(name: str, category: str | None = None) -> dict | None:
    """按显示名精确/包含匹配 → {key, name, category, count}. 精确优先, 同名多类别都给."""
    view = _load()
    cats = [category] if category else [c for c in view if c in CATEGORIES]
    exact: dict | None = None
    partial: dict | None = None
    for cat in cats:
        for r in view.get(cat, []):
            if r["name"] == name:
                exact = {**r, "category": cat}
                break
            if partial is None and name in r["name"]:
                partial = {**r, "category": cat}
        if exact:
            break
    return exact or partial


def resolve(key: str) -> dict | None:
    """按资源ID/短ID反查 → {name, category, count, baseline?}."""
    view = _load()
    for cat in CATEGORIES:
        for r in view.get(cat, []):
            if r["key"] == key:
                return {**r, "category": cat}
    return None


def per_template(template: str) -> dict[str, list[str]]:
    """某模板用了哪些效果 → {category: [name, ...]}."""
    return (_load().get("per_template") or {}).get(template) or {}


def unresolved() -> list[str]:
    """仍未对号的 ID (≥3 模板) — 补进 config/jy_effect_aliases.json 即可."""
    return (_load().get("_meta") or {}).get("unresolved") or []


@lru_cache(maxsize=1)
def _pyjyd_tables() -> tuple[dict[str, object], dict[str, object]]:
    """(name→枚举成员, 短effect_id→枚举成员) — 生成草稿时直接用枚举成员."""
    by_name: dict[str, object] = {}
    by_id: dict[str, object] = {}
    try:
        import pyJianYingDraft as m  # 双大小写目录坑: 只认混合大小写
    except ImportError:
        logger.warning("[jy_effect_lib] pyJianYingDraft 不可导入")
        return by_name, by_id
    for cls in (getattr(m, "VideoSceneEffectType", None),
                getattr(m, "VideoCharacterEffectType", None),
                getattr(m, "TransitionType", None),
                getattr(m, "TextLoopAnim", None)):
        if cls is None:
            continue
        for x in cls:
            # AnimationMeta 无 name/effect_id 字段 (侦查 2026-08-27), getattr 容错
            nm = getattr(x.value, "name", None)
            if nm:
                by_name.setdefault(nm, x)
            eid = getattr(x.value, "effect_id", None)
            if eid:
                by_id.setdefault(eid, x)
    return by_name, by_id


def pyjyd(name: str):
    """显示名 → pyJianYingDraft 枚举成员 (草稿构建直接 add_effect(成员))."""
    by_name, by_id = _pyjyd_tables()
    return by_name.get(name) or by_id.get(name)
