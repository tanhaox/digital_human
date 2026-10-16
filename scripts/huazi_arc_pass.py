# -*- coding: utf-8 -*-
"""huazi_arc_pass — 段落级花字 CLI 薄壳 (核心逻辑在 anim_pipeline.huazi_pass).

1017 W3: 核心 (弧级 LLM 提炼 + 确定性落镜 + 密度帽 + text_layer 去重) 已入
管线, 规划收尾 _finalize_doc 自动执行; 本 CLI 供已产集手动补跑:

  python scripts/huazi_arc_pass.py <book_id|书名片段> <ep> [--apply]

不带 --apply 只出提案报告; 带 --apply 落库 (h3_text_event 重写 + 同词
text_layer 去重) 并打印需重掷视频的镜。"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import set_config, load_config  # noqa: E402
set_config(load_config())

from app.services.anim_pipeline import shots as shots_mod  # noqa: E402
from app.services.anim_pipeline.huazi_pass import enrich_text_route  # noqa: E402


def _resolve(book_ref: str, ep: int) -> tuple[str, int]:
    import sqlite3
    con = sqlite3.connect("data/pipeline.db")
    col = "id" if len(book_ref) >= 30 else "book_title"
    r = con.execute(f"select book_title from book_projects where {col} like ?",
                    (f"%{book_ref}%",)).fetchone()
    if not r:
        raise SystemExit(f"书未命中: {book_ref}")
    return r[0], ep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("ep", type=int)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    book_title, ep = _resolve(a.book, a.ep)
    doc = shots_mod.load_doc(book_title, ep)
    cur = {s["shot_id"]: (s.get("h3_text_event") or {}).get("text") or ""
           for s in doc["shots"] if (s.get("h3_text_event") or {}).get("text")}
    result = enrich_text_route(doc)
    print("=== 弧级花字终案 ===")
    print(" ".join(f"{k}「{v}」" for k, v in result.items()))
    changed = sorted({k for k in set(result) | set(cur)
                      if result.get(k) != cur.get(k)})
    print("变更镜 (需重掷视频):", " ".join(changed) or "(无)")
    if a.apply:
        shots_mod.save(doc)
        print("已落库 shots.json")


if __name__ == "__main__":
    main()
