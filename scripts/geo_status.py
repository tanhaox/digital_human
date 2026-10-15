# -*- coding: utf-8 -*-
"""geo 批次状态: 各实体已下载切片数 + 清理风景类国家片(都市向用户令)."""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
COUNTRIES = {"美国", "日本", "菲律宾", "伊朗", "俄罗斯", "乌克兰", "韩国", "欧盟"}

if len(sys.argv) > 1 and sys.argv[1] == "purge-country":
    from send2trash import send2trash
    purged = 0
    for mf in glob.glob(str(ROOT / "data/materials/youtube/*/manifest.json")):
        m = json.loads(Path(mf).read_text(encoding="utf-8"))
        if m.get("entity") in COUNTRIES:
            send2trash(str(Path(mf).parent))
            v = ROOT / "data/materials/youtube" / f"yt_{m['video_id']}.mp4"
            if v.exists():
                send2trash(str(v))
            purged += 1
    print(f"已清理国家风景片 {purged} 部 (都市向重下)")
else:
    ents = Counter()
    for mf in glob.glob(str(ROOT / "data/materials/youtube/*/manifest.json")):
        m = json.loads(Path(mf).read_text(encoding="utf-8"))
        ents[m.get("entity") or "?"] += len(m.get("clips", []))
    for k, v in sorted(ents.items()):
        print(f"  {k}: {v} 切片")
