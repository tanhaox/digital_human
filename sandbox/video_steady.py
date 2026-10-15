# -*- coding: utf-8 -*-
"""边缘配准稳像 — 免疫光效呼吸的视频位置抖动消除
原理: 光效呼吸改变亮度但不动边缘 → 用边缘图做相位相关, 每帧对齐到第一帧, 裁掉边缘, 重编码.
用法: python video_steady.py <in.mp4> <out.mp4> [zoom_end=1.12]
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"


def edges(gray):
    im = Image.fromarray(gray.astype(np.uint8))
    im = im.filter(ImageFilter.FIND_EDGES)
    return np.asarray(im, dtype=np.float32)


def shift_vs_ref(frame, ref_edges, ref_shape):
    g = np.asarray(frame.convert("L"), dtype=np.float32)
    e = edges(g)
    corr = np.fft.irfft2(np.fft.rfft2(e) * np.conj(np.fft.rfft2(ref_edges)), s=ref_shape)
    pk = np.unravel_index(np.argmax(corr), corr.shape)
    dy = pk[0] - ref_shape[0] if pk[0] > ref_shape[0] // 2 else pk[0]
    dx = pk[1] if pk[1] < ref_shape[1] // 2 else pk[1] - ref_shape[1]
    return dx, dy


def main():
    src, dst = sys.argv[1], sys.argv[2]
    zoom_end = float(sys.argv[3]) if len(sys.argv) > 3 else 1.12
    with tempfile.TemporaryDirectory() as td:
        n = 81
        for i in range(n):
            subprocess.run([FF, "-y", "-ss", str(5.0 * i / (n - 1)), "-i", src,
                            "-frames:v", "1", os.path.join(td, f"{i:03d}.png")],
                           capture_output=True)
        frames = [Image.open(os.path.join(td, f"{i:03d}.png")) for i in range(n)]
        ref_gray = np.asarray(frames[0].convert("L"), dtype=np.float32)
        ref_edges = edges(ref_gray)
        shape = ref_gray.shape
        shifts = [shift_vs_ref(f, ref_edges, shape) for f in frames]
        s = np.array(shifts)
        print(f"帧偏移 vs 首帧: dx∈[{s[:,0].min()},{s[:,0].max()}] dy∈[{s[:,1].min()},{s[:,1].max()}]")
        # 对齐+统一裁剪(留 zoom 余量)
        m = int(np.abs(s).max()) + 4
        aligned_dir = os.path.join(td, "al")
        os.makedirs(aligned_dir, exist_ok=True)
        for i, f in enumerate(frames):
            dx, dy = int(s[i, 0]), int(s[i, 1])
            base = np.asarray(f)
            canvas = np.zeros_like(base)
            H, W = shape
            src_y = slice(max(0, dy), H + min(0, dy))
            src_x = slice(max(0, dx), W + min(0, dx))
            dst_y = slice(max(0, -dy), H + min(0, -dy))
            dst_x = slice(max(0, -dx), W + min(0, -dx))
            canvas[dst_y, dst_x] = base[src_y, src_x]
            Image.fromarray(canvas[m:H - m, m:W - m]).save(os.path.join(aligned_dir, f"{i:03d}.png"))
        raw = os.path.join(td, "al.mp4")
        subprocess.run([FF, "-y", "-framerate", "16", "-i", os.path.join(aligned_dir, "%03d.png"),
                        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", raw],
                       capture_output=True)
        # 数学变焦
        subprocess.run([FF, "-y", "-i", raw,
                        "-vf", f"scale=2560:-2,zoompan=z='1+{zoom_end-1:.3f}*on/81':"
                               f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1280x704:fps=16",
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", dst],
                       capture_output=True)
    print("✅", dst)


if __name__ == "__main__":
    main()
