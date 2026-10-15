# -*- coding: utf-8 -*-
"""RLR 批量识别进度探针: 单行输出, 供监控循环调用。状态存 sandbox/_rlr_probe.state 算速率。"""
import glob, json, sqlite3, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

vids = []
for mf in sorted(glob.glob('data/materials/youtube/*/manifest.json')):
    try:
        m = json.loads(Path(mf).read_text(encoding='utf-8'))
    except Exception:
        continue
    if m.get('entity') == 'RealLifeLore':
        vids.append(m['video_id'])

tl = 0
for v in vids:
    p = Path(f'data/materials/youtube/{v}/timeline.json')
    if p.exists():
        try:
            if 'sec_flags' in json.loads(p.read_text(encoding='utf-8')):
                tl += 1
        except Exception:
            pass

s2 = s2done = reg = 0
try:
    con = sqlite3.connect('file:data/pipeline.db?mode=ro', uri=True, timeout=5)
    rows = con.execute(
        "select stage, count(*), coalesce(sum(stats_json like '%registered%'),0) "
        "from material_ingest_jobs where id like 'rlrg-%' group by stage").fetchall()
    d = {r[0]: r[1] for r in rows}
    s2 = sum(d.values())
    s2done = d.get('done', 0)
    for r in con.execute("select stats_json from material_ingest_jobs "
                         "where id like 'rlrg-%' and stats_json is not null"):
        try:
            reg += json.loads(r[0]).get('registered', 0)
        except Exception:
            pass
    con.close()
except Exception:
    s2 = -1

state_path = Path('sandbox/_rlr_probe.state')
now = time.time()
rate = ''
try:
    st = json.loads(state_path.read_text())
    dt = now - st['ts']
    done_now = tl + max(s2, 0)
    dc = done_now - st['cnt']
    if dt > 120:
        r = dc / (dt / 60)
        remain = (len(vids) - tl) + max(0, s2 - s2done)
        if r > 0.005:
            rate = f" | 速率{r:.2f}/min 剩余约{remain / r / 60:.1f}h"
        else:
            rate = f" | 速率≈0 剩{remain}"
except Exception:
    pass
state_path.write_text(json.dumps({'ts': now, 'cnt': tl + max(s2, 0)}))

print(f"阶段1: {tl}/{len(vids)} | 阶段2: {s2}部(done {s2done}) | 累计入库 {reg}{rate}")
