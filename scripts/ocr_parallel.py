# -*- coding: utf-8 -*-
"""并行 OCR 快筛 (2026-08-31): manifest 按 index 分片 × N 进程, 各管各片无竞态.

瓶颈实测: OCR ~1.8s/帧 (1280px CPU), 单进程 704 片要 116min;
4 进程分片 → ~30min。ocr_clean 标记幂等续跑。
"""
import glob
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N = 4

# 收集还有未筛片段的 video_id
todo = []
for mf in sorted(glob.glob(str(ROOT / "data/materials/youtube/*/manifest.json"))):
    import json
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    if any("ocr_clean" not in c for c in m.get("clips", [])):
        todo.append(Path(mf).parent.name)
print(f"待筛 {len(todo)} 个视频, 分 {N} 片")

procs = []
for i in range(N):
    shard = todo[i::N]
    if not shard:
        continue
    # 用 -u 独立子进程跑各自的 video 列表
    code = (
        "import sys; sys.path.insert(0, '.');\n"
        "from sandbox.yt_ingest import ocr_screen;\n"
        f"for vid in {shard!r}: ocr_screen(vid)\n")
    p = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=str(ROOT))
    procs.append((i, len(shard), p))
    print(f"  片 {i}: {len(shard)} 个视频 (PID {p.pid})")

for i, n, p in procs:
    p.wait()
    print(f"片 {i} 完成 ({n} 视频, exit={p.returncode})")
print("全部并行快筛完成")
