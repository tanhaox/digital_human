# -*- coding: utf-8 -*-
"""高市早苗 job 重跑后实态: 状态/时间/素材来源/实体."""
import json
import sqlite3
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("data/pipeline.db")
rows = con.execute(
    "select slot_index, status, updated_at, params_json from director_slots "
    "where director_job_id like '5f4a5c7d%' order by slot_index").fetchall()
src = Counter()
ents_total = 0
for idx, st, ts, pj in rows:
    p = json.loads(pj) if pj else {}
    if p.get("entities"):
        ents_total += 1
    lf = p.get("local_file") or ""
    if not lf:
        src[st] += 1
    elif "youtube" in lf:
        src["youtube"] += 1
        if idx < 6:
            print(f"  slot {idx}: {lf[-60:]}")
    else:
        src["local"] += 1
print("状态分布:", dict(src), "| 挂实体 slot:", ents_total)
r = con.execute("select status, updated_at from director_jobs where id like '5f4a5c7d%'").fetchone()
print("job:", r[0], "| updated:", str(r[1])[:19])
