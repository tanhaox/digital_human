# -*- coding: utf-8 -*-
"""huazi_arc_pass — 段落级花字提炼+分配 (1016 用户令, 治逐镜残片).

用法:
  python scripts/huazi_arc_pass.py <book_id> <ep> [--apply]
  不带 --apply 只出提案报告; 带 --apply 落库并打印需重掷视频的镜。

两段式:
  A 段 (每弧一次 LLM): 从弧完整口播提炼 1-3 个主旨关键词 (≤4字完整词 +
     口播原词锚 spoken), 点睛不是常态。
  B 段 (确定性代码): spoken 锚落镜 (跨镜断句落在包含原词的镜) + 同弧去重 +
     相邻镜不连发 (自动让位到次优镜) + 全集密度帽 (~1/4.5 镜)。

军规来源: ID-098 (≤4字主次律), ID-101 (跨镜上下文), ID-102 (弧级密度)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import set_config, load_config  # noqa: E402
set_config(load_config())

from app.services.anim_pipeline import shots as shots_mod  # noqa: E402
from app.services.anim_pipeline.config import load as anim_load  # noqa: E402
from app.services.anim_pipeline.llm import chat_json  # noqa: E402

SYSTEM = """你是短视频花字设计师。给你一个语义完整段落(弧)的完整口播, 提炼点睛
关键词。只输出 JSON。"""

TPL = """# 段落完整口播
{passage}

# 任务
这段话的核心论点/冲突/金句是什么? 提炼 0-3 个点睛花字。

# 硬律
1. text ≤4 个汉字, 必须是完整语义词 (禁止截断残片, "挑三"是"挑三个"的残片)。
2. spoken = 口播里真实说出的原词片段 (≤8字), 用于定位落镜; 若 text 本身就是
   原词, spoken = text。
3. 点睛不是常态: 只有主旨/金句/冲突词配花字, 叙事细节不配。
4. 与字幕分工: 字幕打全程, 花字只点睛。

# 输出 JSON
{{"keywords": [{{"text": "≤4字", "spoken": "口播原词", "why": "≤12字"}}, ...]}}"""


def _best_shot(kw: dict, arc_shots: list[dict]) -> str | None:
    """spoken 锚落镜: 完整包含 > 最长重叠。跨镜断句落在包含原词的镜."""
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


def run(book_ref: str, ep: int, apply: bool) -> None:
    doc = shots_mod.load_doc(*resolve(book_ref, ep))
    llm_cfg = anim_load().llm

    arcs: dict[str, list[dict]] = {}
    for s in doc["shots"]:
        arcs.setdefault(str(s.get("arc_id") or s["shot_id"]), []).append(s)

    result: dict[str, str] = {}   # sid -> text
    dropped: list[str] = []
    for arc, ss in arcs.items():
        passage = "".join((s.get("narration") or "") for s in ss)
        try:
            out = chat_json(llm_cfg, SYSTEM, TPL.format(passage=passage))
        except Exception as exc:  # noqa: BLE001
            print(f"[arc {arc}] LLM 失败: {str(exc)[:80]}")
            continue
        taken: set[int] = set()    # 本弧已占镜的下标 (相邻让位用)
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
            if txt in result.values():                 # 同弧/全集同词去重
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

    cur = {s["shot_id"]: (s.get("h3_text_event") or {}).get("text") or ""
           for s in doc["shots"] if (s.get("h3_text_event") or {}).get("text")}
    print("=== 弧级花字终案 ===")
    print(" ".join(f"{k}「{v}」" for k, v in result.items()))
    if dropped:
        print("让位/未命中:", "; ".join(dropped))
    changed = sorted({k for k in set(result) | set(cur)
                      if result.get(k) != cur.get(k)})
    print("变更镜 (需重掷视频):", " ".join(changed) or "(无)")

    if apply:
        for s in doc["shots"]:
            if s["shot_id"] in result:
                dur = float((s.get("anim") or {}).get("duration_s") or 3)
                s["h3_text_event"] = {"text": result[s["shot_id"]],
                                      "at_s": round(min(1.5, dur / 3), 1),
                                      "zone": "头顶留白"}
            else:
                s["h3_text_event"] = {}
        shots_mod.save(doc)
        print("已落库 shots.json")


def resolve(book_ref: str, ep: int) -> tuple[str, int]:
    """任意引用 (id/书名/书名片段) → (book_title, ep) — shots.json 按书名落目录."""
    import sqlite3
    con = sqlite3.connect("data/pipeline.db")
    col = ("id" if len(book_ref) >= 30 else "book_title")
    r = con.execute(
        f"select book_title from book_projects where {col} like ?",
        (f"%{book_ref}%",)).fetchone()
    if not r:
        raise SystemExit(f"书未命中: {book_ref}")
    return r[0], ep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("ep", type=int)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    run(a.book, a.ep, a.apply)
