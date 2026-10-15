# -*- coding: utf-8 -*-
"""entity_extractor — 素材实体层 (2026-08-27, 素材层 2.0 第 1 期).

用户诊断: 素材层 40 分的根因 = 源类型错配 + 检索与口播稿脱节 —
特朗普演讲/日本首相 Pexels(图库) 根本没有, 航母有也是道具级;
9 维词表全是风格维度, 无实体维度。

本模块: 口播稿 → 实体清单 (人/事/装备/机构/概念), 按类型路由素材源 —
  person/military/event/org → 油管精准截取 / DVIDS (第 2 期接线)
  concept/place 通用意象    → Pexels (现有, 降兜底位)
实体回库带标签后本地碰撞按实体优先命中 (第 3 期), 素材复利。

用法:
  from app.services.entity_extractor import extract_material_entities
  ents = extract_material_entities(口播全文)   # [{"name","type","queries":{...},"why"}]
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 实验开关 (2026-08-27 用户令: 实验成功前不并入系统内部) —
# False 时 _persist_plan 的实体抽取挂钩静默跳过; 模块函数仍可供离线实验。
# 2026-08-28 开闸: 小米稿(15f7df6e)重配画面 = 验证实验 (用户令"测试再配画面这一步")。
ENTITY_LAYER_ENABLED = True

# 实体类型 → 素材源路由 (第 2 期接下载管线, 第 1 期先出需求单)
SOURCE_ROUTES: dict[str, list[str]] = {
    "person": ["youtube", "dvids"],     # 真实人物: 演讲/新闻画面 (军事人物 DVIDS)
    "military": ["dvids", "youtube"],   # 装备: 美军公共领域真实影像
    "event": ["youtube"],               # 事件/新闻现场
    "org": ["youtube"],                 # 公司/机构: 发布会/工厂
    "place": ["youtube", "pexels"],     # 地点: 实拍优先, 通用兜底
    "concept": ["pexels"],              # 抽象概念: 图库主场
}

ENTITY_PROMPT = """你是短视频素材采购员。读下面这篇口播稿，提取所有需要**真实画面**的实体，并给出素材源搜索词。只输出 JSON。

# 实体类型 (route 决定素材源)
- person: 真实人物 (特朗普/马斯克/日本首相…) — 需要真人画面
- military: 军事装备 (航母/F-35/驱逐舰/导弹…) — 需要真实装备影像
- event: 具体事件/新闻 (关税战/芯片禁令/大选/发布会…) — 需要现场画面
- org: 公司/机构 (英伟达/台积克/五角大楼…) — 需要实景/发布会
- place: 具体地点 (台海/硅谷/底特律…) — 实拍优先
- concept: 抽象概念 (算力/供应链/通胀/资本…) — 图库意象即可

# 规则
- 只提口播稿**点名**的实体; 稿里没提的不猜 (宁缺毋滥)
- person 仅限**公众人物** (政要/企业家/名人) — 稿内 anecdotes 虚构人物/普通市民/受访者 (如"义乌老陈""一位工程师") 不算, 他们搜不到真实画面
- **国家名不算实体** (美国/中国/日本每篇都有, 无检索价值) — 除非绑定具体事件场景; 具体城市可提 (底特律/义乌)
- 每实体给 queries: {"youtube": "英文搜索词(新闻/官方频道口径)", "pexels": "英文图库词(仅concept/place需要)", "dvids": "英文军语(仅military)"}
- 出现多次的同一实体只提一次; 优先提画面权重高的 (主角人物/核心装备/关键事件)
- concept 不超过 3 个 (太多=没重点); 全稿没有真实画面实体就返回空 entities 数组

