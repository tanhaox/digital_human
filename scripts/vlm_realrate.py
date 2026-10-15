# -*- coding: utf-8 -*-
"""A/B 级 153 部 VLM 实拍率复筛 (2026-09-01): 干净窗内抽 10 帧 → llama is_real_footage.

输出 _final_review.txt: vid | 干净% | 实拍率% | 分钟 (按实拍率降序) — 两维终审榜单。
(干净占比是必要维度, 今日 _t78kfUKKDQ 68% 纯合成假阴性实锤第二维不可省)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

ROOT = Path(".").resolve()
STAGE = ROOT / "data/materials/youtube"
FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"

PROMPT = """你是视频素材审核员。看这一帧。只输出 JSON:
{"is_real_footage": true/false, "reason": "≤10字"}
is_real_footage=true 当且仅当: 摄像机实拍的真实世界画面
(真实人物/街景/城市/自然/建筑/工厂/交通/器物/真实事件现场)。
is_real_footage=false 当画面是任何自制/合成/非实拍产物:
地图/地形图/大面积国旗/图表/3D渲染/CG动画/游戏画面/AI生成感/
剪影+纯色渐变/纯色背景示意图/商品棚拍/截图/文字卡/黑白老胶片/历史档案。"""

# 首帧预热拉起 llama (含 GPU-VPN 守卫; VPN 已退)
from tools.vision import analyze  # noqa: E402
import glob  # noqa: E402

vids = []
for line in Path("_review_153.txt").read_text(encoding="utf-8").splitlines():
    v = line.split()[0] if line.split() else ""
    if not v:
        continue
    is_supp = "(补检)" in line
    if "--supplement" in sys.argv:
        if is_supp:
            vids.append(v)
    elif not is_supp:
        vids.append(v)
print(f"VLM 复筛 {len(vids)} 部 (每部干净窗内 10 帧, llama parallel)", flush=True)


def _pick_times(vid: str, n: int = 10) -> list[float]:
    """干净窗内均匀取 n 个时刻."""
    p = STAGE / vid / "timeline.json"
    try:
        tl = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    wins = [w for w in (tl.get("clean_windows") or []) if w[1] - w[0] > 1]
    if not wins:
        return []
    total = sum(we - ws for ws, we in wins)
    times, acc = [], 0.0
    for ws, we in wins:
        span = we - ws
        k = max(1, round(n * span / total))
        step = span / (k + 1)
        times += [round(ws + step * (i + 1), 1) for i in range(k)]
    return times[:n]


_print_lock = threading.Lock()
_done = [0]


def one(vid: str) -> dict:
    src = STAGE / f"yt_{vid}.mp4"
    times = _pick_times(vid)
    if not src.exists() or not times:
        return {"vid": vid, "real": None}
    real = total_f = 0
    for t in times:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            jpg = Path(tf.name)
        try:
            r = subprocess.run([FF, "-y", "-v", "error", "-ss", str(t), "-i", str(src),
                                "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "3", str(jpg)],
                               capture_output=True, timeout=30)
            if r.returncode != 0 or not jpg.exists():
                continue
            raw = analyze([str(jpg)], PROMPT)
            m = re.search(r"\{.*\}", str(raw), re.S)
            v = json.loads(m.group(0)) if m else {}
            total_f += 1
            if v.get("is_real_footage") is True:
                real += 1
        except Exception:
            pass
        finally:
            jpg.unlink(missing_ok=True)
    with _print_lock:
        _done[0] += 1
        print(f"  [{_done[0]}/{len(vids)}] {vid} 实拍 {real}/{total_f}", flush=True)
    return {"vid": vid, "real": real, "of": total_f}


results = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(one, v): v for v in vids}
    for f in as_completed(futs):
        results.append(f.result())

# 两维合并榜单
rows = []
for r in results:
    if r["real"] is None:
        continue
    tl = json.loads((STAGE / r["vid"] / "timeline.json").read_text(encoding="utf-8"))
    total = tl.get("total_sec") or 1
    dsl = tl.get("dirty_sec_list") or []
    clean_pct = round((1 - len(dsl) / total) * 100)
    real_pct = round(r["real"] / max(1, r["of"]) * 100)
    rows.append((r["vid"], clean_pct, real_pct, round(total / 60)))
rows.sort(key=lambda x: -x[2])
mode = "a" if "--supplement" in sys.argv else "w"
with open("_final_review.txt", mode, encoding="utf-8") as f:
    for v, c, p, m in rows:
        f.write(f"{v}  实拍率{p}%  干净{c}%  {m}分钟\n")
n60 = sum(1 for r in rows if r[2] >= 60)
n30 = sum(1 for r in rows if r[2] >= 30)
print(f"\n终审榜单 _final_review.txt: 实拍率≥60% {n60} 部 | ≥30% {n30} 部 | <30% {len(rows)-n30} 部", flush=True)
