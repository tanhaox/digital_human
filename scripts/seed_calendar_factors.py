# -*- coding: utf-8 -*-
"""calendar_factors 种子 — 节假日/平台因素窗口 (1021 立, 分析口径防坑).

背景 (1021 用户令): 10-01~10-10 国庆假期流量萎缩, 弱日全是窗内无投稿日 —
不标注会把平台因素误读成内容问题。底流实测: 窗内 419.6 vs 窗外 562.4 播/日 (-25.5%);
但窗内发布日吸粉 25.0 vs 窗外 16.7 — 假期杀被动流量, 不杀内容拉动。

用法: python scripts/seed_calendar_factors.py   (按 from+to upsert 幂等; 以后春节等窗口追加)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "analytics.db"

# (date_from, date_to, label, note, source)
FACTORS: list[tuple[str, str, str, str, str]] = [
    ("2026-10-01", "2026-10-10", "国庆假期",
     "底流 -25.5% (窗内无投稿日 419.6 vs 窗外 562.4 播/日); 窗内发布日吸粉反高 (25.0 vs 16.7) — 弱日 10-01/05/07/09 均为窗内无投稿日, 平台因素非内容问题",
     "1021 用户令 + 33 天实测"),
]

DDL = """
CREATE TABLE IF NOT EXISTS calendar_factors (
  date_from TEXT NOT NULL,
  date_to TEXT NOT NULL,
  label TEXT NOT NULL,
  note TEXT,
  source TEXT,
  PRIMARY KEY (date_from, date_to)
)
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(DDL)
        for f in FACTORS:
            con.execute(
                "INSERT OR REPLACE INTO calendar_factors (date_from, date_to, label, note, source) "
                "VALUES (?,?,?,?,?)", f)
        con.commit()
        for r in con.execute("SELECT date_from, date_to, label FROM calendar_factors"):
            print(f"[ok] 因素窗: {r[0]} ~ {r[1]} {r[2]}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
