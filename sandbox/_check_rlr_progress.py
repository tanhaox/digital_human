# -*- coding: utf-8 -*-
import glob, json, sqlite3, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

vids = []
for mf in sorted(glob.glob('data/materials/youtube/*/manifest.json')):
    m = json.loads(Path(mf).read_text(encoding='utf-8'))
    if m.get('entity') == 'RealLifeLore':
        vids.append(m['video_id'])

tl = 0; clipped = 0; clip_total = 0; tagged = 0; recent = []
for v in vids:
    p = Path(f'data/materials/youtube/{v}/timeline.json')
    if p.exists():
        tl += 1
        recent.append((p.stat().st_mtime, v))
    m = json.loads(Path(f'data/materials/youtube/{v}/manifest.json').read_text(encoding='utf-8'))
    cs = m.get('clips', [])
    if cs:
        clipped += 1
        clip_total += len(cs)
        tagged += sum(1 for c in cs if c.get('tags'))

import time
recent.sort(reverse=True)
print(f"RLR 总数: {len(vids)}")
print(f"阶段1 CPU 完成 (timeline.json): {tl}/{len(vids)}  剩 {len(vids)-tl}")
print(f"已切片: {clipped} 部 / {clip_total} 片, 其中已带 tags: {tagged}")
if recent:
    for mt, v in recent[:5]:
        print(f"  最新完成: {time.strftime('%H:%M:%S', time.localtime(mt))} {v}")
con = sqlite3.connect('data/pipeline.db')
try:
    rows = con.execute("select stage, count(*) from material_ingest_jobs where id like 'rlrg-%' group by stage").fetchall()
    print('DB rlrg 任务:', rows if rows else '无 (阶段2 LLM 打标未启动)')
    try:
        n = con.execute("select count(*) from materials where entity='RealLifeLore'").fetchone()[0]
        print('DB RLR 素材已入库:', n)
    except Exception as e:
        print('materials 表查询:', e)
except Exception as e:
    print('DB:', e)
con.close()
