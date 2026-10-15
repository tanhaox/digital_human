# -*- coding: utf-8 -*-
"""重roll 账本统计 (0918 用户令: 文字扭曲先统计后治理).

用法: python scripts/reroll_stats.py [账本路径]
输出: 重roll 总账 / 按镜排行 / 文字字段聚合 (整串 top + 单字频 vs 全集基线的
过表征榜 — 几集累积后, 哪些字/词高频出现于重roll 镜自然浮出)。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

LEDGER = Path(r"f:\AI-Agent-Local\digital_human\outputs\动画\_资产\reroll_ledger.jsonl")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else LEDGER
    if not path.exists():
        print(f"账本不存在 (还没有重roll 记录): {path}")
        return
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("账本为空")
        return
    print(f"=== 重roll 总账: {len(rows)} 次 ===")
    by_reason = Counter(r.get("reason", "?")[:18] for r in rows)
    for k, v in by_reason.most_common(8):
        print(f"  {v:3d}× {k}")
    print("\n=== 按镜排行 (top10) ===")
    by_shot = Counter((r.get("book", "?")[:12], r.get("ep"), r.get("shot_id")) for r in rows)
    for (bk, ep, sid), v in by_shot.most_common(10):
        print(f"  {v:2d}× {bk} ep{ep} {sid}")
    print("\n=== 重roll 镜的文字串 (top15) ===")
    texts = Counter()
    for r in rows:
        for t in r.get("texts") or []:
            if t.strip():
                texts[t.strip()] += 1
    for t, v in texts.most_common(15):
        print(f"  {v:2d}× 「{t}」")
    print("\n=== 单字过表征榜 (重roll字频 ÷ 全账字频, ≥2倍且≥3次) ===")
    roll_chars = Counter("".join(r.get("texts") or []) for r in rows)
    all_chars = Counter("".join(t for t in roll_chars.elements()))
    hits = [(c, n, round(n / max(all_chars[c], 1), 1))
            for c, n in roll_chars.most_common(200)
            if n >= 3 and n >= 2 * (all_chars[c] / max(len(rows), 1))]
    if hits:
        for c, n, ratio in hits[:20]:
            print(f"  {c}: 重roll镜 {n}次 (密度约 {ratio}×)")
    else:
        print("  (样本尚少, 规律未显 — 继续累积)")
    print(f"\n账本: {path} — 每次 anim_fail 重试/人检打回自动追加")


if __name__ == "__main__":
    main()
