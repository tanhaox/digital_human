# -*- coding: utf-8 -*-
"""22 部未分级补体检 (probe+ocr fps=3) → 追加分级清单."""
import glob
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.services import material_ingest_service as svc  # noqa: E402

ROOT = Path(".").resolve()
graded = set()
for mf in glob.glob(str(ROOT / "data/materials/youtube/*/timeline.json")):
    d = Path(mf).parent
    try:
        ent = json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("entity") or ""
    except Exception:
        ent = ""
    tl = json.loads(Path(mf).read_text(encoding="utf-8"))
    if ent == "RealLifeLore" and tl.get("dirty_sec_list") is not None:
        graded.add(d.name)
srcs = {p.stem[3:] for p in (ROOT / "data/materials/youtube").glob("yt_*.mp4")}
todo = sorted(srcs - graded)
print(f"补体检: {len(todo)} 部", flush=True)


def one(vid: str) -> dict:
    d = ROOT / "data/materials/youtube" / vid
    d.mkdir(exist_ok=True)
    job = SimpleNamespace(video_id=vid, title="", entity="RealLifeLore")
    t0 = time.time()
    svc._probe(job)
    r = svc._ocr(job)
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    total = tl.get("total_sec") or 0
    dsl = tl.get("dirty_sec_list") or []
    pct = round((1 - len(dsl) / total) * 100) if total else 0
    return {"vid": vid, "pct": pct, "min": round(total / 60), "sec": round(time.time() - t0)}


results = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(one, v): v for v in todo}
    for i, f in enumerate(as_completed(futs), 1):
        r = f.result()
        results.append(r)
        print(f"  [{i}/{len(todo)}] {r}", flush=True)

keep = sorted([r for r in results if r["pct"] >= 45], key=lambda x: -x["pct"])
bone = [r for r in results if r["pct"] < 45]
with open("_review_153.txt", "a", encoding="utf-8") as f:
    for r in keep:
        f.write(f"{r['vid']}  干净{r['pct']}%  {r['min']}分钟  (补检)\n")
with open("_grade_C.txt", "a", encoding="utf-8") as f:
    for r in bone:
        f.write(r["vid"] + "\n")
print(f"补检完成: 留审 +{len(keep)} / 骨头 +{len(bone)} (清单已追加)", flush=True)
