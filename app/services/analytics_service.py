"""数据看板 — data/analytics.db (douyin_daily) 读取 + 口径计算.

数据入库: scripts/import_douyin_metrics.py 从《数据表现_合并分析.xlsx》按日 upsert。
口径与 docs/analytics/数据记录/粉丝全量指标报告-20261021.html 逐项对齐:
- 异常日 = 粉丝净增 ≥ 3×中位数(全期逐日净增)
- 常态基线 = 剔除异常日后的日均
- 周桶按周一对齐 (首尾可为残周)
纯计算函数 (median/pearson/week_buckets/summarize) 不触 DB, 供 tests 直接测。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "analytics.db"

# douyin_daily 列序 (导入脚本与本模块共用)
DAILY_COLUMNS = (
    "date", "weekday", "posts", "views", "likes", "shares", "comments",
    "interactions", "interaction_rate", "sec5_rate", "sec2_bounce",
    "cover_ctr", "avg_watch_sec", "views_per_post", "total_fans",
    "fan_net", "fan_new", "fan_lost", "revisit_fans", "fan_efficiency",
    "source", "updated_at",
)


def fetch_daily() -> list[dict[str, Any]]:
    """全量逐日行, 按日期升序; 库不存在/无表 → []."""
    if not DB_PATH.exists():
        logger.info("[analytics] DB 不存在: %s", DB_PATH)
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        logger.exception("[analytics] DB 打开失败")
        return []
    try:
        cur = con.execute("SELECT * FROM douyin_daily ORDER BY date")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []  # 表未建 (导入前)
    finally:
        con.close()


# ---------- 纯计算 ----------

def _r2(x: float) -> float:
    return round(x, 2)


def pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson r; 样本<2 或零方差 → 0.0 (无相关可言)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def week_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """周一对齐周桶; label "MM-DD~MM-DD", 含 net 合计与 is_peak."""
    buckets: dict[date, dict[str, Any]] = {}
    order: list[date] = []
    for r in rows:
        d = date.fromisoformat(str(r["date"]))
        monday = d - timedelta(days=d.weekday())
        if monday not in buckets:
            # 首尾残周按数据实际起止标注 (报告口径: 首桶 "09-18~09-20" 而非周一 09-14)
            buckets[monday] = {"from": d, "to": d, "net": 0, "week_no": monday.isocalendar()[1]}
            order.append(monday)
        b = buckets[monday]
        b["to"] = max(b["to"], d)
        b["net"] += r["fan_net"]
    out = [
        {
            "week_no": b["week_no"],
            "label": f"{b['from']:%m-%d}~{b['to']:%m-%d}",
            "from": b["from"].isoformat(),
            "to": b["to"].isoformat(),
            "net": b["net"],
        }
        for b in (buckets[m] for m in order)
    ]
    peak = max(out, key=lambda w: w["net"]) if out else None
    if peak is not None:
        peak["is_peak"] = True
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """粉丝增长全摘要 — 报告页所有数字的唯一来源."""
    nets = [r["fan_net"] for r in rows]
    news = [r["fan_new"] for r in rows]
    lostr = [r["fan_lost"] for r in rows]
    revs = [r["revisit_fans"] for r in rows]
    days = len(rows)

    med = median(nets)
    thresh = 3 * med
    anomaly = [r for r in rows if r["fan_net"] >= thresh]
    base_rows = [r for r in rows if r["fan_net"] < thresh]
    n_anom, n_base = len(anomaly), len(base_rows)

    total_net, total_new, total_lost = sum(nets), sum(news), sum(lostr)
    anom_net, anom_new = sum(r["fan_net"] for r in anomaly), sum(r["fan_new"] for r in anomaly)
    peak = max(rows, key=lambda r: r["fan_net"]) if rows else None

    def _avg(vals: list[float]) -> float:
        return _r2(sum(vals) / len(vals)) if vals else 0.0

    def _delta(full: float, base: float) -> float | None:
        return round((base - full) / full * 100, 1) if full else None

    avg = {
        "net": _avg(nets), "new": _avg(news), "revisit": _avg(revs), "lost": _avg(lostr),
    }
    base_avg = {
        "net": _avg([r["fan_net"] for r in base_rows]),
        "new": _avg([r["fan_new"] for r in base_rows]),
        "revisit": _avg([r["revisit_fans"] for r in base_rows]),
        "lost": _avg([r["fan_lost"] for r in base_rows]),
    }

    return {
        "days": days,
        "from": rows[0]["date"], "to": rows[-1]["date"],
        "start_total_fans": rows[0]["total_fans"],   # 首日收盘 (报告口径: 从 1 → 411)
        "end_total_fans": rows[-1]["total_fans"],
        "total_net": total_net, "total_new": total_new, "total_lost": total_lost,
        "avg": avg, "baseline_days": n_base,
        "baseline_avg": base_avg,
        "baseline_total_net": sum(r["fan_net"] for r in base_rows),
        "churn_pct": _r2(total_lost / total_new * 100) if total_new else 0.0,
        "median_net": med, "anomaly_threshold": thresh,
        "anomaly_days": [
            {
                "date": r["date"], "net": r["fan_net"], "new": r["fan_new"],
                "lost": r["fan_lost"], "revisit": r["revisit_fans"],
                "is_peak": peak is not None and r["date"] == peak["date"],
            }
            for r in anomaly
        ],
        "anomaly_net": anom_net, "anomaly_new": anom_new,
        # 占比直接舍到 1 位 (前端同精度展示, 二次舍入会 43.55→43.5 偏离报告 43.6)
        "anomaly_net_share_pct": round(anom_net / total_net * 100, 1) if total_net else 0.0,
        "anomaly_new_share_pct": round(anom_new / total_new * 100, 1) if total_new else 0.0,
        "deltas": {  # 全量 → 常态基线变化% (cmp 表)
            "net": _delta(avg["net"], base_avg["net"]),
            "new": _delta(avg["new"], base_avg["new"]),
            "revisit": _delta(avg["revisit"], base_avg["revisit"]),
            "lost": _delta(avg["lost"], base_avg["lost"]),
        },
        "nonpos_days": [{"date": r["date"], "net": r["fan_net"]} for r in rows if r["fan_net"] <= 0],
        "r_net_revisit": _r2(pearson([float(v) for v in nets], [float(v) for v in revs])),
        "r_new_revisit": _r2(pearson([float(v) for v in news], [float(v) for v in revs])),
        "weeks": week_buckets(rows),
    }


def dashboard_payload() -> dict[str, Any]:
    """GET /api/analytics/daily 的载荷: 逐日行 + 摘要; 无数据时 rows=[] summary=None."""
    rows = fetch_daily()
    if not rows:
        return {"range": None, "rows": [], "summary": None}
    return {
        "range": {"from": rows[0]["date"], "to": rows[-1]["date"], "days": len(rows)},
        "rows": rows,
        "summary": summarize(rows),
    }
