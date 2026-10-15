# -*- coding: utf-8 -*-
"""清剿 Courtesy 水印源 (IISS/Shangri-La 等): 查源视频→复核→回收库内条目."""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]

# 1. 找水印源视频 (manifest 标题含 courtesy 迹象关键词)
MARKS = ["iiss", "shangri", "courtesy", "credit:"]
hits = {}
for mf in glob.glob(str(ROOT / "data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    title = (m.get("title") or "").lower()
    if any(k in title for k in MARKS):
        hits[m["video_id"]] = (m.get("entity"), m.get("title"), len(m.get("clips", [])))
print("疑似水印源视频:")
for vid, (ent, title, n) in hits.items():
    print(f"  {vid} [{ent}] {title[:60]} | {n} 片")

# 2. 库内对应资产数
import sqlite3
con = sqlite3.connect("data/pipeline.db")
total = 0
for vid in hits:
    n = con.execute(
        "select count(*) from video_assets where file_path like ?",
        (f"%\\{vid}\\%",)).fetchone()[0]
    total += n
    print(f"  库内 {vid}: {n} 条")
print(f"合计待清: {total} 条")
