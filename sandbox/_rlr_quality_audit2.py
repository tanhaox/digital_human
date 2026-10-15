# -*- coding: utf-8 -*-
import json
import random
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
random.seed(7)
con = sqlite3.connect("file:data/pipeline.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("select * from video_assets where file_path like '%youtube%' "
                   "and file_path like '%clips%'").fetchall()
n = len(rows)
dz = sum(1 for r in rows if r["description_zh"])
de = sum(1 for r in rows if r["description_en"])
sc = sum(1 for r in rows if r["scenes"])
loc = sum(1 for r in rows if r["location"])
st = sum(1 for r in rows if r["shot_types"])
pp = sum(1 for r in rows if r["people"])
print(f"入库 {n} | desc_zh {dz} | desc_en {de} | scenes {sc} | "
      f"location {loc} | shot_types {st} | people {pp}")
ns = sum(1 for r in rows if r["tags"] and "no_subtitle" in r["tags"])
print(f"带 no_subtitle 标记: {ns}/{n}")
print()
for r in random.sample(rows, 12):
    print(f"· [{r['duration_sec']:.0f}s] {r['description_zh']}")
    print(f"    场景:{r['scenes']} 地点:{r['location']} 镜头:{r['shot_types']}")
    print(f"    关键词:{r['tags']}")
