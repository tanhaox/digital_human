# -*- coding: utf-8 -*-
"""抖音《数据表现》xlsx → data/analytics.db 导入 (1021 立, 配 /web/analytics.html 看板).

用法:
    python scripts/import_douyin_metrics.py <数据表现_合并分析.xlsx>

- 按日期 upsert (INSERT OR REPLACE): 新日追加, 重叠日以最新导出为准
- 总粉丝量缺失日按 上日总粉 + 净增 回填 (导出常漏)
- 行级校验 净增 = 吸粉 − 脱粉, 不一致只告警不拦 (导出可能修历史)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "analytics.db"

# xlsx 表头 → DB 列
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


def _norm_date(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    return str(v).strip()[:10]


def load_rows(xlsx: Path, source_name: str) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb["合并明细"] if "合并明细" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header = {h: i for i, h in enumerate(rows[0])}
    colmap = {HEADER_MAP[h]: header[h] for h in header if h in HEADER_MAP}
    missing = [h for h in HEADER_MAP if h not in header]
    if missing:
        raise SystemExit(f"[abort] xlsx 缺列: {missing}")
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


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    xlsx = Path(sys.argv[1])
    if not xlsx.exists():
        raise SystemExit(f"[abort] 文件不存在: {xlsx}")

    rows = load_rows(xlsx, xlsx.name)
    if not rows:
        raise SystemExit("[abort] 无数据行")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(DDL)
        cols = list(HEADER_MAP.values()) + ["source", "updated_at"]
        existing = {r[0] for r in con.execute("SELECT date FROM douyin_daily")}
        new_days = [r["date"] for r in rows if r["date"] not in existing]
        placeholders = ",".join("?" * len(cols))
        for r in rows:
            if r.get("fan_net") is not None and None not in (r.get("fan_new"), r.get("fan_lost")):
                if r["fan_net"] != r["fan_new"] - r["fan_lost"]:
                    print(f"[warn] {r['date']} 净增{r['fan_net']} ≠ 吸粉{r['fan_new']}−脱粉{r['fan_lost']}")
            con.execute(
                f"INSERT OR REPLACE INTO douyin_daily ({','.join(cols)}) VALUES ({placeholders})",
                [r.get(c) for c in cols],
            )
        con.commit()

        # 总粉缺失回填: 按日期序 上日总粉+净增
        backfilled = 0
        cur = con.execute("SELECT date, total_fans, fan_net FROM douyin_daily ORDER BY date")
        data = cur.fetchall()
        prev_total = None
        for d, total, net in data:
            if total is None:
                if prev_total is not None and net is not None:
                    con.execute(
                        "UPDATE douyin_daily SET total_fans = ? WHERE date = ?",
                        (prev_total + net, d),
                    )
                    backfilled += 1
            else:
                prev_total = total
        con.commit()

        n = con.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM douyin_daily").fetchone()
        end_total = con.execute(
            "SELECT total_fans FROM douyin_daily ORDER BY date DESC LIMIT 1"
        ).fetchone()[0]
        print(
            f"[ok] 导入 {len(rows)} 行 (新 {len(new_days)} / 刷新 {len(rows)-len(new_days)}), "
            f"回填总粉 {backfilled} 天\n"
            f"[ok] DB 现存 {n[0]} 行, 范围 {n[1]} ~ {n[2]}, 期末总粉 {end_total}"
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
