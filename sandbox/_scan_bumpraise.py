# -*- coding: utf-8 -*-
"""扫样本批新片: 大字卡(REAL LIFE LORE / MADE POSSIBLE BY)漏网统计."""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.config import load_config, set_config
set_config(load_config())
from app.services.material_ingest_service import _new_ocr
import numpy as np

ROOT = Path(".").resolve()
FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
vids = Path("_sample50_vids.txt").read_text().split()

ocr = _new_ocr()
hits, checked = [], 0
for v in vids[:25]:  # 前 25 部控时长
    mf = ROOT / "data/materials/youtube" / v / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    for c in m.get("clips", []):
        p = ROOT / c["file"]
        if not p.exists():
            continue
        checked += 1
        for t in (0.3, (c["end"] - c["start"]) / 2):  # 片头+片中两帧
            r = subprocess.run([FF, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(p),
                                "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3",
                                "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                               capture_output=True, timeout=20)
            fb = len(r.stdout)
            if fb == 0:
                continue
            h = fb // (1280 * 3)
            frame = np.frombuffer(r.stdout, dtype=np.uint8).reshape(h, 1280, 3)
            res, _ = ocr(frame)
            for box in (res or []):
                txt = str(box[1]).upper()
                conf = float(box[2])
                if "POSSIBLE" in txt or "REAL LIFE" in txt or "REALLIFE" in txt:
                    hits.append((v, p.stem, t, txt[:20], round(conf, 2)))
                    break
print(f"扫 {checked} 片 (25 部, 片头+片中双帧):")
print(f"字卡命中: {len(hits)}")
for h in hits[:15]:
    print("  ", h)
