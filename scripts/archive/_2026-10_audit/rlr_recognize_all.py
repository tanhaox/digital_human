# -*- coding: utf-8 -*-
"""RLR 277 部批量识别: 逐部 probe → OCR 时间轴 → 净窗切片 → LLM → 入库.
前置: VPN 必须退出 (llama GPU 互斥). 幂等 — 已入库的 manifest 跳过."""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.services.gpu_service_manager._manager import vpn_running  # noqa: E402

pid = vpn_running()
print("VPN:", pid if pid else "已退出 ✓", flush=True)
if pid:
    raise SystemExit("请先彻底退出变色龙 (托盘退出) 再跑识别批")

import glob

from app.database import init_db, get_session_maker  # noqa: E402
from app.models import MaterialIngestJob, VideoAsset  # noqa: E402
from app.services import material_ingest_service as svc  # noqa: E402

init_db("sqlite:///data/pipeline.db")
ROOT = Path(".").resolve()

# 待处理 = RealLifeLore 且 timeline.json 不存在 (未跑过新流水)
todo = []
for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    if m.get("entity") == "RealLifeLore":
        vid = m["video_id"]
        if not (ROOT / "data/materials/youtube" / vid / "timeline.json").exists():
            todo.append(vid)
print(f"待识别: {len(todo)} 部", flush=True)

t_start = time.time()
for i, vid in enumerate(todo, 1):
    t0 = time.time()
    print(f"\n[{i}/{len(todo)}] {vid}", flush=True)
    try:
        with get_session_maker()() as db:
            job = MaterialIngestJob(id=f"rlrx-{vid}", mode="url",
                                    entity="RealLifeLore", video_id=vid,
                                    stage="probing")
            db.add(job)
            db.commit()
            svc._probe(job)
            job.stage = "ocr"
            db.commit()
            svc._ocr(job)
            job.stage = "splitting"
            db.commit()
            clips = svc._split(job)
            job.stage = "tagging"
            db.commit()
            svc._tag(job)
            job.stage = "registering"
            db.commit()
            reg = svc._register(db, job)
            bf = svc._backfill_dims(db, job)
            job.stage = "done"
            job.stats_json = {**clips, **reg, "backfilled": bf}
            db.commit()
            print(f"  ✅ {clips.get('clips', 0)} 片 / 入库 {reg.get('registered', 0)} / "
                  f"回填 {bf} ({time.time()-t0:.0f}s)", flush=True)
    except Exception as exc:
        print(f"  ❌ {str(exc)[:120]}", flush=True)
        try:
            with get_session_maker()() as db:
                j = db.get(MaterialIngestJob, f"rlrx-{vid}")
                if j:
                    j.stage = "failed"
                    j.error = str(exc)[:400]
                    db.commit()
        except Exception:
            pass

print(f"\nRLR 识别批完成: 总耗时 {(time.time()-t_start)/60:.0f} 分钟", flush=True)
