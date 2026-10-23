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
    summary = summarize(rows)
    return {
        "range": {"from": rows[0]["date"], "to": rows[-1]["date"], "days": len(rows)},
        "rows": rows,
        "summary": summary,
        "episodes": episodes_table(rows, fetch_episode_links(), summary,
                                    fetch_videos(), fetch_factors()),
        "video": video_summary(rows),
        "weekday": weekday_dist(rows),
        "lag": lag_table(rows),
        "milestones": milestone_rows(
            summary["end_total_fans"], summary["baseline_avg"]["net"],
            summary["avg"]["net"], str(rows[-1]["date"])),
        "alerts": build_alerts(rows),
        "freshness": freshness(rows),
        "portrait": fetch_portrait(),
        "videos": fetch_videos(),
        "factors": factor_stats(rows, fetch_factors()),
    }
# ---------- 看板 v2 (1021): 视频侧 / 周内 / 领先性 / 里程碑 / 预警 / 集数挂钩 ----------

# 涨粉阶梯里程碑 (复盘框架 §4.3/§4.4): 1250 = 毕业线 (1000 有效粉 × 8 折)
MILESTONE_LADDER = (500, 800, 1000, 1250)

_WEEK_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def fetch_episode_links() -> list[dict[str, Any]]:
    """episode_links 全量 (投稿日↔集数挂钩, scripts/seed_episode_links.py 维护)."""
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = con.execute(
            "SELECT date, series, book_id, book_title, ep_index, ep_title, publish_time, note "
            "FROM episode_links ORDER BY date")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []  # 表未种
    finally:
        con.close()


def fetch_videos() -> list[dict[str, Any]]:
    """单视频真值 (douyin_videos, 作品列表导出; 次日12点更新昨日)."""
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = con.execute(
            "SELECT publish_date, publish_time, title, genre, views, completion_rate, "
            "sec5_rate, avg_watch_sec, likes, shares, comments, collects, profile_visits, fan_growth "
            "FROM douyin_videos ORDER BY publish_date")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []  # 表未导
    finally:
        con.close()


def episodes_table(rows: list[dict], links: list[dict],
                   summary: dict, videos: list[dict] | None = None,
                   factors: list[dict] | None = None) -> list[dict[str, Any]]:
    """挂钩表: 集数 × 单视频真值 (播放/官方完播/视频吸粉/收藏/主页) × 账号日 (净增/次日尾)."""
    by_date = {r["date"]: r for r in rows}
    vid_by_date = {v["publish_date"]: v for v in (videos or [])}
    order = [r["date"] for r in rows]
    anom = {a["date"] for a in summary["anomaly_days"]}
    out = []
    for lk in links:
        d = lk["date"]
        r = by_date.get(d)
        v = vid_by_date.get(d) or {}
        if r is None:
            # 1023 用户令: 集已发、账号日未导 (ep2 实发 10-22, 日数据隔日才有) —
            # 照常上屏, 日列空 (—), 视频真值优先; 不再整行蒸发让"集消失"
            out.append({
                "date": d, "series": lk["series"],
                "ep": (f"ep{lk['ep_index']}" if lk["ep_index"] else "非正片"),
                "ep_title": lk["ep_title"], "publish_time": lk["publish_time"], "note": lk["note"],
                "posts": None, "views": None,
                "fan_net": None, "fan_new": None, "revisit": None, "next_net": None,
                "is_anomaly": False, "factor": in_factor(factors or [], d),
                "v_title": v.get("title"), "v_genre": v.get("genre"),
                "v_views": v.get("views"), "v_completion": v.get("completion_rate"),
                "v_watch_sec": v.get("avg_watch_sec"),
                "v_fan_growth": v.get("fan_growth"), "v_collects": v.get("collects"),
                "v_profile_visits": v.get("profile_visits"),
            })
            continue
        i = order.index(d)
        nxt = rows[i + 1]["fan_net"] if i + 1 < len(rows) else None
        out.append({
            "date": d, "series": lk["series"],
            "ep": (f"ep{lk['ep_index']}" if lk["ep_index"] else "非正片"),
            "ep_title": lk["ep_title"], "publish_time": lk["publish_time"], "note": lk["note"],
            "posts": r.get("posts"), "views": r.get("views"),
            "fan_net": r["fan_net"], "fan_new": r["fan_new"], "revisit": r["revisit_fans"],
            "next_net": nxt, "is_anomaly": d in anom,
            "factor": in_factor(factors or [], d),
            # 单视频真值 (作品列表; 09-27 非正片不在列表 → None)
            "v_title": v.get("title"), "v_genre": v.get("genre"),
            "v_views": v.get("views"), "v_completion": v.get("completion_rate"),
            "v_watch_sec": v.get("avg_watch_sec"),
            "v_fan_growth": v.get("fan_growth"), "v_collects": v.get("collects"),
            "v_profile_visits": v.get("profile_visits"),
        })
    return out


