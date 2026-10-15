# -*- coding: utf-8 -*-
"""RLR 样本批 50 部端到端 (2026-09-01 用户令: 跑50条看结果).

对字母序前 50 部 (含已 reocr 的 5 部) 依次: _ocr(fps=3) → _probe → 窗修正 →
_split(新参数重切) → 清旧行/重置 job → 自动接 stage2 --vids 打标入库。
用户抽查通过后再全量。

用法: python -u scripts/rlr_sample50.py
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from app.services import material_ingest_service as svc  # noqa: E402

ROOT = Path(".").resolve()
STAGE = ROOT / "data" / "materials" / "youtube"
N = 50


def one(vid: str) -> dict:
    d = STAGE / vid
    old = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    job = SimpleNamespace(video_id=vid, title=old.get("title") or "",
                          entity=old.get("entity") or "RealLifeLore")
    t0 = time.time()
    r = svc._ocr(job)                      # fps=3 时间轴 (merge 保留 cuts)
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    if tl.get("dirty_sec_list") is None:
        return {"vid": vid, "error": "ocr 未产 dirty_sec_list"}
    # 窗修正 (内收 0.6, 从 dirty_sec_list 重建) — 与 rlr_resplit 同逻辑
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
    (d / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    # 重切 (resplit_one 的 probe/trash/split 逻辑)
    from rlr_resplit import resplit_one
    rr = resplit_one(vid)
    rr["ocr_dirty"] = r.get("dirty_sec")
    rr["sec"] = round(time.time() - t0)
    return rr


def main() -> None:
    todo = []
    for mf in sorted(glob.glob(str(STAGE / "*" / "timeline.json"))):
        vid = Path(mf).parent.name
        m = STAGE / vid / "manifest.json"
        if m.exists():
            try:
                if json.loads(m.read_text(encoding="utf-8")).get("entity") != "RealLifeLore":
                    continue
            except Exception:
                continue
        todo.append(vid)
    todo = todo[:N]
    Path("_sample50_vids.txt").write_text("\n".join(todo), encoding="utf-8")
    print(f"样本批 {len(todo)} 部 (清单 _sample50_vids.txt), 4 进程", flush=True)
    ok = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(one, v): v for v in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            ok.append(r["vid"])
            print(f"  [{i}/{len(todo)}] {r}", flush=True)
    print(f"重切完成 {len(ok)}/{len(todo)} 部 (清单 _sample50_vids.txt)", flush=True)
    print("下一步(分步执行): ① 清 50 部旧行 ② stage2 --vids _sample50_vids.txt", flush=True)


if __name__ == "__main__":
    main()
