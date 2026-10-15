# -*- coding: utf-8 -*-
"""RealLifeLore 全频道下载 (278 部) — VPN 在线态, 只下载+切片, 不跑识别.
已下载的自动跳过; 新流水识别 (ocr/tag/register) 等 VPN 关后批跑."""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
PROXY = "http://127.0.0.1:9876"
STAGE = ROOT / "data" / "materials" / "youtube"
MAX = 278  # 全量

r = subprocess.run(
    [sys.executable, "-m", "yt_dlp", "--proxy", PROXY, "--flat-playlist",
     "--print", "%(id)s|%(duration)s|%(title)s",
     "https://www.youtube.com/@RealLifeLore/videos"],
    cwd=str(ROOT), capture_output=True, text=True, timeout=900)
picks = []
for line in (r.stdout or "").splitlines():
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 3 or not parts[0] or parts[0] == "NA":
        continue
    dur = float(parts[1]) if parts[1].replace(".", "").isdigit() else 0
    if dur and 60 <= dur <= 1500:
        picks.append((parts[0], dur, parts[2]))
print(f"频道正片: {len(picks)} 部, 目标 {min(MAX, len(picks))}", flush=True)

sys.path.insert(0, str(ROOT))
from sandbox.yt_ingest import cmd_split  # noqa: E402

done = skip = fail = 0
for vid, dur, title in picks[:MAX]:
    mf = STAGE / vid / "manifest.json"
    if mf.exists():
        skip += 1
        continue
    out = STAGE / f"yt_{vid}.mp4"
    print(f"[{done + skip + fail + 1}/{len(picks[:MAX])}] ↓ {title[:50]} ({dur:.0f}s)", flush=True)
    d = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--proxy", PROXY,
         "-f", "bv*[height<=1080]+ba/b[height<=1080]",
         "--merge-output-format", "mp4", "-o", str(out),
         f"https://www.youtube.com/watch?v={vid}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    if not out.exists():
        fail += 1
        print(f"    ✗ {(d.stderr or '')[-90:]}", flush=True)
        continue
    cmd_split(str(out), vid, entity="RealLifeLore", title=title[:80])
    done += 1
    print(f"    ✂ 累计 {done} (跳过{skip} 失败{fail})", flush=True)
print(f"\nRLR 全量下载批完成: 新下 {done} / 跳过 {skip} / 失败 {fail}", flush=True)
