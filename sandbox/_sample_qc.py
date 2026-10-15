# -*- coding: utf-8 -*-
"""样本批切片质量抽验 (CPU): 单镜头纯净度(scene) + 无字(OCR)."""
import glob
import json
import random
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
ROOT = Path(".").resolve()
FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
random.seed(42)

# 已重切的 50 部清单里, 取 manifest clips 有值的 (重切完成)
vids = Path("_sample50_vids.txt").read_text().split()
clips = []
for v in vids:
    mf = ROOT / "data/materials/youtube" / v / "manifest.json"
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not m.get("clips") or (m["clips"][0].get("tags")):
        continue  # 空/未重切跳过 (tags 有值 = 旧 manifest)
    for c in m["clips"]:
        clips.append(ROOT / c["file"])
print(f"可抽验切片: {len(clips)} (来自已完成重切的部)")
sample = random.sample(clips, min(40, len(clips)))

from app.config import load_config, set_config
set_config(load_config())
from app.services.material_ingest_service import _new_ocr

ocr = _new_ocr()
import numpy as np

bad_scene, bad_text, checked = [], [], 0
for p in sample:
    if not p.exists():
        continue
    checked += 1
    # ① 单镜头纯净度: 片内帧间 scene score
    r = subprocess.run([FF, "-i", str(p), "-vf",
                        "select='gt(scene,0.10)',metadata=print", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=60)
    hits = re.findall(r"pts_time:([0-9.]+)", r.stderr or "")
    if hits:
        bad_scene.append((p.stem, [round(float(t), 1) for t in hits[:3]]))
    # ② 无字: fps=2 抽帧 OCR
    pr = subprocess.run([FF, "-v", "error", "-i", str(p), "-vf",
                         "fps=2,scale=1280:-2", "-frames:v", "1",
                         "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                        capture_output=True, timeout=30)
    fb = len(pr.stdout)
    if fb > 0:
        h = fb // (1280 * 3)
        frame = np.frombuffer(pr.stdout, dtype=np.uint8).reshape(h, 1280, 3)
        res, _ = ocr(frame)
        if res:
            bad_text.append((p.stem, [r[1][:10] for r in res[:2]]))

print(f"\n抽验 {checked} 片:")
print(f"  单镜头纯净 (片内无 ≥0.10 切点): {checked - len(bad_scene)}/{checked}")
if bad_scene:
    for s, ts in bad_scene[:8]:
        print(f"    ⚠ {s} 片内切点@{ts}")
print(f"  无字: {checked - len(bad_text)}/{checked}")
if bad_text:
    for s, ts in bad_text[:8]:
        print(f"    ⚠ {s} 文字 {ts}")
