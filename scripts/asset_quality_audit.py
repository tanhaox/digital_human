# -*- coding: utf-8 -*-
"""素材库质量体检 + 批量处置 — 烂素材全面治理工具 (2026-08-16).

用法:
    python scripts/asset_quality_audit.py                # 只读体检报告
    python scripts/asset_quality_audit.py --blacklist-low-res   # 低清全部标记 dislike
    python scripts/asset_quality_audit.py --blacklist-short N   # 时长 < N 秒标记 dislike
    python scripts/asset_quality_audit.py --restore ASSET_NO    # 恢复单个素材为 neutral
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import load_config, set_config

set_config(load_config())
from app.database import init_db, get_session_maker

init_db("sqlite:///data/pipeline.db")
db = get_session_maker()()
from app.models import VideoAsset


def is_low_res(a: VideoAsset) -> bool:
    if a.orientation == "portrait":
        return (a.height or 0) < 1280
    return (a.width or 0) < 1280


assets = db.query(VideoAsset).all()
low_res = [a for a in assets if is_low_res(a)]
short = [a for a in assets if (a.duration_sec or 0) < 5]
disliked = [a for a in assets if a.preference == "dislike"]
hi_res = [a for a in assets if not is_low_res(a)]

print(f"素材库总况: {len(assets)} 个")
print(f"  低清(横宽<1280/竖高<1280): {len(low_res)}  ← 治理对象")
print(f"  时长<5s:                   {len(short)}")
print(f"  已 dislike(出局):          {len(disliked)}")
print(f"  质量线以上可用:            {len(hi_res)}")

print("\n低清素材 TOP20 (按已用次数排序 — 用得越多越该杀):")
for a in sorted(low_res, key=lambda x: -(x.used_count or 0))[:20]:
    print(f"  {a.asset_no} used={a.used_count or 0} {a.orientation} {a.width}x{a.height} "
          f"{(a.description_zh or a.description_en or '?')[:36]}")

args = sys.argv[1:]
if "--blacklist-low-res" in args:
    n = 0
    for a in low_res:
        if a.preference != "dislike":
            a.preference = "dislike"
            n += 1
    db.commit()
    print(f"\n✅ 已将 {n} 个低清素材标记 dislike (出局, 可 --restore 恢复)")
if "--blacklist-short" in args:
    try:
        sec = float(args[args.index("--blacklist-short") + 1])
    except (IndexError, ValueError):
        sec = 5.0
    n = 0
    for a in short:
        if (a.duration_sec or 0) < sec and a.preference != "dislike":
            a.preference = "dislike"
            n += 1
    db.commit()
    print(f"\n✅ 已将 {n} 个 <{sec}s 素材标记 dislike")
if "--restore" in args:
    try:
        no = args[args.index("--restore") + 1]
    except IndexError:
        no = None
    if no:
        a = db.query(VideoAsset).filter(VideoAsset.asset_no == no).first()
        if a:
            a.preference = "neutral"
            db.commit()
            print(f"\n✅ {no} 已恢复 neutral")
db.close()
