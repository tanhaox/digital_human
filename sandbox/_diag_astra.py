# -*- coding: utf-8 -*-
import sqlite3, json, sys
sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect('data/pipeline.db')
db.row_factory = sqlite3.Row

jid = '33b2b922-b138-4e9a-9c14-2d3e16ba9572'
j = db.execute("SELECT id, status, created_at, script_id, plan_json FROM director_jobs WHERE id LIKE '33b2b922%'", ()).fetchone()
print('job status:', j['status'], 'created:', j['created_at'])

plan = json.loads(j['plan_json']) if j['plan_json'] else {}
print('\n-- trace --')
for t in plan.get('trace', []):
    print(f"  #{t['n']} [{t['ts']}] {t['step']}/{t['status']}: {t['detail'][:110]}")

print('\n-- slots --')
slots = db.execute("SELECT slot_index, workflow, status, error_code, error_message, updated_at, params_json FROM director_slots WHERE director_job_id=? ORDER BY slot_index", (j['id'],)).fetchall()
missing = 0
for s in slots:
    try:
        pj = json.loads(s['params_json']) if s['params_json'] else {}
    except Exception:
        pj = {}
    if s['workflow'] == 'broll_pexels':
        kws = pj.get('keywords')
        if not kws:
            missing += 1
            flag = 'MISSING'
        else:
            flag = str(kws[:3])
    else:
        flag = pj.get('fallback_reason', '')[:40] or '-'
    err = (s['error_message'] or '')[:60]
    print(f"[{s['slot_index']:02d}] {s['workflow']:<13} {s['status']:<9} {s['updated_at']} {flag} {err}")
print(f"\npexels 缺词槽: {missing}")
