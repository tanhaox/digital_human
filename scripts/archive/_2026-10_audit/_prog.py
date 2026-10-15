# -*- coding: utf-8 -*-
"""进度查询 (RLR 识别批)."""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
done = 0
for tl in glob.glob("data/materials/youtube/*/timeline.json"):
    d = json.loads(Path(tl).read_text(encoding="utf-8"))
    if d.get("clean_windows") is not None:
        done += 1
print(f"进度: {done}/275")
