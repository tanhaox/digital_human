# -*- coding: utf-8 -*-
"""僵尸 timeline 处理: 今天驱动启动(15:23)之前写入且无 sec_flags 的 timeline = 首灌遗留。
其 manifest 旧 clips 打 ocr_clean=False 隔离(防阶段2误打标, 可逆); timeline 留待 worker 全退后统一清理。"""
import json, sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
STAGE = Path("data/materials/youtube")
CUTOFF = datetime(2026, 8, 31, 15, 20).timestamp()

stale = []
for mf in sorted(STAGE.glob("*/manifest.json")):
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        continue
    if m.get("entity") != "RealLifeLore":
        continue
    tl = STAGE / m["video_id"] / "timeline.json"
    if not tl.exists():
        continue
    try:
        t = json.loads(tl.read_text(encoding="utf-8"))
    except Exception:
        continue
    if "sec_flags" in t:
        continue  # v2 已完成
    if tl.stat().st_mtime < CUTOFF:
        stale.append(m["video_id"])
        clips = m.get("clips", [])
        for c in clips:
            c["ocr_clean"] = False  # 隔离: 阶段2扫描跳过 (可逆)
        mf.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"僵尸隔离: {len(stale)} 部 -> {stale}")
print("其 timeline 将在 worker 全退后与 total_sec==0 垃圾一起删除, 恢复时重跑")
