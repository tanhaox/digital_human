# -*- coding: utf-8 -*-
"""GPU OCR 基准 v2: add_dll_directory 方式加载 cuDNN (Win Py3.8+ 不继承 PATH)."""
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NV = ROOT / ".venv/Lib/site-packages/nvidia"
for sub in ("cudnn/bin", "cublas/bin", "cuda_nvrtc/bin", "cufft/bin", "nvjitlink/bin"):
    d = NV / sub
    if d.exists():
        os.add_dll_directory(str(d))
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")

from rapidocr_onnxruntime import RapidOCR  # noqa: E402
import rapidocr_onnxruntime as _r  # noqa: E402
_MODELS = Path(_r.__file__).parent / "models"

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
clip = str(ROOT / "data/materials/youtube/nEBcD6sFDpY/clips/clip_003.mp4")
tmp = tempfile.mkdtemp()
_MODELS = Path(_r.__file__).parent / "models"
ocr = RapidOCR(det_use_cuda=True, rec_use_cuda=True,
               det_model_path=str(_MODELS / "ch_PP-OCRv3_det_infer.onnx"),
               rec_model_path=str(_MODELS / "ch_PP-OCRv3_rec_infer.onnx"))
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
# 预热后二次测量 (首次含 CUDA context/cudnn 初始化)
t0 = time.time()
for fp in frames:
    ocr(fp)
dt2 = time.time() - t0
print(f"GPU OCR: 首轮 {dt/len(frames)*1000:.0f}ms/帧, 预热后 {dt2/len(frames)*1000:.0f}ms/帧 (CPU 1800ms)")
