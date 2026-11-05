# -*- coding: utf-8 -*-
"""1105 批6 (T1): LLM token 台账汇总 — llm_ledger.jsonl 按 集/tag/日 聚合.

用法:
  python scripts/llm_usage.py                    # 全部书的 llm 台账
  python scripts/llm_usage.py 卓有               # 书名前缀过滤
  python scripts/llm_usage.py 卓有 --ep 5        # 单集
  python scripts/llm_usage.py --days 7           # 近 N 天
  python scripts/llm_usage.py --top 20           # tag 明细前 N (默认 15)

输出: 按 (书, 集) 的 calls/ptok/ctok 合计 + top-tag 分布 — token 优化的地面真值
(批7/8 动刀前后的 A/B 对照取数源)。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _rows(book: str, ep: int | None) -> list[dict]:
    from app.services.anim_pipeline.config import load
    base = Path(load().output_root)
    rows: list[dict] = []
    for p in base.glob(f"*{book}*/ep*/llm_ledger.jsonl"):
        if ep is not None and p.parent.name != f"ep{ep}":
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append({**json.loads(ln), "_ep_dir": p.parent.name,
                             "_book": p.parent.parent.name})
            except Exception:  # noqa: BLE001 — 损坏行跳过
                continue
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM token 台账汇总")
    ap.add_argument("book", nargs="?", default="", help="书名前缀 (空=全部)")
    ap.add_argument("--ep", type=int, help="集号过滤")
    ap.add_argument("--days", type=int, default=0, help="近 N 天 (0=不限)")
    ap.add_argument("--top", type=int, default=15, help="tag 明细前 N")
    a = ap.parse_args()

    rows = _rows(a.book, a.ep)
    if a.days:
        cutoff = (datetime.now() - timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M")
        rows = [r for r in rows if str(r.get("ts", "")) >= cutoff]
    if not rows:
        print("无台账记录 (llm_ledger.jsonl 不存在或为空 — 1105 批6 前的 job 无台账)")
        return 0

    by_ep: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    by_tag: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    by_day: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        c, p, ct = 1, int(r.get("ptok") or 0), int(r.get("ctok") or 0)
        k = (r["_book"], r["_ep_dir"])
        for acc, kk in ((by_ep, k), (by_tag, str(r.get("tag") or "?")),
                        (by_day, str(r.get("ts", ""))[:10])):
            acc[kk][0] += c
            acc[kk][1] += p
            acc[kk][2] += ct

    kfmt = lambda n: f"{n / 1000:.1f}k" if n >= 1000 else str(n)  # noqa: E731
    print(f"== LLM 台账 · {len(rows)} 次调用 · 共 {kfmt(sum(v[1] for v in by_ep.values()))}"
          f" prompt + {kfmt(sum(v[2] for v in by_ep.values()))} completion tokens ==")
    print("\n-- 按集 --")
    for (book, epd), v in sorted(by_ep.items(), key=lambda kv: -(kv[1][1] + kv[1][2])):
        print(f"  {book}/{epd:<6} {v[0]:>4} 次  prompt {kfmt(v[1]):>8}  completion {kfmt(v[2]):>8}  合计 {kfmt(v[1] + v[2]):>8}")
    print("\n-- 按调用点 tag (top %d) --" % a.top)
    for tag, v in sorted(by_tag.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:a.top]:
        print(f"  {tag:<28} {v[0]:>4} 次  prompt {kfmt(v[1]):>8}  completion {kfmt(v[2]):>8}")
    print("\n-- 按日 --")
    for day, v in sorted(by_day.items()):
        print(f"  {day}  {v[0]:>4} 次  合计 {kfmt(v[1] + v[2]):>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
