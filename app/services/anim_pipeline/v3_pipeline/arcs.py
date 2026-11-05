# -*- coding: utf-8 -*-
"""v3 ② 立意 (V2) — 语态驱动切弧 + 弧界执法 + LLM 只做创作.

分层纪律 (与 ① 同构):
- 确定性层 = 骨架与执法: 连续同语态行聚成「稿模块」; 弧必须单一语态
  (= 弧界落模块边界, 用户令) — 违者红痕拒放行, 不靠 LLM 自觉。
- LLM 层 = 创作: 每弧产出 视觉世界(world) + 隐喻核心(A=B); 超长评论段可建议
  在行边界内再拆 (拆点仍须单语态)。

弧型 (画面模式, 与 ③④ 消费):
  narr=叙事隐喻弧 / quote=书证镜(书的视角, 只限原话直出) / persona=人设镜(老谭亲身经历)
  hook=系列钩子 / cta=行动召唤 / brand=品牌位 — 后三者非叙事, 不占立意世界。
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

TYPE_OF_MODE = {
    "comm": "narr", "quote": "quote", "persona": "persona",
    "hook": "hook", "cta": "cta", "brand": "brand",
}
NON_NARRATIVE = ("hook", "cta", "brand")     # 非叙事位: 系列冻结语言, 不分配立意
CHARS_PER_SEC = 4.5                          # 中文口播估时 (与 ① 预估同口径)
SPLIT_SUGGEST_LINES = 15                     # 单语态段超过此值, 提示可在段内再拆


def modules_of(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """确定性稿模块: 连续同语态的有效行聚一段。

    连续判定看「上一个有效行的语态」而非行号相邻 — 中间夹空行是常态
    (ep5 实锤: 逐行空行把同类语态切成 36 个单行模块)。段标/卡/图指令
    是**硬断点** (节目段落边界); 空行/未分诊是软跳过。
    """
    mods: list[dict[str, Any]] = []
    last_mode: str | None = None
    for ln in lines:
        mode = str(ln.get("mode") or "unknown")
        if mode in ("section", "card", "img", "note"):
            last_mode = None            # 硬断点
            continue
        if not str(ln.get("text") or "").strip() or mode == "unknown":
            continue                     # 软跳过 (不断)
        if mods and last_mode == mode:
            mods[-1]["no_end"] = ln["no"]
            mods[-1]["lines"].append(ln)
        else:
            mods.append({"mode": mode, "no_start": ln["no"], "no_end": ln["no"],
                         "lines": [ln]})
        last_mode = mode
    return mods


def est_seconds(lines: list[dict[str, Any]]) -> float:
    chars = sum(len(str(l.get("text") or "").strip()) for l in lines)
    return round(chars / CHARS_PER_SEC, 1)


def build_skeleton(mods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """骨架 (确定性): 一模块一弧, 统一 A1..An 编号 + 弧型 + 行区间 + 预估时长。"""
    arcs: list[dict[str, Any]] = []
    for i, m in enumerate(mods, 1):
        arcs.append({
            "arc_id": f"A{i}",
            "type": TYPE_OF_MODE.get(m["mode"], "narr"),
            "mode": m["mode"],
            "no_start": m["no_start"],
            "no_end": m["no_end"],
            "line_count": len(m["lines"]),
            "est_s": est_seconds(m["lines"]),
            "narrative": "", "world": "", "metaphor": "",
        })
    return arcs


def effective_nos(lines: list[dict[str, Any]]) -> set[int]:
    """参与分弧的有效行 (段标/卡图指令/未分诊/空行除外)。"""
    return {ln["no"] for ln in lines
            if str(ln.get("text") or "").strip()
            and str(ln.get("mode") or "") not in ("section", "card", "img", "note", "unknown")}


def boundary_issues(arcs: list[dict[str, Any]],
                    lines: list[dict[str, Any]]) -> list[str]:
    """执法 (用户令: 弧界必须落模块边界):
    ① 弧内单一语态 (= 不跨语态, 即弧界只在模块边界)
    ② 有效行恰好归属一弧 (无遗漏/无重复/无归属到段标等无效行)
    """
    mode_of = {ln["no"]: str(ln.get("mode") or "unknown") for ln in lines}
    valid = effective_nos(lines)
    issues: list[str] = []
    seen: dict[int, str] = {}
    for a in sorted(arcs, key=lambda x: int(x.get("no_start") or 0)):
        s, e = int(a.get("no_start") or 0), int(a.get("no_end") or 0)
        aid = a.get("arc_id")
        if s <= 0 or e < s:
            issues.append(f"{aid}: 行区间非法 [{s}, {e}]")
            continue
        nos = [no for no in range(s, e + 1) if no in valid]
        if not nos:
            issues.append(f"{aid}: 区间内无有效行 [{s}, {e}]")
            continue
        modes = {mode_of.get(no) for no in nos}
        if len(modes) > 1:
            issues.append(f"{aid}: 跨语态 {sorted(modes)} — 弧界未落模块边界")
        for no in nos:
            if no in seen:
                issues.append(f"L{no} 同时属于 {seen[no]} 与 {aid} (重复归属)")
            seen[no] = aid
    missing = sorted(valid - set(seen))
    if missing:
        prev = missing[0]
        span = [prev]
        for no in missing[1:]:
            if no == prev + 1:
                span.append(no)
            else:
                issues.append(f"L{span[0]}-L{span[-1]} 未归属任何弧")
                span = [no]
            prev = no
        issues.append(f"L{span[0]}-L{span[-1]} 未归属任何弧")
    return issues


_SYS = """你是短视频系列的分镜立意导演。输入是一集的稿件模块 (每行: 行号|语态|文本),
以及系统已确定的弧切分骨架 (弧号|语态|行区间|行数)。你的任务**只做创作, 不改切分**:

