# -*- coding: utf-8 -*-
"""高市早苗 job 5f4a5c7d: 重置 slot + 去重 + 触发重执行."""
import json
import sqlite3
import subprocess
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("data/pipeline.db")
n = con.execute(
    "update director_slots set status='queued', output_path=NULL, "
    "error_code=NULL, error_message=NULL, "
    "params_json = json_remove(params_json, '$.local_file') "
    "where director_job_id like '5f4a5c7d%'").rowcount
rows = con.execute(
    "select id, slot_index from director_slots "
    "where director_job_id like '5f4a5c7d%' and status in ('queued','completed') "
    "order by slot_index, created_at").fetchall()
seen = set()
for rid, idx in rows:
    if idx in seen:
        con.execute("update director_slots set status='replaced' where id=?", (rid,))
    seen.add(idx)
con.execute("update director_jobs set status='reviewing' where id like '5f4a5c7d%'")
con.commit()
print(f"重置 {n} slot, 去重后 {len(seen)} 个")

jid = con.execute("select id from director_jobs where id like '5f4a5c7d%'").fetchone()[0]
req = urllib.request.Request(
    f"http://127.0.0.1:54321/api/director/jobs/{jid}/execute",
    data=json.dumps({"pipelines": "p,h"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=60) as r:
    print("执行触发:", r.status)
