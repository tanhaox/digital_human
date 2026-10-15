# -*- coding: utf-8 -*-
"""RLR 阶段2 并行版 (2026-08-31): 4 worker 填满 llama --parallel 4 槽。
幂等: 已有 rlrg-<vid> done 行跳过; tags 已有的 clips 跳过; 杀进程安全。
替代串行驱动的阶段2 (其阶段1已完成)。"""
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

# VPN 守卫 (与原驱动一致)
from app.services.gpu_service_manager._manager import vpn_running  # noqa: E402

pid = vpn_running()
print("VPN:", pid if pid else "已退出 ✓", flush=True)
if pid:
    raise SystemExit("请先彻底退出变色龙再跑")

ROOT = Path(".").resolve()
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import MaterialIngestJob  # noqa: E402

init_db("sqlite:///data/pipeline.db")

# 已完成的 job 跳过; 半途非 done 行清掉 (否则主键冲突)
with get_session_maker()() as db:
    done_vids = {r[0] for r in db.query(MaterialIngestJob.id).filter(
        MaterialIngestJob.id.like("rlrg-%"),
        MaterialIngestJob.stage == "done")}
    stale = db.query(MaterialIngestJob).filter(
        MaterialIngestJob.id.like("rlrg-%"),
        MaterialIngestJob.stage != "done").delete(synchronize_session=False)
    db.commit()
done_vids = {v[5:] for v in done_vids}
if stale:
    print(f"清理半途 job 行 {stale} 条", flush=True)

todo = []
for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    if m.get("entity") != "RealLifeLore":
        continue
    vid = m["video_id"]
    if vid in done_vids:
        continue
    clips = m.get("clips", [])
    if clips and any(not c.get("tags") and c.get("ocr_clean") is not False
                     for c in clips):
        todo.append(vid)

# --vids <file>: 只跑清单内的部 (样本批模式, 2026-09-01 用户令"跑50条看结果")
if "--vids" in sys.argv:
    allow = set(Path(sys.argv[sys.argv.index("--vids") + 1]).read_text().split())
    todo = [v for v in todo if v in allow]

print(f"阶段2 并行×4: {len(todo)} 部待打标入库 (已完成跳过 {len(done_vids)})", flush=True)

WORKER = r'''
import sys, time, json
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.database import init_db, get_session_maker
from app.models import MaterialIngestJob
from app.services import material_ingest_service as svc
init_db("sqlite:///data/pipeline.db")
vids = {vids!r}
t0 = time.time(); tot = 0
for i, vid in enumerate(vids, 1):
    try:
        with get_session_maker()() as db:
            job = MaterialIngestJob(id=f"rlrg-{{vid}}", mode="url",
                                    entity="RealLifeLore", video_id=vid,
                                    stage="tagging")
            db.add(job); db.commit()
            svc._tag(job)
            job.stage = "registering"; db.commit()
            reg = svc._register(db, job)
            bf = svc._backfill_dims(db, job)
            job.stage = "done"
            job.stats_json = {{**reg, "backfilled": bf}}
            db.commit()
            tot += reg.get("registered", 0)
            print(f"  [{{i}}/{{len(vids)}}] {{vid}} 入库{{reg.get('registered',0)}} "
                  f"({{time.time()-t0:.0f}}s)", flush=True)
    except Exception as exc:
        print(f"  {{vid}} FAIL: {{str(exc)[:90]}}", flush=True)
print(f"WORKER DONE vids={{len(vids)}} 入库{{tot}} {{(time.time()-t0)/60:.0f}}min", flush=True)
'''

N = 4
procs = []
for i in range(N):
    shard = todo[i::N]
    if not shard:
        continue
    code = WORKER.format(vids=shard)
    p = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=str(ROOT))
    procs.append((i, len(shard), p))
    print(f"  片{i}: {len(shard)} 部 (PID {p.pid})", flush=True)

for i, n, p in procs:
    p.wait()
    print(f"  片{i} 完成 exit={p.returncode}", flush=True)
print("阶段2 并行批全部完成", flush=True)
