# -*- coding: utf-8 -*-
"""高市早苗 slot 明细: 哪些 slot 挂她、workflow、结果."""
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("data/pipeline.db")
rows = con.execute(
    "select slot_index, visual_type, workflow, status, params_json "
    "from director_slots where director_job_id like '5f4a5c7d%' order by slot_index").fetchall()
for idx, vt, wf, st, pj in rows:
    p = json.loads(pj) if pj else {}
    ents = p.get("entities") or []
    lf = (p.get("local_file") or "")[-38:]
    mark = "★" if "高市早苗" in ents else " "
    if ents or lf:
        print(f"{mark} slot {idx:2d} [{wf:14s}] {st:9s} ent={ents} | {lf}")
