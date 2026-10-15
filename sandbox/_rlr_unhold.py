# -*- coding: utf-8 -*-
"""恢复队列: .hold 源改回 .mp4, 报告数量。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
STAGE = Path("data/materials/youtube")
n = 0
fail = []
for h in STAGE.glob("yt_*.mp4.hold"):
    dst = h.with_suffix("")  # 去掉 .hold
    try:
        h.rename(dst)
        n += 1
    except Exception as e:
        fail.append((h.name, str(e)[:50]))
print(f"恢复 {n} 部; 失败 {len(fail)}")
for f in fail:
    print(" ", f)
