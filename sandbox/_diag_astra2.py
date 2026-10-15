# -*- coding: utf-8 -*-
import sqlite3, json, sys
sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect('data/pipeline.db')
db.row_factory = sqlite3.Row

# job -> script -> article / package
j = db.execute("SELECT script_id FROM director_jobs WHERE id LIKE '33b2b922%'").fetchone()
sc = db.execute("SELECT id, article_id, material_package_id FROM scripts WHERE id=?", (j['script_id'],)).fetchone()
print('script:', sc['id'][:8], 'pkg:', (sc['material_package_id'] or 'None')[:8] if sc['material_package_id'] else None)
art = db.execute("SELECT id, title, images_json FROM articles WHERE id=?", (sc['article_id'],)).fetchone()
imgs = json.loads(art['images_json']) if art['images_json'] else []
print('article images_json:', len(imgs))

if sc['material_package_id']:
    items = db.execute("SELECT id, media, source_type, images_json FROM material_items WHERE package_id=?", (sc['material_package_id'],)).fetchall()
    pool = 0
    for it in items:
        ij = json.loads(it['images_json']) if it['images_json'] else []
        charts = [e for e in ij if (e.get('vlm') or {}).get('is_chart')]
        pool += len(charts)
    print(f'package items={len(items)}, chart-pool size={pool}')

# 管线字段
j2 = db.execute("SELECT pipelines, status FROM director_jobs WHERE id LIKE '33b2b922%'").fetchone()
print('job.pipelines:', repr(j2['pipelines']), 'status:', j2['status'])
