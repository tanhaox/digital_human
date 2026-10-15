# -*- coding: utf-8 -*-
"""RealLifeLore 频道批量下载 (2026-08-31 用户荐矿: 无字幕+单话题解说).
VPN 已开 — 纯下载+切片(不跑 LLM), 新流水识别等 VPN 关."""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
PROXY = "http://127.0.0.1:9876"
STAGE = ROOT / "data" / "materials" / "youtube"

# 1. 频道平铺: 取 60s~20min 的正片
r = subprocess.run(
    [sys.executable, "-m", "yt_dlp", "--proxy", PROXY, "--flat-playlist",
     "--print", "%(id)s|%(duration)s|%(title)s",
     "https://www.youtube.com/@RealLifeLore/videos"],
    cwd=str(ROOT), capture_output=True, text=True, timeout=600)
picks = []
for line in (r.stdout or "").splitlines():
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 3 or not parts[0] or parts[0] == "NA":
        continue
    dur = float(parts[1]) if parts[1].replace(".", "").isdigit() else 0
    if dur and 60 <= dur <= 1200:
        picks.append((parts[0], dur, parts[2]))
print(f"频道正片(60s~20min): {len(picks)} 部, 取前 15", flush=True)

sys.path.insert(0, str(ROOT))
from sandbox.yt_ingest import cmd_split  # noqa: E402

done = 0
for vid, dur, title in picks[:15]:
    mf = STAGE / vid / "manifest.json"
    if mf.exists():
        print(f"  {vid} 已有, 跳过", flush=True)
        continue
    out = STAGE / f"yt_{vid}.mp4"
    print(f"  ↓ [{title[:44]}] {dur:.0f}s", flush=True)
    d = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--proxy", PROXY,
         "-f", "bv*[height<=1080]+ba/b[height<=1080]",
         "--merge-output-format", "mp4", "-o", str(out),
         f"https://www.youtube.com/watch?v={vid}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    if not out.exists():
        print(f"    ✗ {(d.stderr or '')[-100:]}", flush=True)
        continue
    cmd_split(str(out), vid, entity="RealLifeLore", title=title[:80])
    done += 1
    print(f"    ✂ 切片完成 ({done}/{min(15, len(picks))})", flush=True)
print(f"RealLifeLore 下载批完成: {done} 部", flush=True)
