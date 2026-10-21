# -*- coding: utf-8 -*-
"""抖音《数据表现》xlsx → data/analytics.db 导入 CLI (核心逻辑在 app/services/analytics_import.py).

用法:
    python scripts/import_douyin_metrics.py <数据表现_合并分析.xlsx>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.analytics_import import import_xlsx


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    xlsx = Path(sys.argv[1])
    if not xlsx.exists():
        raise SystemExit(f"[abort] 文件不存在: {xlsx}")
    r = import_xlsx(xlsx)
    print(f"[ok] 导入 {r['imported']} 行 (新 {r['new']} / 刷新 {r['refreshed']}), "
          f"回填总粉 {r['backfilled_total_fans']} 天")
    print(f"[ok] DB 现存 {r['db_days']} 行, 范围 {r['range']['from']} ~ {r['range']['to']}, "
          f"期末总粉 {r['end_total_fans']}")
    for w in r["warns"]:
        print(f"[warn] {w}")


if __name__ == "__main__":
    main()
