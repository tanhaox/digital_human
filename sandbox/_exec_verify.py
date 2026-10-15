# -*- coding: utf-8 -*-
import sqlite3, json, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

db = sqlite3.connect('data/pipeline.db')
db.row_factory = sqlite3.Row
j = db.execute("SELECT id FROM director_jobs WHERE id LIKE '33b2b922%'").fetchone()
jid = j['id']
print('job:', jid)

req = urllib.request.Request(f"http://127.0.0.1:54321/api/director/jobs/{jid}/execute", method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    body = json.loads(r.read())
print('execute resp status:', r.status, '-> job status:', body.get('status'))

for round_ in range(6):
    time.sleep(15)
    slots = db.execute("SELECT slot_index, workflow, status, error_message FROM director_slots WHERE director_job_id=? AND status != 'queued' ORDER BY slot_index", (jid,)).fetchall()
    job = db.execute("SELECT status FROM director_jobs WHERE id=?", (jid,)).fetchone()
    running = [f"[{s['slot_index']:02d}]{s['workflow']}/{s['status']}" for s in slots]
    print(f"t+{(round_+1)*15:>3}s job={job['status']} done={len(slots)}/32: {running[-6:] if running else '(nothing yet)'}")
    if job['status'] != 'executing' and round_ >= 1:
        print('!! execute exited early')
        break
