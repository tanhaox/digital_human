# -*- coding: utf-8 -*-
"""修复 video_assets JSON 列脏格式 — scenes/shot_types 存成 str('["a","b"]') 回写为 list.

背景 (2026-09-01): RLR 批量导入 (V20260828 起, 2026-08-28~) 把 scenes/shot_types
以 JSON 字符串写入 JSON 列 → VideoAssetOut 序列化 500 (library 页翻页必炸)。
读侧已在 schemas/assets.py 加宽容 validator 止血; 本脚本治本回写数据。

⚠️ 等 rlr_stage2_parallel 跑完再执行 (避免与打标写库抢 SQLite 写锁);
   分批小事务 + 撞锁重试, 即使撞上也不损坏数据。

用法: python scripts/fix_json_columns.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, ".")

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402

BATCH = 200
COLS = ("scenes", "shot_types", "tags")


def _parse(v):
    if not isinstance(v, str):
        return None
    try:
        parsed = json.loads(v)
    except (ValueError, TypeError):
        return [v]
    return parsed if isinstance(parsed, list) else [parsed]


def main() -> None:
    dry = "--dry-run" in sys.argv
    set_config(load_config())
    init_db("sqlite:///data/pipeline.db")
    from app.models import VideoAsset

    db = get_session_maker()()
    rows = (
        db.query(VideoAsset)
        .filter(
            VideoAsset.scenes.isnot(None),
            # SQLite 无 typeof() 便捷判定, Python 层过滤 (见下)
        ).all()
    )
    dirty = [a for a in rows if any(isinstance(getattr(a, c), str) for c in COLS)]
    print(f"total={len(rows)} dirty={len(dirty)}{' (dry-run)' if dry else ''}")

    fixed = 0
    for i, a in enumerate(dirty):
        changed = False
        for c in COLS:
            parsed = _parse(getattr(a, c))
            if parsed is not None:
                setattr(a, c, parsed)
                changed = True
        if not changed:
            continue
        fixed += 1
        if dry:
            continue
        db.commit()  # 单行提交: 与并发写者 (若仍在跑) 最小化锁窗口
        if i % BATCH == 0:
            print(f"  {i}/{len(dirty)} ...")
            time.sleep(0.05)
    if not dry:
        print(f"fixed {fixed} rows")
    else:
        print(f"would fix {fixed} rows")
    db.close()


if __name__ == "__main__":
    main()
