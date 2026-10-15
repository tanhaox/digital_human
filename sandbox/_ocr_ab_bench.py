# -*- coding: utf-8 -*-
"""OCR 基准 v2: A现行 vs B管道@{1280,960,720,640} + 脏秒一致性, 结果存 json。"""
import json, subprocess, sys, tempfile, time
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
STAGE = Path("data/materials/youtube")

vid = sys.argv[1] if len(sys.argv) > 1 else "GlkCofOyxUA"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 150
video = str(STAGE / f"yt_{vid}.mp4")

out = subprocess.run([r"C:\Programs\ffmpeg\bin\ffprobe.exe", "-v", "error",
                      "-select_streams", "v:0", "-show_entries",
                      "stream=height", "-of", "csv=p=0", video],
                     capture_output=True, text=True)
SRC_H = int((out.stdout or "720").strip().split(",")[0])

from app.services.material_ingest_service import _new_ocr
ocr = _new_ocr()


def ocr_dirty(result):
    if not result:
        return False
    for r in result:
        try:
            if float(r[2]) >= 0.55 and (r[0][3][1] - r[0][1][1]) >= 9:
                return True
        except (TypeError, ValueError, IndexError):
            continue
    return False


def warmup():
    import os
    fd, name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp = Path(name)
    subprocess.run([FF, "-y", "-v", "error", "-ss", "1.0", "-i", video,
                    "-frames:v", "1", "-vf", "scale=1280:-2",
                    "-pix_fmt", "yuvj420p", str(tmp)], capture_output=True)
    ocr(str(tmp))
    tmp.unlink(missing_ok=True)


def method_a():
    tmp = Path(tempfile.mkdtemp(prefix="benchA_"))
    dirty = {}
    t0 = time.perf_counter()
    t = 0.5
    while t < 0.5 + N:
        fp = tmp / f"{int(t*2)}.jpg"
        subprocess.run([FF, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video,
                        "-frames:v", "1", "-vf", "scale=1280:-2",
                        "-pix_fmt", "yuvj420p", str(fp)], capture_output=True)
        if fp.exists():
            res, _ = ocr(str(fp))
            dirty[int(t)] = ocr_dirty(res)
            fp.unlink(missing_ok=True)
        t += 1.0
    return time.perf_counter() - t0, dirty


def method_b(scale):
    probe = subprocess.run([FF, "-v", "error", "-i", video, "-vf",
                            f"fps=1,scale={scale}:-2", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                           capture_output=True)
    fb = len(probe.stdout)
    H = fb // (scale * 3)
    p = subprocess.Popen([FF, "-v", "error", "-i", video, "-vf",
                          f"fps=1,scale={scale}:-2", "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "pipe:1"], stdout=subprocess.PIPE)
    dirty = {}
    t0 = time.perf_counter()
    for k in range(N):
        raw = p.stdout.read(fb)
        if len(raw) < fb:
            break
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(H, scale, 3)
        res, _ = ocr(frame)
        dirty[k] = ocr_dirty(res)
    el = time.perf_counter() - t0
    p.kill()
    return el, dirty


def agree(sa, sb, tol=1):
    hit = sum(1 for a in sa if any(abs(a - b) <= tol for b in sb))
    return hit


warmup()
print(f"== {vid} 源{SRC_H}p, N={N} ==")
res = {"vid": vid, "n": N, "src_h": SRC_H}

el_a, d_a = method_a()
sa = {k for k, v in d_a.items() if v}
res["A"] = {"ms": el_a / N * 1000, "dirty": len(sa)}
print(f"A 现行1280: {el_a/N*1000:.0f}ms/帧 脏{len(sa)}")

for scale in (1280, 960, 720, 640):
    el, d = method_b(scale)
    sb = {k for k, v in d.items() if v}
    hit = agree(sa, sb)
    recall = hit / len(sa) if sa else 1.0
    extra = len(sb - {a for a in sa if any(abs(a - b) <= 1 for b in sb)})
    res[f"B{scale}"] = {"ms": el / N * 1000, "dirty": len(sb),
                        "recall_1s": round(recall, 4), "extra": extra}
    print(f"B 管道{scale}: {el/N*1000:.0f}ms/帧 脏{len(sb)} "
          f"±1s召回{recall*100:.1f}% 多报{extra}")

Path("sandbox/_ocr_bench_result.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("已存 sandbox/_ocr_bench_result.json")
