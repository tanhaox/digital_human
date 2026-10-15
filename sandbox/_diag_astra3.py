# -*- coding: utf-8 -*-
import sqlite3, json, sys
sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect('data/pipeline.db')
db.row_factory = sqlite3.Row

j = db.execute("SELECT id, plan_json FROM director_jobs WHERE id LIKE '33b2b922%'").fetchone()
plan = json.loads(j['plan_json'])
wf = [s['workflow'] for s in plan.get('slots', [])]
from collections import Counter
print('plan_json workflows:', dict(Counter(wf)))

slots = db.execute("SELECT slot_index, workflow, params_json FROM director_slots WHERE director_job_id=?", (j['id'],)).fetchall()
demoted = []
for s in slots:
    pj = json.loads(s['params_json']) if s['params_json'] else {}
    if 'fallback_reason' in pj:
        demoted.append((s['slot_index'], s['workflow'], pj['fallback_reason'][:50]))
print('slots with fallback_reason:', demoted if demoted else 'NONE')
