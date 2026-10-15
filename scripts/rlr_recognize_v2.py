# -*- coding: utf-8 -*-
"""RLR 277 部并行识别 v2: CPU 段(probe/OCR/净窗切) 4进程并行, GPU 段(LLM)排队.
VPN 必须退出. 幂等: timeline.json 已存在跳过 CPU 段, clips tags 已有跳过 LLM."""
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.services.gpu_service_manager._manager import vpn_running  # noqa: E402

pid = vpn_running()
print("VPN:", pid if pid else "已退出 ✓", flush=True)
if pid:
    raise SystemExit("请先彻底退出变色龙再跑")

ROOT = Path(".").resolve()

# ── 阶段 1: CPU 段 4 进程 (probe → ocr 时间轴 → 净窗切片) ──
todo = []
for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    if m.get("entity") == "RealLifeLore":
        vid = m["video_id"]
        if not (ROOT / "data/materials/youtube" / vid / "timeline.json").exists():
            todo.append(vid)
print(f"阶段1 (CPU×4): {len(todo)} 部待 probe/OCR/切片", flush=True)

N = 10  # GPU OCR 并行提至 10 (2026-08-31: 显存 3.4/49GB 大量空, 瓶颈在 ffmpeg 抽帧串行)
procs = []
for i in range(N):
    shard = todo[i::N]
    if not shard:
        continue
    code = (
        "import sys, json, time; sys.path.insert(0, '.');\n"
        "from pathlib import Path;\n"
        "from app.services import material_ingest_service as svc;\n"
        "class J: pass;\n"
        f"for vid in {shard!r}:\n"
        "    j = J(); j.video_id = vid; j.entity = 'RealLifeLore'; j.title = '';\n"
        "    try:\n"
        "        svc._probe(j); svc._ocr(j); c = svc._split(j);\n"
        "        print(f'  CPU {vid}: {c}', flush=True);\n"
        "    except Exception as e:\n"
        "        print(f'  CPU {vid} FAIL: {e}', flush=True);\n")
    p = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=str(ROOT))
    procs.append((i, len(shard), p))
    print(f"  CPU 片 {i}: {len(shard)} 部 (PID {p.pid})", flush=True)

for i, n, p in procs:
    p.wait()
    print(f"  CPU 片 {i} 完成 (exit={p.returncode})", flush=True)
print("阶段1 完成 — CPU 段全部就绪", flush=True)

# ── 阶段 2: GPU 段单进程串行 (llama 打标 + 入库 + 回填) ──
sys.path.insert(0, ".")
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import MaterialIngestJob  # noqa: E402
from app.services import material_ingest_service as svc  # noqa: E402

init_db("sqlite:///data/pipeline.db")
tag_todo = []
for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    if m.get("entity") == "RealLifeLore":
        vid = m["video_id"]
        clips = m.get("clips", [])
        if clips and any(not c.get("tags") and c.get("ocr_clean") is not False
                        for c in clips):
            tag_todo.append(vid)
print(f"阶段2 (GPU 串行): {len(tag_todo)} 部待 LLM+入库", flush=True)

t0 = time.time()
total_reg = 0
for i, vid in enumerate(tag_todo, 1):
    try:
        with get_session_maker()() as db:
            job = MaterialIngestJob(id=f"rlrg-{vid}", mode="url",
                                    entity="RealLifeLore", video_id=vid,
                                    stage="tagging")
            db.add(job)
            db.commit()
            svc._tag(job)
            job.stage = "registering"
            db.commit()
            reg = svc._register(db, job)
            bf = svc._backfill_dims(db, job)
            job.stage = "done"
            job.stats_json = {**reg, "backfilled": bf}
            db.commit()
            total_reg += reg.get("registered", 0)
            if i % 10 == 0 or i == len(tag_todo):
                el = (time.time() - t0) / 60
                print(f"  [{i}/{len(tag_todo)}] 累计入库 {total_reg} "
                      f"({el:.0f}min, 均 {el/i:.1f}min/部)", flush=True)
    except Exception as exc:
        print(f"  GPU {vid} FAIL: {str(exc)[:100]}", flush=True)
print(f"\nRLR 并行识别批完成: 入库合计 {total_reg} 条, "
      f"GPU 段 {(time.time()-t0)/60:.0f} 分钟", flush=True)
