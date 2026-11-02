"""《数据表现》xlsx → data/analytics.db 导入核心 (CLI 与上传 API 共用).

scripts/import_douyin_metrics.py 是其 CLI 壳; POST /api/analytics/import 走 import_xlsx().
口径: 按日 upsert (重叠日以最新导出为准), 总粉缺失按 上日+净增 回填,
行级校验 净增=吸粉−脱粉 (不一致仅告警, 导出可能修历史)。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "analytics.db"

HEADER_MAP = {
    "日期": "date",
    "星期": "weekday",
    "投稿量": "posts",
    "总播放量": "views",
    "总点赞量": "likes",
    "总分享量": "shares",
    "总评论量": "comments",
    "互动量": "interactions",
    "互动率": "interaction_rate",
    "5秒完播率": "sec5_rate",
    "2秒跳出率": "sec2_bounce",
    "封面点击率": "cover_ctr",
    # "平均播放时长" = 1026 改版《短视频全量指标》列名 (值带 s 后缀), 老表叫 "平均播放时长(s)"
    "平均播放时长(s)": "avg_watch_sec",
    "平均播放时长": "avg_watch_sec",
    "播放/投稿": "views_per_post",
    "总粉丝量": "total_fans",
    "粉丝净增": "fan_net",
    "吸粉量": "fan_new",
    "脱粉量": "fan_lost",
    "回访粉丝量": "revisit_fans",
    "吸粉效率(粉/万播放)": "fan_efficiency",
}

# 1026 抖音改版: 《数据表现_短视频全量指标》仅 10 列 (无粉丝列, 率为 "22.02%" 字符串)。
# 只硬性要求三根柱, 其余列按表头在则读; 互动量/互动率/播放比缺失时由已有列派生 (互动量=赞+评+转, 与老表口径一致)。
REQUIRED_DAILY = ("日期", "投稿量", "总播放量")
RATE_COLS = ("interaction_rate", "sec5_rate", "sec2_bounce", "cover_ctr", "fan_efficiency")
INT_COLS = ("posts", "views", "likes", "shares", "comments", "interactions",
            "total_fans", "fan_net", "fan_new", "fan_lost", "revisit_fans")

DDL = """
CREATE TABLE IF NOT EXISTS douyin_daily (
  date TEXT PRIMARY KEY,
  weekday INTEGER,
  posts INTEGER, views INTEGER,
  likes INTEGER, shares INTEGER, comments INTEGER, interactions INTEGER,
  interaction_rate REAL, sec5_rate REAL, sec2_bounce REAL, cover_ctr REAL,
  avg_watch_sec REAL, views_per_post REAL,
  total_fans INTEGER, fan_net INTEGER, fan_new INTEGER, fan_lost INTEGER,
  revisit_fans INTEGER, fan_efficiency REAL,
  source TEXT, updated_at TEXT
)
"""

COLUMNS = list(HEADER_MAP.values()) + ["source", "updated_at"]

# 沉淀总表 (1103 用户令: 两张导出按日期合并成一张表; 同日口径澄清亦 1103 用户令) —
# 逐日侧 = 《数据表现》当日**全账号**口径 (当天所有在播作品的长尾+新发, 总和进这一天);
# 视频侧 = 《作品列表》该日发布作品的**终身累计**口径 (自发到导出时点)。
# 两口径理论近似但视角不同, 不可同行直接相减比较 (1103 用户令: "理论上数据表现≈作品列表, 但可能不完全一致")。
# 逐日侧来自 30 天滚动窗 (重叠 COALESCE 合并, 旧段沉淀), 视频侧全历史按发布日折叠;
# 同日多视频: 计数列求和 / 率列按播放加权 / 标题'｜'拼接 / v_count 计数。
UNIFIED_DDL = """
CREATE TABLE IF NOT EXISTS douyin_unified (
  date TEXT PRIMARY KEY,
  weekday INTEGER,
  posts INTEGER, views INTEGER, likes INTEGER, shares INTEGER, comments INTEGER,
  interactions INTEGER, interaction_rate REAL, sec5_rate REAL, sec2_bounce REAL,
  cover_ctr REAL, avg_watch_sec REAL, views_per_post REAL,
  total_fans INTEGER, fan_net INTEGER, fan_new INTEGER, fan_lost INTEGER,
  revisit_fans INTEGER, fan_efficiency REAL,
  v_count INTEGER, v_titles TEXT, v_genre TEXT,
  v_views INTEGER, v_completion_rate REAL, v_sec5_rate REAL, v_avg_watch_sec REAL,
  v_likes INTEGER, v_shares INTEGER, v_comments INTEGER, v_collects INTEGER,
  v_profile_visits INTEGER, v_fan_growth INTEGER,
  source TEXT, updated_at TEXT
)
"""
_UNIFIED_DAILY_COLS = (
    "weekday", "posts", "views", "likes", "shares", "comments", "interactions",
    "interaction_rate", "sec5_rate", "sec2_bounce", "cover_ctr", "avg_watch_sec",
    "views_per_post", "total_fans", "fan_net", "fan_new", "fan_lost",
    "revisit_fans", "fan_efficiency",
)
_UNIFIED_COUNT_COLS = ("v_views", "v_likes", "v_shares", "v_comments",
                       "v_collects", "v_profile_visits", "v_fan_growth")
_UNIFIED_RATE_COLS = ("v_completion_rate", "v_sec5_rate", "v_avg_watch_sec")

# 《作品列表导出》(单条作品真值: 官方完播/收藏/主页访问/单视频吸粉; 次日12点更新)
VIDEO_HEADER_MAP = {
    "作品名称": "title",
    "发布时间": "publish_time",
    "体裁": "genre",
    "审核状态": "audit_status",
    "播放量": "views",
    "完播率": "completion_rate",
    "5s完播率": "sec5_rate",
    "封面点击率": "cover_ctr",
    "2s跳出率": "sec2_bounce",
    "平均播放时长": "avg_watch_sec",
    "点赞量": "likes",
    "分享量": "shares",
    "评论量": "comments",
    "收藏量": "collects",
    "主页访问量": "profile_visits",
    "粉丝增量": "fan_growth",
}

VIDEO_DDL = """
CREATE TABLE IF NOT EXISTS douyin_videos (
  publish_time TEXT PRIMARY KEY,
  publish_date TEXT,
  title TEXT, genre TEXT, audit_status TEXT,
  views INTEGER, completion_rate REAL, sec5_rate REAL, cover_ctr REAL,
  sec2_bounce REAL, avg_watch_sec REAL,
  likes INTEGER, shares INTEGER, comments INTEGER, collects INTEGER,
  profile_visits INTEGER, fan_growth INTEGER,
  source TEXT, updated_at TEXT
)
"""


def _num(v):
    """作品导出数值列常为字符串 ('2847'), 统一转数值; 空返 None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    return float(str(v).replace(",", ""))


