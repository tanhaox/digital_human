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
        parts = [_MARKER_RE.sub("", str(lines[no].get("text") or "")).strip()
                 for no in (s.get("line_refs") or []) if no in lines]
        parts = [p for p in parts if p]
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
# 1108 ep6 实锤: 拆书 LLM 自发明的行内标注 (【大字字幕】) 混进稿 — 全链照抄到
# TTS 被念出。合法标记全是行首且分诊已路由, 行内【..】= 必是发明物 → 三处统一剥
_MARKER_RE = __import__("re").compile(r"【[^】]*】")


def _strip_marks(text: str) -> str:
    """剥气口标记 + 行内【..】发明标注 (念稿/锚/回填共用)。"""
    return _MARKER_RE.sub("", _PAUSE_MARK.sub("", str(text or "")))


def _anchor(text: str) -> str:
    """归一化锚: 只取首行 + 剥气口/标注 — manifest 文本多行且带标记,
    直接取前 N 字会跨行吃到第二行 (s15/s24/s34/s41 四连误伤实锤)。"""
    first = str(text or "").splitlines()[0] if str(text or "").strip() else ""
    t = _strip_marks(first)
    return t.strip().replace("　", "")[:12]


def sync_muted_slots(doc: dict[str, Any], blocks: list[dict[str, Any]]
                    ) -> tuple[dict[str, bool], dict[str, bool], bool]:
    """锚匹配块↔槽, 区分三种失配 (1106 用户问: 是修改了还是摒弃了?):

    - unaligned = 包不是按槽切的 (1108 ep6 实锤: 旧语义切块包曾把 20/40 槽
      误标「屏弃」并落盘毒标) → 三不写: 不标屏弃/不回填/不落盘;
      正道 = 🔁 按槽重生成。判据 = 孤儿块: 有块首行连模糊 (≥0.55) 都锚不到
      任何槽首 — 真按槽切的包每块必起于槽首 (用户屏弃 = 块缺席, 不产孤儿)。
    - muted  = 块真没了 (用户 🔇屏蔽) → 槽标 audio_muted, K2/盖章/H3 跳过
    - drifted = 块在但首行被 ✎编辑改了 → **第五处同步**: 槽口播行回填音频文本
      (音频侧微调字是 ⑤ 的正常精修, v3.lines 就是念出来的稿; 回填后锚自然对齐,
      下次读取不再失配)。返回 ({sid: muted}, {sid: drifted}, unaligned)。
    """
    import difflib
    import re as _re2
    _pm = _re2.compile(r"\s*-\d+(?:\.\d+)?s-")
    v3 = doc.get("v3") or {}
    lines = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    blk_firsts = [(_strip_marks(str(b.get("text") or "").splitlines()[0]).strip()
                   if str(b.get("text") or "").strip() else "")
                  for b in blocks]
    muted_out: dict[str, bool] = {}
    drift_out: dict[str, bool] = {}
    _slot_keys = []
    for s in (v3.get("shots") or []):
        _r0 = (s.get("line_refs") or [None])[0]
        _ft = lines.get(_r0, {}).get("text") if _r0 is not None else None
        _k = _anchor(_ft) if str(_ft or "").strip() else ""
        if _k:
            _slot_keys.append(_k)
    if not blocks or any(
            not t or not any(
                difflib.SequenceMatcher(None, k, t[:len(k) + 4]).ratio() >= 0.55
                for k in _slot_keys)
            for t in blk_firsts):
        return {}, {}, True                      # 未对齐: 三不写
    for s in (v3.get("shots") or []):
        refs = s.get("line_refs") or []
        first = lines.get(refs[0], {}) if refs else {}
        key = _anchor(first.get("text"))
        hit = key in {_anchor(t) for t in blk_firsts}
        sid = str(s["sid"])
        if hit:
            muted_out[sid] = False; drift_out[sid] = False
            s["audio_muted"] = False
            continue
        # 失配分型: 模糊近邻 = 改字漂移; 全无 = 真屏弃
        best = max(blk_firsts, key=lambda t: difflib.SequenceMatcher(
            None, key, t[:len(key) + 4]).ratio()) if blk_firsts else ""
        ratio = difflib.SequenceMatcher(None, key, best[:len(key) + 4]).ratio()
        _pref = next((i for i, (a, b) in enumerate(zip(key, best)) if a != b),
                     min(len(key), len(best)))
        # 门槛三件套: 相似度 + 共享前缀≥2 + 槽首行≥6字 — 短句偶然相似
        # (第二句。vs 第一句。 ratio 0.67) 会把改字判定打穿覆盖正文 (实锤)
        if ratio >= 0.55 and _pref >= 2 and len(key) >= 6:
            drift_out[sid] = True; muted_out[sid] = False
            s["audio_muted"] = False
            # 第五处同步: 槽内各行 ← 音频块行 (剥气口标记; 行数一致才逐行, 否则只回填首行)
            blk = next(b for b in blocks
                       if _strip_marks(str(b.get("text") or "").splitlines()[0]).strip() == best)
            blk_lines = [_strip_marks(x).strip()
                         for x in str(blk.get("text") or "").splitlines() if x.strip()]
            refs_eff = [no for no in refs if str(lines.get(no, {}).get("text") or "").strip()]
            if len(blk_lines) == len(refs_eff):
                for no, txt in zip(refs_eff, blk_lines):
                    if lines[no].get("text") != txt:
                        lines[no]["text"] = txt
            elif refs_eff:
                lines[refs_eff[0]]["text"] = blk_lines[0] if blk_lines else first.get("text")
        else:
            muted_out[sid] = True; drift_out[sid] = False
            s["audio_muted"] = True
    return muted_out, drift_out, False