def _wavg(rows: list[dict], field: str, weight: str = "views") -> float | None:
    tw = sum(r.get(weight) or 0 for r in rows)
    if not tw:
        return None
    return _r2(sum((r.get(field) or 0) * (r.get(weight) or 0) for r in rows) / tw)


def video_summary(rows: list[dict]) -> dict[str, Any]:
    """视频侧: 投稿日 vs 无投稿日 分组 + 全期漏斗与互动."""
    post = [r for r in rows if (r.get("posts") or 0) > 0]
    nopost = [r for r in rows if not (r.get("posts") or 0)]

    def grp(g: list[dict]) -> dict[str, Any]:
        views = sum(r.get("views") or 0 for r in g)
        return {
            "days": len(g),
            "views_total": views,
            "avg_views": _r2(views / len(g)) if g else 0,
            "fan_new_total": sum(r.get("fan_new") or 0 for r in g),
            "avg_fan_new": _r2(sum(r.get("fan_new") or 0 for r in g) / len(g)) if g else 0,
            "sec5_rate": _wavg(g, "sec5_rate"),
            "sec2_bounce": _wavg(g, "sec2_bounce"),
        }

    views_all = sum(r.get("views") or 0 for r in rows)
    new_all = sum(r.get("fan_new") or 0 for r in rows)
    return {
        "post_group": grp(post), "nopost_group": grp(nopost),
        "posts_total": sum(r.get("posts") or 0 for r in rows),
        "views_total": views_all,
        "likes": sum(r.get("likes") or 0 for r in rows),
        "shares": sum(r.get("shares") or 0 for r in rows),
        "comments": sum(r.get("comments") or 0 for r in rows),
        "interactions": sum(r.get("interactions") or 0 for r in rows),
        "sec5_rate": _wavg(rows, "sec5_rate"), "sec2_bounce": _wavg(rows, "sec2_bounce"),
        "avg_watch_sec": _wavg(rows, "avg_watch_sec"),
        "interaction_rate": _r2(sum(r.get("interactions") or 0 for r in rows) / views_all * 100) if views_all else None,
        # 吸粉效率 = 吸粉 / 播放万次
        "fan_efficiency": _r2(new_all / (views_all / 10000)) if views_all else None,
    }


def weekday_dist(rows: list[dict]) -> list[dict[str, Any]]:
    """周内分布: 天数 / 日均播放 / 日均净增 (xlsx 原表同口径)."""
    out = []
    for wd in range(1, 8):
        g = [r for r in rows if (r.get("weekday") or date.fromisoformat(str(r["date"])).isoweekday()) == wd]
        if not g:
            continue
        out.append({
            "weekday": wd, "label": _WEEK_LABELS[wd - 1], "days": len(g),
            "avg_views": _r2(sum(r.get("views") or 0 for r in g) / len(g)),
            "avg_net": _r2(sum(r.get("fan_net") or 0 for r in g) / len(g)),
            "avg_fan_new": _r2(sum(r.get("fan_new") or 0 for r in g) / len(g)),
        })
    return out


def lag_table(rows: list[dict], ks: range = range(-2, 3)) -> list[dict[str, Any]]:
    """回访领先性: r(回访[t], 净增[t+k]) — k>0 = 回访领先净增 k 天."""
    rev = [float(r.get("revisit_fans") or 0) for r in rows]
    net = [float(r["fan_net"] or 0) for r in rows]
    n = len(rows)
    out = []
    for k in ks:
        if k >= 0:
            xs, ys = rev[: n - k], net[k:]
        else:
            xs, ys = rev[-k:], net[: n + k]
        out.append({"lag": k, "r": _r2(pearson(xs, ys)), "n": len(xs)})
    return out


def milestone_rows(end_total: int, base_rate: float, full_rate: float,
                   last_date: str) -> list[dict[str, Any]]:
    """涨粉阶梯 ETA: 常态基线与全量双口径 (基线为主, 全量含爆款上偏)."""
    d0 = date.fromisoformat(last_date)
    out = []
    for target in MILESTONE_LADDER:
        remaining = target - end_total
        if remaining <= 0:
            out.append({"target": target, "done": True})
            continue
        row = {"target": target, "done": False, "remaining": remaining}
        for name, rate in (("base", base_rate), ("full", full_rate)):
            days = remaining / rate if rate > 0 else None
            row[f"eta_{name}_days"] = round(days) if days is not None else None
            row[f"eta_{name}_date"] = (
                (d0 + timedelta(days=round(days))).isoformat() if days is not None else None)
        out.append(row)
    return out


