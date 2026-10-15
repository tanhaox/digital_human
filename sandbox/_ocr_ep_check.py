# -*- coding: utf-8 -*-
"""查证: RapidOCR 的 det/rec session 实际跑在哪个 EP 上; 原生 CUDA session 能否建起来。"""
import sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import numpy as np
import onnxruntime as ort

from app.services.material_ingest_service import _new_ocr  # 调用即完成 DLL 注入

o = _new_ocr()
td, tr = o.text_detector, o.text_recognizer
print("det.infer 类型:", type(td.infer).__name__)
sess_d = getattr(td.infer, "session", None)
sess_r = getattr(getattr(tr, "infer", None), "session", None)
if sess_r is None:  # rec 可能按行持有, 翻属性找 session
    for a in dir(tr):
        v = getattr(tr, a)
        s = getattr(v, "session", None)
        if s is not None and hasattr(s, "get_providers"):
            sess_r = s
            print(f"rec session 藏在 .{a}")
            break
print("det session providers:", sess_d.get_providers() if sess_d else "?")
print("rec session providers:", sess_r.get_providers() if sess_r else "?")

# 原生直建 CUDA session 试试
import rapidocr_onnxruntime as r
det_path = Path(r.__file__).parent / "models" / "ch_PP-OCRv3_det_infer.onnx"
print("\n直建 InferenceSession (CUDA, device_id=1):")
try:
    s1 = ort.InferenceSession(str(det_path), providers=[
        ("CUDAExecutionProvider", {"device_id": 1}),
        "CPUExecutionProvider"])
    print("  providers:", s1.get_providers())
except Exception as e:
    print("  FAIL:", str(e)[:200])

print("直建 (CUDA, device_id=0):")
try:
    s0 = ort.InferenceSession(str(det_path), providers=[
        ("CUDAExecutionProvider", {"device_id": 0}),
        "CPUExecutionProvider"])
    print("  providers:", s0.get_providers())
except Exception as e:
    print("  FAIL:", str(e)[:200])

# 若 CUDA 直建成功, 实测一帧推理耗时对比
if s1.get_providers()[0] == "CUDAExecutionProvider":
    x = np.random.rand(1, 3, 960, 960).astype(np.float32)
    for _ in range(3):
        s1.run(None, {s1.get_inputs()[0].name: x})
    t0 = time.perf_counter()
    for _ in range(10):
        s1.run(None, {s1.get_inputs()[0].name: x})
    print(f"GPU直跑 det 960x960: {(time.perf_counter()-t0)/10*1000:.1f}ms/帧")
