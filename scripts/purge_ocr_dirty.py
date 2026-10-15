# -*- coding: utf-8 -*-
"""OCR 清库 (2026-08-31): 已入库资产按 OCR 复核结果除名脏片.

manifest 里 ocr_clean=False 的切片 → 对应 VideoAsset 行删除 (文件保留在
staging 可复核)。对应既定流程: OCR 快筛 → 清库 → 红水位补弹。
"""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

ROOT = Path(".").resolve()
dirty_files: set[str] = set()
clean = 0
for mf in glob.glob("data/materials/youtube/*/manifest.json"):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    for c in m.get("clips", []):
        if c.get("ocr_clean") is False:
            dirty_files.add(str((ROOT / c["file"]).resolve()))
        elif c.get("ocr_clean") is True:
            clean += 1
print(f"OCR 判定: 干净 {clean} 片 / 脏 {len(dirty_files)} 片")

init_db("sqlite:///data/pipeline.db")
with get_session_maker()() as db:
    # 只除名已入库且被判脏的
    rows = db.query(VideoAsset).filter(VideoAsset.source == "youtube").all()
    removed = 0
    for r in rows:
        if r.file_path in dirty_files:
            db.delete(r)
            removed += 1
    db.commit()
    lib = db.query(VideoAsset).count()
print(f"清库: 除名 {removed} 条脏片 | 库容 → {lib}")
