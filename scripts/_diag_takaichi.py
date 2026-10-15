# -*- coding: utf-8 -*-
"""诊断: 高市早苗稿素材缺失链路."""
import glob
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

con = sqlite3.connect("data/pipeline.db")
n = con.execute("select count(*) from video_assets where tags like '%高市早苗%'").fetchone()[0]
print("库里高市早苗素材:", n, "条")
for mf in glob.glob("data/materials/youtube/*/manifest.json"):
    m = json.loads(open(mf, encoding="utf-8").read())
    if m.get("entity") == "高市早苗":
        print("staging manifest:", mf.split("\\")[-2], "| clips:", len(m.get("clips", [])))
rows = con.execute(
    "select dj.id, dj.status, dj.plan_json from director_jobs dj "
    "join scripts s on s.id = dj.script_id "
    "join articles a on a.id = s.article_id "
    "where a.title like '%高市%' order by dj.created_at desc limit 1").fetchall()
if rows:
    jid, st, pj = rows[0]
    p = json.loads(pj)
    ents = [e["name"] for e in (p.get("material_entities") or [])]
    print(f"导演job {jid[:8]} ({st}): 实体={ents}")
else:
    print("这篇稿还没有导演 job (只生成了音频)")
