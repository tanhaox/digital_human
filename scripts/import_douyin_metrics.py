# -*- coding: utf-8 -*-
"""抖音导出 xlsx → data/analytics.db 导入 CLI (核心逻辑在 app/services/analytics_import.py).

自动判别两类导出:
    《数据表现》(账号逐日 20 列) → douyin_daily
    《作品列表导出》(单视频真值 16 列) → douyin_videos

用法:
    python scripts/import_douyin_metrics.py <导出.xlsx>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.analytics_import import import_any


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    xlsx = Path(sys.argv[1])
    if not xlsx.exists():
        raise SystemExit(f"[abort] 文件不存在: {xlsx}")
    r = import_any(xlsx)
    print(f"[ok] 类型 {r['kind']} · 导入 {r['imported']} 行 (新 {r['new']} / 刷新 {r['refreshed']})")
    if r["kind"] == "daily":
        print(f"[ok] DB 现存 {r['db_days']} 行, 范围 {r['range']['from']} ~ {r['range']['to']}, "
              f"期末总粉 {r['end_total_fans']}, 回填总粉 {r['backfilled_total_fans']} 天")
    else:
        print(f"[ok] 作品现存 {r['db_videos']} 条, 发布范围 {r['range']['from']} ~ {r['range']['to']}")
    for w in r.get("warns", []):
        print(f"[warn] {w}")


if __name__ == "__main__":
    main()
