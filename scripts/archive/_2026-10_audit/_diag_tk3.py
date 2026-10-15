# -*- coding: utf-8 -*-
"""手动复靶: 高市早苗实体 query vs 库内资产指纹."""
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.database import init_db, get_session_maker  # noqa: E402
from app.services.asset_matcher import match_entity_bullseye  # noqa: E402

con = sqlite3.connect("data/pipeline.db")
pj = json.loads(con.execute(
    "select plan_json from director_jobs where id like '5f4a5c7d%'").fetchone()[0])
for e in pj.get("material_entities") or []:
    if "高市" in e["name"] or "自民" in e["name"]:
        print("实体:", e["name"], "| queries:", e["queries"])

init_db("sqlite:///data/pipeline.db")
with get_session_maker()() as db:
    for qs in (["takaichi sanae press conference"], ["takaichi"], ["sanae takaichi"]):
        hits = match_entity_bullseye(db, qs, limit=3)
        print(f"靶心 {qs}: {len(hits)} 命中", [h["asset_no"] for h in hits[:3]])
