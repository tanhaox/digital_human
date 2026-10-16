# -*- coding: utf-8 -*-
"""huazi_pass — 段落级花字提炼+分配 (1016 用户令 → 1017 W3 入管线).

文字总路由的 h3_text_event 唯一写者 (规划收尾 _finalize_doc 自动执行;
beats/motion 旧内嵌路由已由 director2 收敛于此)。两段式:
  A 段 (每弧一次 LLM, fail-open): 喂料首选弧主旨 metaphor_core (立意单一源,
     缺失才自推导), 提炼 0-3 个 ≤4 字完整关键词 + 口播原词锚 spoken。
  B 段 (确定性代码): spoken 锚落镜 + 同弧去重 + 相邻让位 + 全集密度帽
     (~1/4.5 镜) + text_layer 同词去重 (保 2-3 行/镜设计, 只删与花字同词)。

军规: ID-098 (≤4字主次律) / ID-101 (跨镜上下文) / ID-102 (弧级密度)。"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM = """你是短视频花字设计师。给你一个语义完整段落(弧)的完整口播与主旨, 提炼点睛
关键词。只输出 JSON。"""

_TPL = """# 段落主旨 (规划器立意, 首要依据)
{thesis}

# 段落完整口播(按镜序拼接)
{passage}

# 每镜信息 (sid | 本镜口播 | 时长秒 | 现行花字)
{shots}

# 任务
从段落完整语义提炼 0-3 个点睛花字。

# 硬律
1. text ≤4 个汉字, 必须是完整语义词 (禁止截断残片, "挑三"是"挑三个"的残片;
   跨镜断句时关键词取完整词)。
2. spoken = 口播里真实说出的原词片段 (≤8字), 用于定位落镜; text 本身是原词
   则 spoken = text。
3. 点睛不是常态: 只有主旨/金句/冲突词配花字, 叙事细节不配。
4. 与字幕分工: 字幕打全程, 花字只点睛不重复整句。

# 输出 JSON
{{"keywords": [{{"text": "≤4字", "spoken": "口播原词", "why": "≤12字"}}, ...]}}"""


def _best_shot(kw: dict[str, Any], arc_shots: list[dict[str, Any]]) -> str | None:
    """spoken 锚落镜: 完整包含 > 最长重叠(≥2字)。跨镜断句落在包含原词的镜."""
    spoken = str(kw.get("spoken") or kw.get("text") or "").strip()
    if not spoken:
        return None
    for s in arc_shots:
        if spoken in (s.get("narration") or ""):
            return s["shot_id"]
    best, best_n = None, 0
    for s in arc_shots:
        n = max((len(spoken[i:j]) for i in range(len(spoken))
                 for j in range(i + 2, len(spoken) + 1)
                 if spoken[i:j] in (s.get("narration") or "")), default=0)
        if n > best_n:
            best, best_n = s["shot_id"], n
    return best if best_n >= 2 else None


def enrich_text_route(doc: dict[str, Any]) -> dict[str, str]:
    """弧级花字 pass: 写 h3_text_event (唯一写者) + text_layer 同词去重。

    返回 {sid: 花字} 终案; LLM 失败 fail-open (弧跳过, 不炸 finalize)。"""
    from .config import load as _anim_load
    from .llm import chat_json

    llm_cfg = _anim_load().llm
    arcs: dict[str, list[dict[str, Any]]] = {}
    arc_meta: dict[str, dict[str, Any]] = {}
    for s in doc.get("shots") or []:
        aid = str(s.get("arc_id") or s["shot_id"])
        arcs.setdefault(aid, []).append(s)
    for a in doc.get("arcs") or []:
        arc_meta[str(a.get("arc_id") or "")] = a

    result: dict[str, str] = {}
    dropped: list[str] = []
    for arc, ss in arcs.items():
        passage = "".join((s.get("narration") or "") for s in ss)
        meta = arc_meta.get(arc) or {}
        # 1017 W3: 喂料首选弧主旨 (立意单一源); 缺失自推导并打标
        thesis = str(meta.get("metaphor_core")
                     or meta.get("visual_metaphor") or "").strip()
        if not thesis:
            thesis = "(无规划主旨 — 从口播自行归纳段落主旨)"
            dropped.append(f"{arc}:主旨缺自推导")
        shots_desc = "\n".join(
            f"{s['shot_id']} | {(s.get('narration') or '').strip()} | "
            f"{float((s.get('anim') or {}).get('duration_s') or 0):.1f}s"
            f" | {(s.get('h3_text_event') or {}).get('text') or '-'}"
            for s in ss)
        try:
            out = chat_json(llm_cfg, _SYSTEM,
                            _TPL.format(thesis=thesis, passage=passage,
                                        shots=shots_desc))
        except Exception as exc:  # noqa: BLE001 — fail-open, 不炸规划
            logger.warning("[huazi] arc %s LLM 失败 (跳过): %s", arc, str(exc)[:80])
            continue
        taken: set[int] = set()
        order = {s["shot_id"]: i for i, s in enumerate(ss)}
        for kw in (out.get("keywords") or [])[:3]:
            cjk = [c for c in str(kw.get("text") or "") if "一" <= c <= "鿿"]
            txt = str(kw.get("text") or "").strip()
            if not (0 < len(cjk) == len(txt) <= 4):
                continue
            sid = _best_shot(kw, ss)
            if sid is None:
                dropped.append(f"{arc}「{txt}」(原词未命中)")
                continue
            i = order[sid]
            if any(abs(i - j) <= 1 for j in taken):   # 相邻/同镜 → 让位次优
                alt = next((x["shot_id"] for x in ss
                            if order[x["shot_id"]] not in taken
                            and all(abs(order[x["shot_id"]] - j) > 1 for j in taken)
                            and _best_shot(kw, [x])), None)
                if alt is None:
                    dropped.append(f"{arc}「{txt}」(相邻挤压)")
                    continue
                sid = alt
                i = order[sid]
            if txt in result.values():                 # 全集同词去重
                continue
            result[sid] = txt
            taken.add(i)
        time.sleep(0.4)

    # 全局密度帽 (按镜序, 每 3 镜至多 1 个)
    seq = sorted(result.items(), key=lambda kv: next(
        i for i, s in enumerate(doc["shots"]) if s["shot_id"] == kv[0]))
    kept, last = [], -10
    for sid, txt in seq:
        i = next(i for i, s in enumerate(doc["shots"]) if s["shot_id"] == sid)
        if i - last >= 3:
            kept.append((sid, txt))
            last = i
        else:
            dropped.append(f"{sid}「{txt}」(密度帽)")
    result = dict(kept)

    # 落表: h3_text_event 唯一写者 + text_layer 同词去重 (保 2-3 行设计)
    for s in doc.get("shots") or []:
        if s["shot_id"] in result:
            dur = float((s.get("anim") or {}).get("duration_s") or 3)
            s["h3_text_event"] = {"text": result[s["shot_id"]],
                                  "at_s": round(min(1.5, dur / 3), 1),
                                  "zone": "头顶留白"}
            tl = [t for t in (s.get("text_layer") or [])
                  if str(t.get("text") or "").strip() != result[s["shot_id"]]]
            if len(tl) != len(s.get("text_layer") or []):
                s["text_layer"] = tl
        else:
            s["h3_text_event"] = {}
    if dropped:
        logger.info("[huazi] 让位/未命中: %s", "; ".join(dropped[:8]))
    return result
