# -*- coding: utf-8 -*-
import sqlite3, sys, time
sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect('data/pipeline.db')
db.row_factory = sqlite3.Row
for i in range(10):
    j = db.execute("SELECT status FROM director_jobs WHERE id LIKE '33b2b922%'").fetchone()
    slots = db.execute("SELECT status FROM director_slots WHERE director_job_id LIKE (SELECT id FROM director_jobs WHERE id LIKE '33b2b922%')").fetchall()
    from collections import Counter
    c = dict(Counter(s['status'] for s in slots))
    print(f"job={j['status']} {c}")
    if j['status'] != 'executing':
        break
    time.sleep(20)
# 终态错误明细
bad = db.execute("SELECT slot_index, workflow, status, error_message FROM director_slots WHERE director_job_id LIKE (SELECT id FROM director_jobs WHERE id LIKE '33b2b922%') AND status='failed'").fetchall()
for s in bad:
    print(f"FAILED [{s['slot_index']:02d}] {s['workflow']}: {(s['error_message'] or '')[:120]}")
