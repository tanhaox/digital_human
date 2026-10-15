# -*- coding: utf-8 -*-
"""geo 批次打标前抽稀: 讲话类 manifest 每 2 片取 1 (画面覆盖不变, 打标量减半).
国家宣传片类 (都市空镜) 不抽。被抽掉的 clip 文件从盘上删 (生成中间产物)。"""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
COUNTRIES = {"美国", "日本", "菲律宾", "伊朗", "俄罗斯", "乌克兰", "韩国", "欧盟"}

removed = kept = 0
for mf in sorted(glob.glob(str(ROOT / "data/materials/youtube/*/manifest.json"))):
    m = json.loads(Path(mf).read_text(encoding="utf-8"))
    ent = m.get("entity") or ""
    if ent in COUNTRIES or not m.get("clips"):
        continue
    keep, drop = [], []
    for i, c in enumerate(m["clips"]):
        (keep if i % 2 == 0 else drop).append(c)
    for c in drop:
        f = ROOT / c["file"]
        if f.exists():
            f.unlink()  # 生成中间产物, 直接删 (红线例外)
    m["clips"] = keep
    Path(mf).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    removed += len(drop)
    kept += len(keep)
    print(f"  {ent} {m['video_id']}: 留 {len(keep)} / 删 {len(drop)}")
print(f"抽稀完成: 留 {kept} 删 {removed}")
