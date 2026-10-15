# -*- coding: utf-8 -*-
"""RLR 2 部走新流水全链: probe → ocr 时间轴 → 净窗重切 → tag → register."""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.services.gpu_service_manager._manager import vpn_running  # noqa: E402

pid = vpn_running()
print("VPN 进程:", pid if pid else "已退出 ✓")
if pid:
    raise SystemExit("请先彻底退出变色龙再跑")

from app.services import material_ingest_service as svc  # noqa: E402

for vid in ("nEBcD6sFDpY", "AD4vPNBSrKY"):
    print(f"\n━━━ {vid} ━━━", flush=True)
    t0 = time.time()

    class _J:  # 轻量 job 替身 (直接调段函数, 不落 MaterialIngestJob 表)
        pass
    j = _J()
    j.video_id = vid
    j.entity = "RealLifeLore"
    j.title = ""
    j.stage = "probe"

    print("→ probe", flush=True)
    print("  ", svc._probe(j), flush=True)
    print("→ ocr 时间轴", flush=True)
    print("  ", svc._ocr(j), flush=True)
    print("→ 净窗切片", flush=True)
    print("  ", svc._split(j), flush=True)
    # LLM 打标 (直接用 yt_ingest.cmd_tag — ocr_clean=False 自动跳过)
    print("→ LLM 打标", flush=True)
    print("  ", svc._tag(j), flush=True)
    # 入库 (需要 db session)
    from app.database import init_db, get_session_maker
    init_db("sqlite:///data/pipeline.db")
    with get_session_maker()() as db:
        from app.models import MaterialIngestJob
        job = MaterialIngestJob(id="rlr-" + vid, mode="url", entity="RealLifeLore",
                                video_id=vid, stage="registering")
        db.add(job)
        db.commit()
        print("→ 入库+回填", flush=True)
        print("  ", svc._register(db, job), flush=True)
        print("  回填:", svc._backfill_dims(db, job), flush=True)
        job.stage = "done"
        db.commit()
    print(f"  ⏱ 全链 {time.time()-t0:.0f}s", flush=True)
