# -*- coding: utf-8 -*-
"""修复后测速: 管道法 @1280/960/720, GPU OCR (rec 已强制 CUDA)。"""
import subprocess, sys, time
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
STAGE = Path("data/materials/youtube")
vid = sys.argv[1] if len(sys.argv) > 1 else "GlkCofOyxUA"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 150
video = str(STAGE / f"yt_{vid}.mp4")

from app.services.material_ingest_service import _new_ocr
ocr = _new_ocr()
print("rec EP:", ocr.text_recognizer.session.session.get_providers()[0], flush=True)


def bench(scale):
    probe = subprocess.run([FF, "-v", "error", "-i", video, "-vf",
                            f"fps=1,scale={scale}:-2", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                           capture_output=True)
    fb = len(probe.stdout)
    if fb == 0:
        print(f"B{scale}: 抽帧失败")
        return
    H = fb // (scale * 3)
    p = subprocess.Popen([FF, "-v", "error", "-i", video, "-vf",
                          f"fps=1,scale={scale}:-2", "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "pipe:1"], stdout=subprocess.PIPE)
    # 预热
    raw = p.stdout.read(fb)
    if len(raw) == fb:
        ocr(np.frombuffer(raw, dtype=np.uint8).reshape(H, scale, 3).copy())
    t0 = time.perf_counter()
    n = 0
    for k in range(N):
        raw = p.stdout.read(fb)
        if len(raw) < fb:
            break
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(H, scale, 3)
        ocr(frame)
        n += 1
    el = time.perf_counter() - t0
    p.kill()
    print(f"B 管道{scale}: {el/n*1000:.0f}ms/帧 ({n}帧)", flush=True)


for s in (1280, 960, 720):
    bench(s)
print("对比: 修复前 A法757ms / B法309ms")
