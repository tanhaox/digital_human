# -*- coding: utf-8 -*-
"""现 job 直接跑人物提升 + 重置 + 触发执行."""
import json
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import DirectorJob  # noqa: E402
from app.services.entity_extractor import promote_person_slots  # noqa: E402

init_db("sqlite:///data/pipeline.db")
with get_session_maker()() as db:
    job = db.query(DirectorJob).filter(DirectorJob.id.like("5f4a5c7d%")).first()
    ents = (job.plan_json or {}).get("material_entities") or []
    n = promote_person_slots(db, job, ents)
    print(f"人物提升: {n} 个 hf→broll")
    con = db.connection().connection if hasattr(db, "connection") else None
    db.commit()

con = sqlite3.connect("data/pipeline.db")
n2 = con.execute(
    "update director_slots set status='queued', output_path=NULL, "
    "error_code=NULL, error_message=NULL, "
    "params_json = json_remove(params_json, '$.local_file') "
    "where director_job_id like '5f4a5c7d%' and status != 'replaced'").rowcount
con.execute("update director_jobs set status='reviewing' where id like '5f4a5c7d%'")
con.commit()
print(f"重置 {n2} slot")

jid = con.execute("select id from director_jobs where id like '5f4a5c7d%'").fetchone()[0]
req = urllib.request.Request(
    f"http://127.0.0.1:54321/api/director/jobs/{jid}/execute",
    data=json.dumps({"pipelines": "p,h"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=60) as r:
    print("执行触发:", r.status)
