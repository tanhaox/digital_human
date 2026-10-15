# -*- coding: utf-8 -*-
"""OCR 成果审查: 逐帧时间轴有没有 (设计要求: 每秒1帧 → 第N秒有/无字幕可查)."""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

done = pending = 0
for mf in glob.glob("data/materials/youtube/*/manifest.json"):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    for c in m.get("clips", []):
        if "ocr_clean" in c:
            done += 1
        else:
            pending += 1
print(f"覆盖率: 已筛 {done} / 待筛 {pending}")

for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    c0 = next((c for c in m["clips"] if "ocr_clean" in c), None)
    if c0:
        vid = m["video_id"]
        print(f"\n样例 {vid} clip 字段: {list(c0.keys())}")
        print("  clip 级:", {k: c0[k] for k in ("n", "start", "end", "ocr_clean")})
        tl = Path(mf).parent / "timeline.json"
        print("  timeline.json 存在:", tl.exists())
        if tl.exists():
            t = json.loads(tl.read_text(encoding="utf-8"))
            print("  timeline 字段:", list(t.keys()))
        break
