# -*- coding: utf-8 -*-
"""v3 ⑥ 按槽 TTS (1106 用户令 D 刀: TTS 以导演层方案执行, 不自己乱切).

槽位是 K2/TTS/H3 的共同轴 (③④ 定义): 每个 slot 的口播行 = 一个合成块,
块[i] ↔ 槽[i] 1:1 — ⑦ 盖章变查表, 零字数分摊。

实现: 把 script 的 Segment 层按槽重切 (增量手术 — 旧行 selected_for_host=False
保留可回滚, 新行一槽一段 selected=True), 然后复用音频线 _do_tts 全套
(气口治理/包化/manifest/回听复检)。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def slot_texts(doc: dict[str, Any]) -> list[dict[str, str]]:
    """v3 doc → [{sid, text}] (每槽口播 = 该槽 line_refs 的有效行文本按序拼接)。"""
    v3 = doc.get("v3") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    out: list[dict[str, str]] = []
    for s in (v3.get("shots") or []):
        parts = [str(lines[no].get("text") or "").strip()
                 for no in (s.get("line_refs") or [])
                 if no in lines and str(lines[no].get("text") or "").strip()]
        if parts:
            out.append({"sid": str(s["sid"]), "text": "\n".join(parts)})
    return out


def resegment_by_slots(db, script_id: str, slots: list[dict[str, str]]) -> int:
    """Segment 层按槽重切 (增量): 旧选中段退位保留, 新段一槽一段。

    返回新段数。不删任何行 (安全红线); 回滚 = 音频工坊把旧段重新勾选。
    """
    from app.models.content import Segment
    old = (db.query(Segment)
           .filter(Segment.script_id == script_id,
                   Segment.selected_for_host == True)  # noqa: E712
           .all())
    for seg in old:
        seg.selected_for_host = False
    base = max((s.line_index for s in
                db.query(Segment).filter(Segment.script_id == script_id).all()),
               default=-1) + 1
    for i, sl in enumerate(slots):
        db.add(Segment(script_id=script_id,
                       line_index=base + i,
                       text=sl["text"],
                       segment_type="body",
                       selected_for_host=True,
                       host_order=i,
                       control_chars={"v3_slot": i}))   # 槽墙标 (tts_service 见标立断包墙)
    db.commit()
    logger.info("[v3:tts] 段表按槽重切: %d 旧段退位, %d 槽段就位 (script %s)",
                len(old), len(slots), str(script_id)[:8])
    return len(slots)

_PAUSE_MARK = __import__("re").compile(r"\s*-\d+(?:\.\d+)?s-")


def _anchor(text: str) -> str:
    """归一化锚: 只取首行 + 剥气口标记 (-0.8s-) — manifest 文本多行且带标记,
    直接取前 N 字会跨行吃到第二行 (s15/s24/s34/s41 四连误伤实锤)。"""
    first = str(text or "").splitlines()[0] if str(text or "").strip() else ""
    t = _PAUSE_MARK.sub("", first)
    return t.strip().replace("　", "")[:12]


def sync_muted_slots(doc: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, bool]:
    """锚匹配块↔槽, 缺块的槽标 audio_muted (自愈: 恢复勾选后下次刷新自动清)。

    返回 {sid: muted}。匹配键 = 块文本前缀 vs 槽首行前缀 (57/57 实弹验证)。
    纯索引不可用 — 屏一块后全体错位一格 (1106 实锤)。
    """
    v3 = doc.get("v3") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    have = {_anchor(b.get("text")) for b in blocks}
    out: dict[str, bool] = {}
    for s in (v3.get("shots") or []):
        refs = s.get("line_refs") or []
        first = lines.get(refs[0], {}) if refs else {}
        muted = _anchor(first.get("text")) not in have
        if muted != bool(s.get("audio_muted")):
            s["audio_muted"] = muted
    return {str(s["sid"]): bool(s.get("audio_muted"))
            for s in (v3.get("shots") or [])}

