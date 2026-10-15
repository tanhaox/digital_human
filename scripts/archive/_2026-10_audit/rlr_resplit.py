# -*- coding: utf-8 -*-
"""RLR 重切批 (2026-09-01, 用户令 A): 修复切片跨镜头拼接后的全量重切.

背景: 阶段1 切片三 bug (scene 0.20 漏检 / _split 兜底无条件追加 / //10 不均分)
→ 已产出的 RLR clips 是"2-3 素材拼在一片"的脏素材 (V20260901-5496 起实测).
修复已落 material_ingest_service (0.05+簇合并 / 洞填补 / ceil 均分).

本批 per vid (CPU only, GPU 不碰):
  1. svc._probe  re-probe 新阈值 (merge 写, 保留 _ocr 的 clean_windows — 最贵段不重跑)
  2. send2trash  旧 clips/ 整目录 (回收站可恢复)
  3. svc._split  新逻辑重切 (manifest 重写, entity/title 保留)
  4. DB: 删该 vid 旧 VideoAsset 行 (脏窗行) + 删 rlrg-/rlrx- job 行 (让 stage2 重跑)
之后重启 rlr_stage2_parallel.py → 4 worker 重打标+入库.

用法:
  python scripts/rlr_resplit.py --only fUWTGTHrB1Y   # 单部试跑
  python scripts/rlr_resplit.py                      # 全量 (4 进程并行)
"""
from __future__ import annotations

import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

from send2trash import send2trash

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

ROOT = Path(".").resolve()
STAGE = ROOT / "data" / "materials" / "youtube"

from app.database import init_db, get_session_maker  # noqa: E402
from app.models import MaterialIngestJob, VideoAsset  # noqa: E402
from app.services import material_ingest_service as svc  # noqa: E402

init_db("sqlite:///data/pipeline.db")


def collect_todo() -> list[str]:
    todo = []
    for mf in sorted(glob.glob(str(STAGE / "*" / "manifest.json"))):
        m = json.loads(Path(mf).read_text(encoding="utf-8"))
        if m.get("entity") == "RealLifeLore" and (Path(mf).parent / "timeline.json").exists():
            todo.append(m["video_id"])
    return todo


def resplit_one(vid: str) -> dict:
    d = STAGE / vid
    old_m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    job = SimpleNamespace(video_id=vid, title=old_m.get("title") or "",
                          entity=old_m.get("entity") or "")
    src = STAGE / f"yt_{vid}.mp4"
    if not src.exists():
        return {"vid": vid, "skip": "源文件缺失"}
    t0 = time.time()
    svc._probe(job)                     # 新阈值 cuts (merge 保 clean_windows)
    # 窗方向修正 (2026-09-01): 旧 timeline 的 clean_windows 是外扩 ±0.5 的脏窗
    # (窗首/尾包进带字半秒) — 从 dirty_sec_list 重建, 内收 0.6, 不重跑 OCR
    tl_path = d / "timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    if tl.get("dirty_sec_list") is not None:
        total = tl.get("total_sec") or 0.0
        dirty = set(tl["dirty_sec_list"])
        raw_wins, run_start = [], None
        for sec in range(int(total) + 1):
            if sec in dirty:
                if run_start is not None:
                    raw_wins.append([run_start, float(sec)])
                    run_start = None
            elif run_start is None:
                run_start = float(sec)
        if run_start is not None:
            raw_wins.append([run_start, total])
        tl["clean_windows"] = [
            [max(0.0, ws + 0.6), min(total, we - 0.6)]
            for ws, we in raw_wins if we - ws - 1.2 >= 2.0
        ]
        tl_path.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    clips_dir = d / "clips"
    if clips_dir.exists():
        send2trash(str(clips_dir))      # 红线: 回收站
    svc._split(job)                     # 重切 (manifest 重写)
    new_m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    return {"vid": vid, "clips": len(new_m.get("clips", [])),
            "old": len(old_m.get("clips", [])), "sec": round(time.time() - t0)}


def clean_db(vids: list[str]) -> dict:
    """串行 DB 清理: 脏窗行 + job 行 (重置后 stage2 会重新 tag+register)."""
    import os
    removed_assets = removed_jobs = 0
    with get_session_maker()() as db:
        for vid in vids:
            d = STAGE / vid
            removed_assets += (
                db.query(VideoAsset)
                .filter(VideoAsset.file_path.like(f"%{os.sep}youtube{os.sep}{vid}{os.sep}%"))
                .delete(synchronize_session=False)
            )
            removed_jobs += (
                db.query(MaterialIngestJob)
                .filter(MaterialIngestJob.id.in_([f"rlrg-{vid}", f"rlrx-{vid}"]))
                .delete(synchronize_session=False)
            )
        db.commit()
    return {"assets_removed": removed_assets, "jobs_reset": removed_jobs}


def main() -> None:
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    todo = [only] if only else collect_todo()
    print(f"重切 {len(todo)} 部 (RLR, timeline 已就绪)", flush=True)

    results = []
    if len(todo) == 1:
        results.append(resplit_one(todo[0]))
    else:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(resplit_one, v): v for v in todo}
            for i, f in enumerate(as_completed(futs), 1):
                r = f.result()
                results.append(r)
                print(f"  [{i}/{len(todo)}] {r}", flush=True)

    ok_vids = [r["vid"] for r in results if "skip" not in r]
    db_stats = clean_db(ok_vids)
    print(f"\n重切完成 {len(ok_vids)}/{len(todo)} | {db_stats}", flush=True)
    print("下一步: python -u scripts/rlr_stage2_parallel.py (重启 GPU 打标入库)", flush=True)


if __name__ == "__main__":
    main()
