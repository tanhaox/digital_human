# -*- coding: utf-8 -*-
"""RLR 下载进度."""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
n = clips = 0
for mf in glob.glob("data/materials/youtube/*/manifest.json"):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    if m.get("entity") == "RealLifeLore":
        n += 1
        clips += len(m.get("clips", []))
print(f"RealLifeLore: {n}/15 部切片完成, 共 {clips} 片段")
