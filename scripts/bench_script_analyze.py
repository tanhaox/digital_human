# -*- coding: utf-8 -*-
"""逐句文案量化分析器 (bench-analyze skill 第5步固化, 0911).

用法: python scripts/bench_script_analyze.py .tmp/transcript_<tag>.txt [tag2] [tag3] ...
多文件时自动对比输出。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROLEPLAY_RE = re.compile(r'你想|你发现|你决定|聪明的你|假如你|你开始|身为|你突发奇想|你恍然大悟|你不信|你巧妙|你仔细')


def analyze(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    lines = re.findall(r'\[([\d.]+)s\] (.+)', raw)
    texts = [(float(t), s.strip()) for t, s in lines]
    if not texts:
        return {"file": path, "err": "no segments"}

    full = "".join(t for _, t in texts)
    dur = texts[-1][0]
    n = len(texts)
    you = full.count("你")
    we = full.count("我们") + full.count("咱")
    roleplay = len(_ROLEPLAY_RE.findall(full))
    hook = " ".join(t for _, t in texts[:3])[:60]
    return {
        "file": Path(path).stem,
        "chars": len(full), "dur_s": round(dur), "cps": round(len(full) / dur, 1),
        "segments": n, "avg_sent": round(len(full) / n),
        "you_per_k": round(you / len(full) * 1000), "we_per_k": round(we / len(full) * 1000),
        "roleplay_signals": roleplay,
        "hook": hook,
        "narrative": "第二人称沉浸式" if roleplay >= 5 and you / len(full) * 1000 >= 10 else "第三人称讲课式" if we >= you else "混合",
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    results = [analyze(p) for p in sys.argv[1:]]
    if len(results) == 1:
        r = results[0]
        for k, v in r.items():
            print(f"  {k}: {v}")
    else:
        keys = ["chars", "dur_s", "cps", "avg_sent", "you_per_k", "we_per_k", "roleplay_signals", "narrative"]
        print(f"{'指标':12s}", "".join(f"{r['file'][:12]:>14s}" for r in results))
        for k in keys:
            print(f"{k:12s}", "".join(f"{str(r.get(k, '')):>14s}" for r in results))
        print(f"{'钩子':12s}")
        for r in results:
            print(f"  {r['file'][:12]}: {r['hook']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