def build_alerts(rows: list[dict], today: date | None = None) -> list[dict[str, str]]:
    """预警条: 数据停更 / 连续失速 (干预机制触发线 ≥2 天净增≤0)."""
    today = today or date.today()
    alerts: list[dict[str, str]] = []
    last = date.fromisoformat(str(rows[-1]["date"]))
    stale = (today - last).days
    if stale > 3:
        alerts.append({"level": "warn", "kind": "stale",
                       "text": f"数据停在 {last:%m-%d}, 已 {stale} 天 — 该从抖音后台导出新表入库了"})
    streak = 0
    for r in reversed(rows):
        if (r.get("fan_net") or 0) <= 0:
            streak += 1
        else:
            break
    if streak >= 2:
        tail = [str(r["date"])[5:] for r in rows[-streak:]]
        alerts.append({"level": "bad", "kind": "stall",
                       "text": f"连续 {streak} 天净增≤0 ({('、'.join(tail))}) — 干预机制触发: 调整发布频率/选题"})
    return alerts


def freshness(rows: list[dict]) -> dict[str, Any]:
    return {
        "last_date": rows[-1]["date"],
        "last_import": max((r.get("updated_at") or "" for r in rows), default=None),
    }


def fetch_portrait() -> list[dict[str, Any]]:
    """粉丝画像快照 (fan_portrait, 截图→GLM 转录通道; 倒序最新在前)."""
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = con.execute(
            "SELECT snapshot_date, scope, dimension, rank, label, pct, note "
            "FROM fan_portrait ORDER BY snapshot_date DESC, scope, dimension, rank")
        cols = [d[0] for d in cur.description]
        raw = [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []  # 表未种
    finally:
        con.close()
    # 按 (date, scope) 聚合成快照, dimension 分组
    snaps: dict[tuple[str, str], dict[str, Any]] = {}
    for r in raw:
        key = (r["snapshot_date"], r["scope"])
        s = snaps.setdefault(key, {"date": r["snapshot_date"], "scope": r["scope"], "dims": {}})
        s["dims"].setdefault(r["dimension"], []).append(
            {"rank": r["rank"], "label": r["label"], "pct": r["pct"], "note": r["note"]})
    return list(snaps.values())

# ---------- 节假日/平台因素窗 (1021 用户令: 国庆流量萎缩非内容问题) ----------

def fetch_factors() -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = con.execute(
            "SELECT date_from, date_to, label, note, source FROM calendar_factors ORDER BY date_from")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []  # 表未种
    finally:
        con.close()


def in_factor(factors: list[dict], d: str) -> str | None:
    """日期命中的因素窗 label (可多重叠加时取首个)."""
    for f in factors:
        if f["date_from"] <= d <= f["date_to"]:
            return f["label"]
    return None


def factor_stats(rows: list[dict], factors: list[dict]) -> list[dict[str, Any]]:
    """每个因素窗: 底流(无投稿日播放)与发布日吸粉 窗内/窗外对比 — 平台因素 vs 内容因素的分离器."""
    out = []
    for f in factors:
        win = [r for r in rows if f["date_from"] <= str(r["date"]) <= f["date_to"]]
        lose = [r for r in rows if not (f["date_from"] <= str(r["date"]) <= f["date_to"])]
        win_np = [r for r in win if not (r.get("posts") or 0)]
        lose_np = [r for r in lose if not (r.get("posts") or 0)]
        win_p = [r for r in win if (r.get("posts") or 0)]
        lose_p = [r for r in lose if (r.get("posts") or 0)]

        def _avg(g, k):
            return _r2(sum(x.get(k) or 0 for x in g) / len(g)) if g else None

        np_in, np_out = _avg(win_np, "views"), _avg(lose_np, "views")
        out.append({
            **f,
            "days": len(win),
            "nopost_days": [{"date": r["date"], "net": r["fan_net"]} for r in win_np],
            "nopost_views_in": np_in, "nopost_views_out": np_out,
            "nopost_views_delta_pct": round((np_in - np_out) / np_out * 100, 1) if np_in and np_out else None,
            "post_fannew_in": _avg(win_p, "fan_new"), "post_fannew_out": _avg(lose_p, "fan_new"),
        })
    return out
