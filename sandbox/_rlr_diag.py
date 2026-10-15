# -*- coding: utf-8 -*-
import json
import re
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("file:data/pipeline.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row

rows = con.execute("select id, stage, created_at, updated_at from "
                   "material_ingest_jobs where id like 'rlrg-%' and "
                   "stage != 'done' order by created_at").fetchall()
print(f"在制 job ({len(rows)}):")
for r in rows:
    print(f"  {r['id']} stage={r['stage']} created={r['created_at']} "
          f"updated={r['updated_at']}")

for r in rows:
    vid = r["id"][5:]
    mf = f"data/materials/youtube/{vid}/manifest.json"
    try:
        m = json.loads(open(mf, encoding="utf-8").read())
        clips = m.get("clips", [])
        tagged = sum(1 for c in clips if c.get("tags"))
        print(f"  {vid}: clips={len(clips)} 已tag={tagged}")
    except Exception as e:
        print(f"  {vid}: manifest 读取失败 {e}")

# 输出文件尾部诊断
f = r"C:\Users\tanhaox\AppData\Local\Temp\claude\f--AI-Agent-Local-digital-human\802e8f07-aead-470a-991f-17277d5a4497\tasks\bp3b4izm5.output"
lines = open(f, encoding="utf-8", errors="replace").readlines()
fails = [ln.strip() for ln in lines if "FAIL" in ln]
print(f"\nFAIL 行总数: {len(fails)}")
for ln in fails[-8:]:
    print(f"  {ln[:110]}")

progs = [ln.strip() for ln in lines if re.search(r"\[\d+/\d+\]", ln)]
print(f"进度行总数: {len(progs)}")
for ln in progs[-6:]:
    print(f"  {ln[:110]}")

wd = [ln.strip() for ln in lines if "WORKER DONE" in ln]
print(f"WORKER DONE: {len(wd)}")
for ln in wd:
    print(f"  {ln[:110]}")
