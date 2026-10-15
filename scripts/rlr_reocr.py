# -*- coding: utf-8 -*-
"""RLR OCR 时间轴重扫 (2026-09-01): fps=3 修闪字盲区后全量重建 dirty 数据.

背景: _ocr 原 fps=1 对 <1s 飞入即出的动效闪字全盲 (V20260901-4417 实锤) →
提采样密度到 3 帧/秒。本批重跑 277 部 OCR (GPU CUDA, 4 进程, 预计 ~2h),
timeline merge 写保留 cuts; 跑完后 rlr_resplit (窗修正用新 dirty_sec_list)
→ rlr_stage2_parallel (is_real_footage 打标入库)。

用法: python -u scripts/rlr_reocr.py [--only <视频id>]
"""
from __future__ import annotations

import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

ROOT = Path(".").resolve()

from app.services import material_ingest_service as svc  # noqa: E402

# 每进程独立 OCR 实例 (CUDA session 不跨线程共享安全)
_tls_cache: dict[int, object] = {}


def _ocr_one(vid: str) -> dict:
    d = ROOT / "data" / "materials" / "youtube" / vid
    src = ROOT / "data" / "materials" / "youtube" / f"yt_{vid}.mp4"
    if not src.exists():
        return {"vid": vid, "skip": "源文件缺失"}
    old = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    job = SimpleNamespace(video_id=vid, title=old.get("title") or "",
                          entity=old.get("entity") or "RealLifeLore")
    t0 = time.time()
    r = svc._ocr(job)   # merge 写: 保留 cuts/total_sec
    return {"vid": vid, "wins": r.get("clean_windows"), "dirty": r.get("dirty_sec"),
            "sec": round(time.time() - t0)}


def main() -> None:
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    todo = []
    if only:
        todo = [only]
    else:
        for mf in sorted(glob.glob(str(ROOT / "data/materials/youtube/*/timeline.json"))):
            vid = Path(mf).parent.name
            m = ROOT / "data/materials/youtube" / vid / "manifest.json"
            ent = ""
            if m.exists():
                try:
                    ent = json.loads(m.read_text(encoding="utf-8")).get("entity") or ""
                except Exception:
                    pass
            if ent == "RealLifeLore":
                todo.append(vid)
    print(f"OCR 重扫 (fps=3): {len(todo)} 部, 4 进程", flush=True)
    n = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_ocr_one, v): v for v in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            print(f"  [{i}/{len(todo)}] {r}", flush=True)
    print(f"OCR 重扫完成 {n or len(todo)} 部", flush=True)
    print("下一步: python -u scripts/rlr_resplit.py → rlr_stage2_parallel.py", flush=True)


if __name__ == "__main__":
    main()