def _cell(v, col: str):
    """逐日单元格 → 库值: 率列剥 '%'/100, 时长剥 's' 后缀, 整数列剥逗号; 空返 None."""
    if v is None or v == "":
        return None
    if col in RATE_COLS:
        if isinstance(v, str):
            s = v.strip()
            return float(s[:-1]) / 100 if s.endswith("%") else float(s)
        return float(v)
    if col == "avg_watch_sec":
        return float(v.strip().rstrip("s")) if isinstance(v, str) else float(v)
    if col in INT_COLS:
        return int(_num(v))
    return v


def _norm_date(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    return str(v).strip()[:10]


def load_rows(xlsx: Path, source_name: str) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        return _iter_rows(wb, source_name)
    finally:
        wb.close()  # read_only 模式持文件句柄, 不关则 Windows 上 unlink 报 WinError 32


def _iter_rows(wb, source_name: str) -> list[dict[str, Any]]:
    ws = wb["合并明细"] if "合并明细" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header = {h: i for i, h in enumerate(rows[0])}
    missing = [h for h in REQUIRED_DAILY if h not in header]
    if missing:
        raise ValueError(f"xlsx 缺列: {missing}")
    colmap = {HEADER_MAP[h]: header[h] for h in header if h in HEADER_MAP}
    out = []
    now = datetime.now().isoformat(timespec="seconds")
    for line in rows[1:]:
        if line[header["日期"]] is None:
            continue
        r = {c: _cell(line[i], c) for c, i in colmap.items()}
        d = date.fromisoformat(_norm_date(r["date"]))
        r["date"] = d.isoformat()
        r["weekday"] = d.isoweekday()
        if r.get("interactions") is None and None not in (r.get("likes"), r.get("shares"), r.get("comments")):
            r["interactions"] = r["likes"] + r["shares"] + r["comments"]
        if r.get("interaction_rate") is None and r.get("interactions") is not None and r.get("views"):
            r["interaction_rate"] = r["interactions"] / r["views"]
        if r.get("views_per_post") is None and r.get("views") is not None and r.get("posts"):
            r["views_per_post"] = r["views"] / r["posts"]
        r["source"] = source_name
        r["updated_at"] = now
        out.append(r)
    return out


def rebuild_unified(con: sqlite3.Connection) -> int:
    """重建 douyin_unified 沉淀总表 (douyin_daily + douyin_videos 按日期合并); 返回行数.

    幂等全量重建 (数据量=天数级, 无增量复杂度); 两次导入后各调一次。"""
    con.execute(UNIFIED_DDL)
    con.execute(VIDEO_DDL)  # 仅 daily 导入过的新库尚无 videos 表, 先确保再查
    cur = con.execute(f"SELECT date, {','.join(_UNIFIED_DAILY_COLS)} FROM douyin_daily ORDER BY date")
    daily = {r[0]: dict(zip(_UNIFIED_DAILY_COLS, r[1:])) for r in cur.fetchall()}
    vids: dict[str, list[dict]] = {}
    for r in con.execute(
        "SELECT publish_date, title, genre, views, completion_rate, sec5_rate, avg_watch_sec, "
        "likes, shares, comments, collects, profile_visits, fan_growth FROM douyin_videos ORDER BY publish_time"
    ):
        vids.setdefault(r[0], []).append(dict(zip(
            ("title", "genre", "views", "completion_rate", "sec5_rate", "avg_watch_sec",
             "likes", "shares", "comments", "collects", "profile_visits", "fan_growth"), r[1:])))
    now = datetime.now().isoformat(timespec="seconds")
    cols = ("date",) + _UNIFIED_DAILY_COLS + ("v_count", "v_titles", "v_genre") + \
        _UNIFIED_COUNT_COLS + _UNIFIED_RATE_COLS + ("source", "updated_at")
    placeholders = ",".join("?" * len(cols))
    n = 0
    for d in sorted(set(daily) | set(vids)):
        day = daily.get(d, {})
        g = vids.get(d, [])
        row = {c: day.get(c) for c in _UNIFIED_DAILY_COLS}
        if g:
            row["v_count"] = len(g)
            row["v_titles"] = "｜".join(v["title"].split(" ")[0] for v in g)
            row["v_genre"] = next((v["genre"] for v in g if v["genre"]), None)
            for c, k in zip(_UNIFIED_COUNT_COLS, ("views", "likes", "shares", "comments",
                                                  "collects", "profile_visits", "fan_growth")):
                row[c] = sum(v[k] or 0 for v in g)
            tv = sum(v["views"] or 0 for v in g)
            for c, k in zip(_UNIFIED_RATE_COLS, ("completion_rate", "sec5_rate", "avg_watch_sec")):
                vals = [(v[k], v["views"]) for v in g if v[k] is not None]
                row[c] = (sum(x * w for x, w in vals) / sum(w for _, w in vals)) if vals and tv else None
        else:
            row["v_count"] = 0
        con.execute(
            f"INSERT OR REPLACE INTO douyin_unified ({','.join(cols)}) VALUES ({placeholders})",
            [d] + [row.get(c) for c in _UNIFIED_DAILY_COLS] +
            [row.get("v_count"), row.get("v_titles"), row.get("v_genre")] +
            [row.get(c) for c in _UNIFIED_COUNT_COLS] + [row.get(c) for c in _UNIFIED_RATE_COLS] +
            [f"daily+videos@{now}", now],
        )
        n += 1
    con.commit()
    return n


def import_xlsx(xlsx: Path, source_name: str | None = None) -> dict[str, Any]:
    """导入一份导出 xlsx, 返回报告 dict (不抛业务异常外的意外)."""
    rows = load_rows(xlsx, source_name or xlsx.name)
    if not rows:
        raise ValueError("xlsx 无数据行 (合并明细 sheet 为空)")

    warns: list[str] = []
    if all(r.get("total_fans") is None for r in rows):
        warns.append("本次导出无粉丝列 (1026 改版《短视频全量指标》): fan_* / total_fans / revisit 留空, "
                     "重叠日旧值保留 — 粉丝侧请另导《粉丝数据》表")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(DDL)
        existing = {r[0] for r in con.execute("SELECT date FROM douyin_daily")}
        new_days = [r["date"] for r in rows if r["date"] not in existing]
        placeholders = ",".join("?" * len(COLUMNS))
        # 重叠日: 新导出缺的列 (如新版无粉丝列) 保留旧值, 在则覆盖 (COALESCE(excluded.x, x))
        update_sets = ",".join(
            f"source=excluded.source" if c == "source" else
            f"updated_at=excluded.updated_at" if c == "updated_at" else
            f"{c}=COALESCE(excluded.{c}, {c})"
            for c in COLUMNS if c != "date"
        )
        for r in rows:
            if r.get("fan_net") is not None and None not in (r.get("fan_new"), r.get("fan_lost")):
                if r["fan_net"] != r["fan_new"] - r["fan_lost"]:
                    warns.append(f"{r['date']} 净增{r['fan_net']} ≠ 吸粉{r['fan_new']}−脱粉{r['fan_lost']}")
            con.execute(
                f"INSERT INTO douyin_daily ({','.join(COLUMNS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(date) DO UPDATE SET {update_sets}",
                [r.get(c) for c in COLUMNS],
            )
        con.commit()

        backfilled = 0
        data = con.execute("SELECT date, total_fans, fan_net FROM douyin_daily ORDER BY date").fetchall()
        prev_total = None
        for d, total, net in data:
            if total is None and prev_total is not None and net is not None:
                con.execute("UPDATE douyin_daily SET total_fans = ? WHERE date = ?", (prev_total + net, d))
                backfilled += 1
            elif total is not None:
                prev_total = total
        con.commit()

        n_days, d_min, d_max = con.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM douyin_daily").fetchone()
        end_row = con.execute(
            "SELECT total_fans FROM douyin_daily WHERE total_fans IS NOT NULL "
            "ORDER BY date DESC LIMIT 1").fetchone()
        end_total = end_row[0] if end_row else None
        unified_rows = rebuild_unified(con)
    finally:
        con.close()

    report = {
        "imported": len(rows), "new": len(new_days), "refreshed": len(rows) - len(new_days),
        "backfilled_total_fans": backfilled, "db_days": n_days,
        "unified_rows": unified_rows,
        "range": {"from": d_min, "to": d_max}, "end_total_fans": end_total, "warns": warns,
    }
    return report


def load_video_rows(xlsx: Path, source_name: str) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        header = {h: i for i, h in enumerate(rows[0])}
        missing = [h for h in VIDEO_HEADER_MAP if h not in header]
        if missing:
            raise ValueError(f"作品导出缺列: {missing}")
        colmap = {VIDEO_HEADER_MAP[h]: header[h] for h in header if h in VIDEO_HEADER_MAP}
        now = datetime.now().isoformat(timespec="seconds")
        out = []
        for line in rows[1:]:
            if line[header["发布时间"]] is None:
                continue
            r = {c: line[i] for c, i in colmap.items()}
            pt = r["publish_time"]
            if isinstance(pt, datetime):
                pt_str = pt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                pt_str = str(pt).strip()
            r["publish_time"] = pt_str
            r["publish_date"] = pt_str[:10]
            for c in ("views", "likes", "shares", "comments", "collects", "profile_visits", "fan_growth"):
                r[c] = int(_num(r[c]))
            for c in ("completion_rate", "sec5_rate", "cover_ctr", "sec2_bounce", "avg_watch_sec"):
                r[c] = _num(r[c])
            r["source"] = source_name
            r["updated_at"] = now
            out.append(r)
        return out
    finally:
        wb.close()


def import_videos_xlsx(xlsx: Path, source_name: str | None = None) -> dict[str, Any]:
    """导入作品列表导出 (单视频真值, 按 publish_time upsert)."""
    rows = load_video_rows(xlsx, source_name or xlsx.name)
    if not rows:
        raise ValueError("作品导出无数据行")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(VIDEO_DDL)
        existing = {r[0] for r in con.execute("SELECT publish_time FROM douyin_videos")}
        new_n = sum(1 for r in rows if r["publish_time"] not in existing)
        cols = list(VIDEO_HEADER_MAP.values()) + ["publish_date", "source", "updated_at"]
        placeholders = ",".join("?" * len(cols))
        for r in rows:
            con.execute(
                f"INSERT OR REPLACE INTO douyin_videos ({','.join(cols)}) VALUES ({placeholders})",
                [r.get(c) for c in cols],
            )
        con.commit()
        n, d_min, d_max = con.execute(
            "SELECT COUNT(*), MIN(publish_date), MAX(publish_date) FROM douyin_videos").fetchone()
        unified_rows = rebuild_unified(con)
    finally:
        con.close()
    return {
        "kind": "videos", "imported": len(rows), "new": new_n, "refreshed": len(rows) - new_n,
        "db_videos": n, "unified_rows": unified_rows,
        "range": {"from": d_min, "to": d_max},
        "note": "单视频真值 (官方完播/收藏/主页访问/吸粉), 次日12点更新昨日; 导入后已重建 douyin_unified 沉淀总表",
    }


def detect_kind(xlsx: Path) -> str:
    """按表头判别导出类型: daily(数据表现) / videos(作品列表)."""
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        first = next(ws.iter_rows(values_only=True), None) or ()
        header = {str(h) for h in first if h}
        wb.close()
    except Exception:
        wb.close()
        raise
    if "作品名称" in header:
        return "videos"
    if "日期" in header:
        return "daily"
    raise ValueError(f"无法识别的导出表头: {sorted(header)[:6]}")


def import_any(xlsx: Path, source_name: str | None = None) -> dict[str, Any]:
    """入口: 自动判别《数据表现》(账号日) 或《作品列表》(单视频真值) 并导入."""
    kind = detect_kind(xlsx)
    if kind == "videos":
        return import_videos_xlsx(xlsx, source_name)
    report = import_xlsx(xlsx, source_name)
    report["kind"] = "daily"
    return report
