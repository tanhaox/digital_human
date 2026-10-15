# -*- coding: utf-8 -*-
"""GPU OCR 基准: 真帧实测 RapidOCR CUDA 速度."""
import os
import subprocess
import tempfile
import time

from rapidocr_onnxruntime import RapidOCR

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
clip = "data/materials/youtube/nEBcD6sFDpY/clips/clip_003.mp4"
tmp = tempfile.mkdtemp()
ocr = RapidOCR(det_use_cuda=True, rec_use_cuda=True,
               det_model_path=None, rec_model_path=None,
               det_model_dir=None, rec_model_dir=None)
frames = []
for k in range(5):
    fp = os.path.join(tmp, f"f{k}.jpg")
    subprocess.run([FF, "-y", "-v", "error", "-ss", str(k * 2 + 0.5), "-i", clip,
                    "-frames:v", "1", "-vf", "scale=1280:-2",
                    "-pix_fmt", "yuvj420p", fp], capture_output=True)
    if os.path.exists(fp):
        frames.append(fp)
t0 = time.time()
for fp in frames:
    ocr(fp)
dt = time.time() - t0
print(f"GPU OCR: {dt:.2f}s / {len(frames)}帧 = {dt / len(frames) * 1000:.0f}ms/帧 (CPU 实测 1800ms)")