# 输出
{"entities": [{"name": "中文名", "type": "person", "queries": {...}, "why": "口播中的上下文短语(≤20字)"}]}"""


def extract_material_entities(script_text: str) -> list[dict[str, Any]]:
    """口播全文 → 素材实体清单 (flash 单跑, 失败返回 []).

    每项: {name, type, queries{source:搜索词}, why, routes[素材源]}
    """
    text = (script_text or "").strip()
    if not text:
        return []
    try:
        from .boost_service import _call, _extract_json
        raw = _call(ENTITY_PROMPT + "\n\n【口播稿】\n" + text[:8000],
                    json_mode=True, max_tokens=2000, temperature=0.2)
        data = _extract_json(raw)
        items = (data or {}).get("entities") or []
    except Exception as exc:
        logger.warning("[entity] 抽取失败(素材需求单缺省): %s", exc)
        return []

    out: list[dict[str, Any]] = []
    # 显著性代码侧兜底 (LLM 偶尔仍会提): 国家名/纯国名 place 丢弃 —
    # 每篇都有无检索价值; anecdotes 人物由提示词拦, 这里再拦泛化国家。
    _GENERIC_PLACES = {"美国", "中国", "中国大陆", "日本", "俄罗斯", "欧盟", "欧洲", "亚洲", "全球", "西方"}
    for it in items:
        etype = str(it.get("type") or "").strip()
        name = str(it.get("name") or "").strip()
        if etype not in SOURCE_ROUTES or not name:
            continue
        if etype == "place" and name in _GENERIC_PLACES:
            continue
        out.append({
            "name": name,
            "type": etype,
            "queries": {k: v for k, v in (it.get("queries") or {}).items() if v},
            "why": str(it.get("why") or "")[:40],
            "routes": SOURCE_ROUTES[etype],
        })
    return out


def match_slot_entities(slot_text: str, entities: list[dict[str, Any]]) -> list[str]:
    """slot 口播上下文 ↔ 实体名匹配 → 该 slot 关联的实体名 (本地碰撞实体维度用)."""
    if not slot_text or not entities:
        return []
    hit = [e["name"] for e in entities if e["name"] and e["name"] in slot_text]
    # 别名兜底: 名字≥2 字且 slot 里出现
    if not hit:
        hit = [e["name"] for e in entities
               if len(e["name"]) >= 2 and e["name"] in slot_text[:300]]
    return hit


def promote_person_slots(db, job, entities: list[dict[str, Any]]) -> int:
    """人物 slot 提升 (2026-08-30): slot 挂的人物实体在库里有真画面 → hf 文字卡转 broll.

    根因: 规划 LLM 不知道素材库有什么, 高市早苗的关键 slot 被排成 hf_title
    文字卡 — 她的镜头永远上不了片。此处确定性提升: person 类实体且靶心有货,
    hf_* slot → broll_pexels (实体随行, 靶心直接命中官片)。返回转换数。
    """
    from ..models import DirectorSlot
    person_ents = [e for e in entities if e.get("type") == "person"]
    if not person_ents:
        return 0
    from .asset_matcher import match_entity_bullseye
    # 只提升"库里有货"的人物
    stocked: dict[str, list[str]] = {}
    for e in person_ents:
        qs = [q.lower() for q in (e.get("queries") or {}).values() if q]
        qs.append(e["name"].lower())
        try:
            if match_entity_bullseye(db, qs, limit=1):
                stocked[e["name"]] = qs
        except Exception:
            continue
    if not stocked:
        return 0
    n = 0
    for s in job.slots:
        if not (s.workflow or "").startswith("hf"):
            continue
        # 来源声明卡豁免 (2026-08-30 用户反馈: 尾部资料页引用卡被误换成人物镜头)
        rc = ((s.params_json or {}).get("render_config") or {})
        if rc.get("style") in ("references", "hf-source-v1"):
            continue
        ents = (s.params_json or {}).get("entities") or []
        hit_ent = next((nm for nm in stocked if nm in ents), None)
        if not hit_ent:
            continue
        params = dict(s.params_json or {})
        params["keywords"] = [hit_ent] + [e for e in ents if e != hit_ent][:2]
        s.workflow = "broll_pexels"
        s.visual_type = "broll_pexels"
        s.params_json = params
        n += 1
        logger.info("[entity] slot %s 人物提升 hf→broll (实体 %s 有库藏)",
                    s.slot_index, hit_ent)
    if n:
        db.commit()
    return n


def requirement_sheet(entities: list[dict[str, Any]]) -> str:
    """需求单文本 (人读/落 trace): 哪些实体走哪个源、搜什么."""
    if not entities:
        return "无实体 (纯概念稿, Pexels 兜底即可)"
    lines = []
    for e in entities:
        q = " | ".join(f"{k}:{v}" for k, v in e["queries"].items())
        lines.append(f"  [{e['type']}] {e['name']} → {'/'.join(e['routes'])}  {q}")
    return "\n".join(lines)
