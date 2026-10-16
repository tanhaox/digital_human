# -*- coding: utf-8 -*-
"""final_gate — 最终产物唯一闸 (1013 P0 阶段3).

闸门纪律 (用户令: 只验最终产物): 中间态检查全部让位 — writer 镜五件套即
最终产物, fallback 镜器层句即最终产物; 本闸一把尺量两者。分区配额 (宪法)
替代全场一刀切; 锚定链 (opening==keyframe_en) writer 镜硬闸; 主体围栏/单角色
密度迁入终产口径 (原 validate_report 三闸)。

validate_doc (中间态口径) 保留在 fallback 链内部自用; planner 主路切本闸。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import constitution as CST

logger = logging.getLogger(__name__)

_STRIP_RE = re.compile(r"[\s，。！？；：、,.!?:;\"'“”‘’（）()\[\]【】]+")
# 1017 W4: 归一 shots.SINGLE_BEAT_FALLBACK (旧二变体一并识别兼容历史 doc)
from .shots import SINGLE_BEAT_FALLBACK as _SBF
_PLACEHOLDER_BEATS = ("the scene evolves with subtle ambient motion", _SBF)


def _norm(s: str) -> str:
    return _STRIP_RE.sub("", s or "")


def _bigrams(s: str) -> set[str]:
    t = _norm(s)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _jaccard(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / len(A | B) if (A | B) else 0.0


def zone_quota_issues(doc: dict[str, Any], skip_placeholders: bool = True) -> list[str]:
    """分区人物配额 (宪法 scene_min_present; brand 豁免; fallback 占位镜豁免)。

    1014 一把尺: 本函数是人物在场配额的唯一尺 — 场级闸 (validate_doc)、
    终产闸 (validate_final)、器层注入帽 (_quota_person_inject) 三消费者同源。
    旧版三处两把尺 (生成按 ⌈n/4⌉、验收按分区 0.6): 确定性补人永远停在
    ⌈n/4⌉, 终闸按分区要人 → 重试无穷 (ep4 A6 16镜补到4即停 vs 需≥10 实锤)。"""
    import json as _json
    import math as _math

    from .director2 import _bible_cache_path, _has_person_words
    words = list(CST.person_words())
    try:
        bt = str(doc.get("book_title") or "")
        if bt:
            _bb = _json.loads(_bible_cache_path(bt).read_text(encoding="utf-8"))
            words += [str(c.get("name") or "").strip() for c in (_bb.get("characters") or [])
                      if str(c.get("name") or "").strip()]
    except Exception:  # noqa: BLE001
        pass
    tc = 0.0
    try:
        from .config import load
        tc = float(load().brand_card.opening_sec)
    except Exception:  # noqa: BLE001
        pass
    # 按 (场, 分区) 聚合 — 一场可跨 hook/mid
    buckets: dict[tuple[str, str], list] = {}
    for s in doc.get("shots") or []:
        if s.get("brand_card") or (skip_placeholders and s.get("_kf_missing")):
            continue
        z = CST.zone_of(s, doc, tc)
        if z == "brand":
            continue
        buckets.setdefault((str(s.get("arc_id") or ""), z), []).append(s)
    issues: list[str] = []
    for (a, z), ss in sorted(buckets.items()):
        need_frac = CST.zone_value("person_quota", "scene_min_present", z) or 0.25
        need = len(ss) if need_frac >= 1 else max(1, _math.ceil(len(ss) * need_frac))
        np_ = sum(1 for s in ss if _has_person_words(str(s.get("keyframe_zh") or ""), words))
        if np_ < need:
            no = "/".join(s["shot_id"] for s in ss
                          if not _has_person_words(str(s.get("keyframe_zh") or ""), words))[:4]
            issues.append(f"分区人物配额: 场{a}[{z}] {len(ss)}镜仅{np_}镜有人物 (需≥{need}) — 补 {no}")
    return issues


def anchor_chain_issues(doc: dict[str, Any]) -> list[str]:
    """锚定链 (writer 镜硬闸): opening_desc 逐字 == keyframe_en; beats 非占位。"""
    issues: list[str] = []
    for s in doc.get("shots") or []:
        if s.get("brand_card") or str(s.get("writer_source") or "") != "writer":
            continue
        en = str(s.get("keyframe_en") or "").strip()
        od = str(((s.get("anim") or {}).get("opening_desc")) or "").strip()
        if en and od != en:
            issues.append(f"{s['shot_id']} 锚定链断: opening_desc ≠ keyframe_en")
        _anim = s.get("anim") if isinstance(s.get("anim"), dict) else {}
        _beats = _anim.get("beats") if isinstance(_anim.get("beats"), list) else []
        b0 = _beats[0] if _beats and isinstance(_beats[0], dict) else {}
        mot = str(b0.get("motion") or "")
        if any(p in mot for p in _PLACEHOLDER_BEATS):
            issues.append(f"{s['shot_id']} beats 占位串 (H3 无实动指令)")
    return issues


def fence_issues(doc: dict[str, Any], bible: dict[str, Any] | None = None) -> list[str]:
    """主体围栏 + 单角色密度 (终产口径, 原 validate_report 三闸之二)。"""
    from .registers import author_of
    bible = bible or {}
    author = author_of(doc, bible)
    tc = 0.0
    try:
        from .config import load
        tc = float(load().brand_card.opening_sec)
    except Exception:  # noqa: BLE001
        pass
    look_key = ""
    for c in bible.get("characters") or []:
        if str(c.get("name") or "") == author and c.get("look"):
            look_key = str(c["look"]).split("，")[0][:6]
            break
    issues: list[str] = []
    counts: dict[str, int] = {}
    total = 0
    for s in doc.get("shots") or []:
        if s.get("brand_card"):
            continue
        total += 1
        kf = str(s.get("keyframe_zh") or "")
        reg = str(s.get("register") or "")
        # 1013 P0 试点回喂②: 围栏分区化 — 圣经"作者=讲述化身"两角色焊死, 围栏
        # 分不开开场讲述人与中段作者刷屏 (A2 钟摆死循环实锤)。hook=讲述人
        # 主场豁免 (老谭读书开场产品形态); mid/case 仍只准引用镜。
        _z = CST.zone_of(s, doc, tc)
        if _z == "hook":
            pass  # 讲述人主场豁免
        elif reg != "author_cite" and (author and author in kf or look_key and look_key in kf):
            # 试点回喂③: 圣经定义作者=常驻讲述锚点, mid 区讲述形态成立 (A5 工具箱
            # 讲解两把都押老谭实锤) — 降软警告 (⚠ 不阻断); 反刷屏由下方单角色
            # 密度帽 (≤25% 集级, 硬) 兜底, 密度帽才是 MTV 铁律的本体。
            issues.append(f"⚠ {s['shot_id']} 作者出现于非引用镜 (讲述形态, 密度帽监管)")
        for c in bible.get("characters") or []:
            nm = str(c.get("name") or "")
            if nm and nm in kf and reg not in ("author_cite",):
                counts[nm] = counts.get(nm, 0) + 1
    cap = CST.zone_value("person_quota", "episode_max_single_char", "mid")
    cap = cap if isinstance(cap, (int, float)) else 0.25
    if total:
        for nm, n in sorted(counts.items(), key=lambda x: -x[1]):
            if n > max(2, total * cap) and nm != author:
                issues.append(f"人物密度: {nm} 出镜 {n}/{total} 超 {cap:.0%} (案例run豁免外)")
                break
    return issues


def clone_and_jaccard_issues(doc: dict[str, Any], warn_p90: float = 0.2) -> tuple[list[str], list[str]]:
    """克隆 (硬) + 相邻 Jaccard (软, HBO 级散文偶有合法呼应不硬杀)。"""
    hard: list[str] = []
    soft: list[str] = []
    shots = sorted((s for s in doc.get("shots") or [] if not s.get("brand_card")),
                   key=lambda s: float(s.get("t_start") or 0))
    seen: dict[str, str] = {}
    sims: list[float] = []
    prev = None
    for s in shots:
        zh = str(s.get("keyframe_zh") or "").strip()
        if not zh:
            continue
        if zh in seen:
            hard.append(f"{s['shot_id']} 与 {seen[zh]} 逐字克隆")
        seen.setdefault(zh, str(s.get("shot_id")))
        if prev is not None:
            sims.append(_jaccard(prev, zh))
        prev = zh
    if len(sims) >= 5:
        sims.sort()
        p90 = sims[max(0, int(len(sims) * 0.9) - 1)]
        if p90 > warn_p90:
            soft.append(f"相邻镜相似度 p90={p90:.2f} > {warn_p90} (构图多样性不足, 人检)")
    return hard, soft


def validate_final(doc: dict[str, Any], bible: dict[str, Any] | None = None,
                   segs: list[dict] | None = None) -> list[str]:
    """唯一终产闸: 时间轴 (复用 validate_doc 硬段) + 分区配额 + 锚定链 + 克隆 +
    围栏密度 + Jaccard 软警告 (带 ⚠ 前缀, 进 plan_warnings 不阻断)。

    1014 一把尺后 validate_doc 的配额段与 zone_quota 同源 — 去重防同违规双报。"""
    from .director2 import validate_doc  # 时间轴/场窗/花字/时长等硬段复用
    issues: list[str] = list(validate_doc(doc))
    issues += zone_quota_issues(doc)
    issues += anchor_chain_issues(doc)
    issues += fence_issues(doc, bible)
    hard, soft = clone_and_jaccard_issues(doc)
    issues += hard
    issues += [f"⚠ {w}" for w in soft]
    return list(dict.fromkeys(issues))
