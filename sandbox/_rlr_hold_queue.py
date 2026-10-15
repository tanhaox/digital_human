# -*- coding: utf-8 -*-
"""停队列: RLR 未开始的视频源改名 .hold; 在制的(有timeline无sec_flags)不动。
同时输出真实完成数(sec_flags) / 在制数 / 持有数。"""
import glob, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
STAGE = Path("data/materials/youtube")

queued, inflight, done = [], [], []
for mf in sorted(STAGE.glob("*/manifest.json")):
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        continue
    if m.get("entity") != "RealLifeLore":
        continue
    vid = m["video_id"]
    tl = STAGE / vid / "timeline.json"
    if not tl.exists():
        queued.append(vid)
    else:
        try:
            t = json.loads(tl.read_text(encoding="utf-8"))
        except Exception:
            queued.append(vid)
            continue
        if "sec_flags" in t:
            done.append(vid)
        else:
            inflight.append(vid)

held = 0
for vid in queued:
    src = STAGE / f"yt_{vid}.mp4"
    if src.exists():
        src.rename(STAGE / f"yt_{vid}.mp4.hold")
        held += 1

print(f"真实OCR完成(sec_flags): {len(done)}")
print(f"在制(probe完成/OCR或split进行中): {len(inflight)} -> {inflight}")
print(f"队列未开始 -> 已改名.hold: {held}/{len(queued)}")
left = [v for v in queued if (STAGE / f"yt_{v}.mp4").exists()]
if left:
    print(f"!! 未成功改名: {left}")
