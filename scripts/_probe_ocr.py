# -*- coding: utf-8 -*-
"""OCR 探针 v2: clip_000 多个时间点 + 一帧全图看水印位置."""
import os
import subprocess
import tempfile

from rapidocr_onnxruntime import RapidOCR

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
clip = "data/materials/youtube/_Wkz-y07OdU/clips/clip_000.mp4"
tmp = tempfile.mkdtemp()
ocr = RapidOCR()
for clip_n in ("clip_000", "clip_004", "clip_008"):
    clip = f"data/materials/youtube/_Wkz-y07OdU/clips/{clip_n}.mp4"
    for t in ("2", "6"):
        fp = os.path.join(tmp, f"{clip_n}_{t}.jpg")
        subprocess.run([FF, "-y", "-v", "error", "-ss", t, "-i", clip,
                        "-frames:v", "1", "-vf", "scale=1280:-2",
                        "-pix_fmt", "yuvj420p", fp], capture_output=True)
        if not os.path.exists(fp):
            print(f"{clip_n} t={t}: 抽帧失败")
            continue
        result, _ = ocr(fp)
        if result:
            for box in result:
                h = box[0][2][1] - box[0][0][1]
                print(f"{clip_n} t={t}: '{box[1][:40]}' conf={box[2]:.2f} 高={h:.0f}px y={box[0][0][1]:.0f}")
        else:
            print(f"{clip_n} t={t}: 无文字")
