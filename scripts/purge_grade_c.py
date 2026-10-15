# -*- coding: utf-8 -*-
"""弃源清理 (2026-09-01): 读 vid 清单 → 行清+源/staging 回收站. 默认 _grade_C.txt,
可传任意清单文件: python scripts/purge_grade_c.py <清单.txt> [备份标签]."""
import shutil
import sys
from pathlib import Path

from send2trash import send2trash

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

ROOT = Path(".").resolve()
STAGE = ROOT / "data" / "materials" / "youtube"

list_file = sys.argv[1] if len(sys.argv) > 1 else "_grade_C.txt"
bak_tag = sys.argv[2] if len(sys.argv) > 2 else Path(list_file).stem
shutil.copy2("data/pipeline.db", f"data/pipeline.db.bak-20260901-{bak_tag}")

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import MaterialIngestJob, VideoAsset  # noqa: E402

set_config(load_config())
init_db("sqlite:///data/pipeline.db")

vids = [v for v in Path(list_file).read_text().split() if v]
print(f"弃源 {len(vids)} 部 (清单 {list_file})", flush=True)

rows = jobs = trashed = 0
with get_session_maker()() as db:
    for i, vid in enumerate(vids, 1):
        rows += (
            db.query(VideoAsset)
            .filter(VideoAsset.file_path.contains(f"\\youtube\\{vid}\\"))
            .delete(synchronize_session=False)
        )
        jobs += (
            db.query(MaterialIngestJob)
            .filter(MaterialIngestJob.id.in_([f"rlrg-{vid}", f"rlrx-{vid}"]))
            .delete(synchronize_session=False)
        )
        try:
            send2trash(str(STAGE / vid))
            trashed += 1
        except Exception as exc:
            print(f"  staging trash 失败 {vid}: {exc}", flush=True)
        src = STAGE / f"yt_{vid}.mp4"
        if src.exists():
            try:
                send2trash(str(src))
            except Exception:
                pass
        if i % 20 == 0:
            db.commit()
            print(f"  {i}/{len(vids)} ...", flush=True)
    db.commit()
print(f"完成: 清行 {rows} / job {jobs} / staging 回收 {trashed} 部 (源视频一并回收站)", flush=True)
print(f"剩余 RLR 源: {len(list(STAGE.glob('yt_*.mp4')))} 部 (A 72 + B 81 = 153)", flush=True)
