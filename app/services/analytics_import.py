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
    "平均播放时长(s)": "avg_watch_sec",
    "播放/投稿": "views_per_post",
    "总粉丝量": "total_fans",
    "粉丝净增": "fan_net",
    "吸粉量": "fan_new",
    "脱粉量": "fan_lost",
    "回访粉丝量": "revisit_fans",
    "吸粉效率(粉/万播放)": "fan_efficiency",
}

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
    missing = [h for h in HEADER_MAP if h not in header]
    if missing:
        raise ValueError(f"xlsx 缺列: {missing}")
    colmap = {HEADER_MAP[h]: header[h] for h in header if h in HEADER_MAP}
    out = []
    now = datetime.now().isoformat(timespec="seconds")
    for line in rows[1:]:
        if line[header["日期"]] is None:
            continue
        r = {c: line[i] for c, i in colmap.items()}
        d = date.fromisoformat(_norm_date(r["date"]))
        r["date"] = d.isoformat()
        r["weekday"] = d.isoweekday()
        r["source"] = source_name
        r["updated_at"] = now
        out.append(r)
    return out


def import_xlsx(xlsx: Path, source_name: str | None = None) -> dict[str, Any]:
    """导入一份导出 xlsx, 返回报告 dict (不抛业务异常外的意外)."""
    rows = load_rows(xlsx, source_name or xlsx.name)
    if not rows:
        raise ValueError("xlsx 无数据行 (合并明细 sheet 为空)")

    warns: list[str] = []
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(DDL)
        existing = {r[0] for r in con.execute("SELECT date FROM douyin_daily")}
        new_days = [r["date"] for r in rows if r["date"] not in existing]
        placeholders = ",".join("?" * len(COLUMNS))
        for r in rows:
            if r.get("fan_net") is not None and None not in (r.get("fan_new"), r.get("fan_lost")):
                if r["fan_net"] != r["fan_new"] - r["fan_lost"]:
                    warns.append(f"{r['date']} 净增{r['fan_net']} ≠ 吸粉{r['fan_new']}−脱粉{r['fan_lost']}")
            con.execute(
                f"INSERT OR REPLACE INTO douyin_daily ({','.join(COLUMNS)}) VALUES ({placeholders})",
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
        end_total = con.execute(
            "SELECT total_fans FROM douyin_daily ORDER BY date DESC LIMIT 1").fetchone()[0]
    finally:
        con.close()

    report = {
        "imported": len(rows), "new": len(new_days), "refreshed": len(rows) - len(new_days),
        "backfilled_total_fans": backfilled, "db_days": n_days,
        "range": {"from": d_min, "to": d_max}, "end_total_fans": end_total, "warns": warns,
    }
    return report