为每个弧输出:
- world: 视觉世界 (本弧画面构成的物理场景, 8-20 字; 书证弧=书的视角/引文排印,
  人设弧=老谭出镜讲自己的亲身经历, 叙事弧=一个具体的物理装置世界)
- metaphor: 立意核心, 必须写成 A=B 映射 (信任波动=三阶段路径…), 40-80 字
- narrative: 本弧讲什么, 一句 15-30 字

铁律:
1. 相邻弧的视觉世界禁止重复 (轮换宪法)。
2. 叙事弧的 world 必须是可拍的物理装置/机械/空间, 禁抽象概念。
3. 非叙事位 (钩子/行动召/品牌) 的 world 填空串 ""。
4. 若某段超过 15 行, 可在 hints 里建议在语义完整处拆成两弧 (只写建议, 不改骨架)。

严格 JSON: {"arcs":[{"arc_id":"A1","narrative":"...","world":"...","metaphor":"..."}],
"hints":["..."]}"""


def _llm_fill(arcs: list[dict[str, Any]], lines: list[dict[str, Any]],
              book_title: str, ep: int) -> tuple[dict[str, dict[str, str]], list[str]]:
    """LLM 创作层: 只填 world/metaphor/narrative; 失败抛异常由调用方退骨架。"""
    from app.services.llm_service import get_llm_service, _parse_json
    text_of = {ln["no"]: str(ln.get("text") or "") for ln in lines}
    body_lines = []
    for a in arcs:
        seg = " / ".join(text_of.get(no, "") for no in
                         range(a["no_start"], min(a["no_end"], a["no_start"] + 6) + 1))
        body_lines.append(f'{a["arc_id"]}|{a["mode"]}|L{a["no_start"]}-L{a["no_end"]}'
                          f'|{a["line_count"]}行|{seg[:140]}')
    body = f"《{book_title}》第{ep}集\n\n" + "\n".join(body_lines)
    raw = get_llm_service().chat(_SYS, body, model="flash", temperature=0.6,
                                 response_format={"type": "json_object"}, tag="v3_arcs")
    data = _parse_json(raw)
    out: dict[str, dict[str, str]] = {}
    for row in data.get("arcs") or []:
        aid = str(row.get("arc_id") or "")
        if aid:
            out[aid] = {"narrative": str(row.get("narrative") or "")[:120],
                        "world": str(row.get("world") or "")[:40],
                        "metaphor": str(row.get("metaphor") or "")[:200]}
    return out, [str(h)[:120] for h in (data.get("hints") or [])][:6]


def run(job: dict[str, Any]) -> None:
    """v3_arcs 相位本体: 读 v3.lines → 模块 → 骨架 → LLM 创作 → 执法 → 落盘。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.config import load

    cfg = load()
    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    lines = v3.get("lines") or []
    if not lines:
        raise SystemExit("未分诊 — 先跑 ① 文稿分诊")

    mods = modules_of(lines)
    if not mods:
        raise SystemExit("无有效语态行 — 回 ① 分诊页补标语态")
    arcs = build_skeleton(mods)

    job["progress"] = {"done": 0, "total": 1, "current": "LLM 立意创作中"}
    hints: list[str] = []
    try:
        filled, hints = _llm_fill(arcs, lines, doc.get("book_title") or job["book_title"], job["ep"])
        for a in arcs:
            f = filled.get(a["arc_id"]) or {}
            if f:
                a["narrative"] = f["narrative"]
                a["world"] = f["world"]
                a["metaphor"] = f["metaphor"]
        logger.info("[v3:arcs] LLM 立意 %d/%d 弧 ✓", sum(1 for a in arcs if a["metaphor"]), len(arcs))
    except Exception as exc:  # noqa: BLE001 — LLM 失败退骨架, 人闸手填
        logger.warning("[v3:arcs] LLM 创作失败 (退骨架, 立意待手填): %s", exc)

    issues = boundary_issues(arcs, lines)
    long_mods = [a for a in mods if len(a["lines"]) > SPLIT_SUGGEST_LINES]
    for m in long_mods:
        hints.append(f"L{m['no_start']}-L{m['no_end']} {m['mode']} 段 {len(m['lines'])} 行, "
                     "可在语义完整处再拆一弧 (需回 ① 重标语态或本页手拆)")

    v3["arcs"] = arcs
    v3["arc_hints"] = hints
    v3["arc_issues"] = issues
    gates = v3.setdefault("gates", {})
    gates["arcs"] = False          # 人闸: 需前端确认放行才开
    v3["arcs_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    shots_mod.save(doc)
    job["result"] = {"arcs": len(arcs), "issues": len(issues),
                     "with_metaphor": sum(1 for a in arcs if a["metaphor"])}
    logger.info("[v3:arcs] %s ep%d: %d 弧 (执法问题 %d) → shots.json v3 ✓",
                job["book_title"], job["ep"], len(arcs), len(issues))
