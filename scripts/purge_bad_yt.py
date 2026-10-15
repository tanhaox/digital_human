# -*- coding: utf-8 -*-
"""两日不合格 yt 清理 (2026-08-31 用户令):
① 视频级: OCR 全脏(净窗总长<4s)或下载失败残留 → 回收源视频+staging
② 切片级: ocr_clean=False 的切片文件回收 (manifest 记录保留)
③ 库级: 对应 VideoAsset 脏条目除名
"""
import glob
import json
import sys
from pathlib import Path
from send2trash import send2trash

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
ROOT = Path(".").resolve()

purged_videos = 0
purged_clips = 0
dirty_files = set()

for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    vid = m["video_id"]
    stage = Path(mf).parent
    src = ROOT / "data/materials/youtube" / f"yt_{vid}.mp4"
    clips = m.get("clips", [])

    dirty = [c for c in clips if c.get("ocr_clean") is False]
    clean = [c for c in clips if c.get("ocr_clean") is True]
    unchecked = [c for c in clips if "ocr_clean" not in c]

    # ① 视频级: 无未筛片段 且 干净切片 <2 且 净时长<8s → 整片回收
    clean_dur = sum(c["end"] - c["start"] for c in clean)
    if not unchecked and len(clean) < 2 and clean_dur < 8:
        send2trash(str(stage))
        if src.exists():
            send2trash(str(src))
        purged_videos += 1
        # 整片的全部切片行也要进除名集合 (2026-09-01 修: 此前只 ② 切片级收集,
        # ① 分支的 DB 行永远漏删 → library 出现 615+ 播 404 的孤儿行)
        for c in clips:
            dirty_files.add(str(ROOT / c["file"]))
        print(f"  整片回收 {vid} [{m.get('entity','?')}] 净{clean_dur:.0f}s/{len(clips)}片")
        continue

    # ② 切片级: 脏片文件回收, manifest 保留记录
    for c in dirty:
        f = ROOT / c["file"]
        if f.exists():
            send2trash(str(f))
            dirty_files.add(str(f))
            purged_clips += 1
    if dirty:
        print(f"  脏片回收 {vid}: {len(dirty)} 个切片文件")

# ③ 库级除名
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402
init_db("sqlite:///data/pipeline.db")
with get_session_maker()() as db:
    removed = 0
    for r in db.query(VideoAsset).filter(VideoAsset.source == "youtube").all():
        if r.file_path in dirty_files:
            db.delete(r)
            removed += 1
    db.commit()
    lib = db.query(VideoAsset).count()
print(f"\n清理汇总: 整片回收 {purged_videos} 部 | 脏切片回收 {purged_clips} 个 | 库除名 {removed} 条 | 库容 → {lib}")
