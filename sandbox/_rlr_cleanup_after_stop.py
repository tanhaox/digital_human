# -*- coding: utf-8 -*-
"""全停后清理 (v2):
1. 无 sec_flags 的 timeline -> 删 (203垃圾级联 + 14首灌僵尸 + 中途被杀的OCR在制)
2. 有 sec_flags 但 manifest 比 timeline 旧 (split被杀半途) -> 只补跑 _split, 不重OCR
3. 真完成 (manifest新于timeline) -> 保留
输出恢复时的待跑清单。"""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
STAGE = Path("data/materials/youtube")

kept, garbage, rescued, redo = [], [], [], []
for mf in sorted(STAGE.glob("*/manifest.json")):
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        continue
    if m.get("entity") != "RealLifeLore":
        continue
    vid = m["video_id"]
    d = STAGE / vid
    tl = d / "timeline.json"
    if not tl.exists():
        redo.append(vid)          # 从未开始 (hold 的 203 部 + 其他)
        continue
    try:
        t = json.loads(tl.read_text(encoding="utf-8"))
    except Exception:
        tl.unlink()
        garbage.append(vid)
        continue
    if "sec_flags" not in t:
        tl.unlink()               # 垃圾(total_sec=0) / 僵尸 / 被杀OCR在制
        garbage.append(vid)
        redo.append(vid)
        continue
    # OCR 完成; 判断 split 是否完成: manifest 必须比 timeline 新 (split 在 ocr 后写)
    if mf.stat().st_mtime > tl.stat().st_mtime + 1.0:
        kept.append(vid)
    else:
        rescued.append(vid)       # 只需补 split

# 补跑被杀半途的 split (快: -c copy 切片, 已存在的 clip 文件会跳过)
if rescued:
    from app.services import material_ingest_service as svc

    class J:
        pass
    for vid in rescued:
        j = J(); j.video_id = vid; j.entity = "RealLifeLore"; j.title = ""
        try:
            c = svc._split(j)
            print(f"  补split {vid}: {c['clips']}片")
        except Exception as e:
            print(f"  补split {vid} FAIL: {str(e)[:80]} (删timeline转重跑)")
            (STAGE / vid / "timeline.json").unlink(missing_ok=True)
            redo.append(vid)

print(f"\n真完成(OCR+split): {len(kept)}")
print(f"抢救补split: {len(rescued)}")
print(f"删除timeline(垃圾/僵尸/被杀在制): {len(garbage)}")
print(f"待重跑(无timeline): {len(redo)}")
n_hold = len(list(STAGE.glob('yt_*.mp4.hold')))
print(f".hold 源(恢复前改名回): {n_hold}")
