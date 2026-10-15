# -*- coding: utf-8 -*-
"""阶段预览: 从 checkpoint 聚合当前疑似。只读。"""
import json
from collections import Counter
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "scripts" / ".relisten_results.jsonl"

recs = {}
for ln in STATE.read_text(encoding="utf-8").splitlines():
    try:
        r = json.loads(ln)
        recs[r["wav"]] = r
    except Exception:
        pass

checked = sum(1 for r in recs.values() if "diffs" in r)
susp = [r for r in recs.values() if r.get("diffs")]
cc: Counter = Counter()
for s in susp:
    for d in s["diffs"]:
        cc[(d["char"], d["expect"], d["heard"] or "(吞)")] += 1

print(f"已检 {checked} 段 | 疑似 {len(susp)} 段 ({len(susp) / max(checked, 1) * 100:.1f}%)")
print("\n== 字级聚合 ≥2 次 ==")
for (ch, e, h), n in sorted(cc.items(), key=lambda x: -x[1]):
    if n >= 2:
        print(f"  {ch}  期望{e} 听到{h}  ×{n}")
