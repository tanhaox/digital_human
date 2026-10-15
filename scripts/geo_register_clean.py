# -*- coding: utf-8 -*-
"""geo 批次专用注册 (2026-08-28 用户单): 只入 has_burned_text=False 的干净片段。

用户硬指标: 唯一要求就是没有字幕干净的素材; 讲话的无需讲话内容。
带烧录字幕的片段不入库 (manifest 里保留记录, 需要时可放宽)。
"""
import json
import glob
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402
from collections import Counter  # noqa: E402

# 只处理这些实体 (geo 批次); 之前的科技批次不动
GEO_ENTITIES = {"美国", "日本", "菲律宾", "伊朗", "俄罗斯", "乌克兰", "韩国", "欧盟",
                "特朗普", "高市早苗", "普京", "泽连斯基", "马科斯", "李在明",
                "佩泽希齐扬", "冯德莱恩", "G7峰会",
                # VIP 轮 (2026-08-29): 过去一年高频人物
                "小泉进次郎", "石破茂", "拉夫罗夫", "梅德韦杰夫", "尹锡悦",
                "万斯", "鲁比奥", "马斯克", "哈梅内伊", "阿拉格齐",
                "莎拉杜特尔特", "马克龙", "默茨", "卡拉斯", "吕特"}
# 量控 (2026-08-28 用户口径修正): 多了入库没事 — 逐片段独立打标天然互异,
# 冷却调度管轮换; 仅保留 无字幕 + ≥4s 过滤, 不设每实体上限。

init_db("sqlite:///data/pipeline.db")
n = 0
stats = Counter()
# Counter 的 f"入:{ent}" 默认 0 → 量控直接可用
for _ent in GEO_ENTITIES:
    stats[f"入:{_ent}"] = 0
with get_session_maker()() as db:
    existing = {a.file_path for a in db.query(VideoAsset).all()}
    today = "V20260828-"
    nums = [int(a.asset_no[-4:]) for a in db.query(VideoAsset)
            .filter(VideoAsset.asset_no.like(today + "%")).all()
            if a.asset_no[-4:].isdigit()]
    seq = max(nums, default=0)
    for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
        m = json.loads(open(mf, encoding="utf-8").read())
        ent = m.get("entity") or ""
        if ent not in GEO_ENTITIES:
            continue
        for c in m.get("clips", []):
            t = c.get("tags") or {}
            if not t:
                continue  # 未打标
            stats["seen"] += 1
            if t.get("has_burned_text"):
                stats["burned_skip"] += 1
                continue
            dur = c["end"] - c["start"]
            if dur < 4.0:
                stats["short_skip"] += 1
                continue  # 太碎的不要
            fp = str((Path(".").resolve() / c["file"]).resolve())
            if fp in existing:
                continue
            seq += 1
            kw = [k for k in (t.get("keywords_en") or []) if k]
            tags = list(dict.fromkeys(kw + [ent, "no_subtitle"]))
            db.add(VideoAsset(asset_no=f"{today}{seq:04d}", source="youtube",
                              file_path=fp, orientation="landscape",
                              width=1920, height=1080,
                              duration_sec=round(c["end"] - c["start"], 2),
                              description_zh=t.get("desc_zh") or "",
                              description_en=", ".join(kw),
                              raw_query=f"youtube:{m.get('title','')[:80]}",
                              tags=tags, source_type="footage", location="foreign"))
            n += 1
            stats[f"入:{ent}"] += 1
    db.commit()
    lib = db.query(VideoAsset).count()
print(f"干净片段入库 {n} 条 (跳过带字幕 {stats['burned_skip']}/{stats['seen']}) | 库容 {lib}")
for k, v in stats.items():
    if k.startswith("入:"):
        print(f"  {k[2:]}: {v}")
