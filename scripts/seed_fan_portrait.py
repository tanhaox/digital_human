# -*- coding: utf-8 -*-
"""fan_portrait 种子 — 粉丝画像快照 (1021 立, 只能截图→GLM 转录的通道).

来源: 抖音创作者中心·粉丝画像页截图 (2026-10-21, 账号级) → scripts/glm_vision.py
(glm-4.6v) 切片转录; 性别/活跃度环形图该截图无数字标注 (需后台悬停后补截图), 存 NULL+备注。
对比参照: 数据记录/视频数据记录-20260929-拆书号.md ep6 观众画像 (口径=单集观众, 非全账号粉)。

用法: python scripts/seed_fan_portrait.py   (按 snapshot_date+scope+dimension+label upsert 幂等)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "analytics.db"

SNAPSHOT_DATE = "2026-10-21"
SCOPE = "account"  # 账号级粉丝画像 (单集观众画像 scope=ep:N 另录)

# (dimension, rank, label, pct, note)
ROWS: list[tuple[str, int, str, float | None, str]] = [
    # 年龄分布 (Σ=100.0 ✓)
    ("age", 1, "<23", 5.0, ""),
    ("age", 2, "24-30", 16.0, ""),
    ("age", 3, "31-40", 61.0, "绝对主力"),
    ("age", 4, "41-50", 14.0, ""),
    ("age", 5, ">50", 4.0, ""),
    # 粉丝兴趣 Top6 (随拍 35 ↔ 0929 ep6 观众随拍 36, 同构信号)
    ("interest", 1, "随拍", 35.0, "↔0929 ep6 36% 同构"),
    ("interest", 2, "社会时政", 12.0, ""),
    ("interest", 3, "美食", 10.0, ""),
    ("interest", 4, "财经", 9.0, ""),
    ("interest", 5, "体育", 8.0, ""),
    ("interest", 6, "亲子", 7.0, ""),
    # 地域 Top6
    ("region", 1, "山东", 14.0, ""),
    ("region", 2, "广东", 9.0, ""),
    ("region", 3, "江苏", 9.0, ""),
    ("region", 4, "河南", 9.0, ""),
    ("region", 5, "河北", 8.0, ""),
    ("region", 6, "浙江", 8.0, ""),
    # 设备 Top6 (苹果+华为=53%)
    ("device", 1, "苹果", 27.0, ""),
    ("device", 2, "华为", 26.0, ""),
    ("device", 3, "荣耀", 9.0, ""),
    ("device", 4, "OPPO", 8.0, ""),
    ("device", 5, "VIVO", 6.0, ""),
    ("device", 6, "小米", 6.0, ""),
    # 环形图无数字 (截图不悬停不显示百分比)
    ("gender", 1, "男性", None, "环形图无数字; 视觉男性显著占优; 0929 单集观众男95% 参照"),
    ("gender", 2, "女性", None, "无数字"),
    ("activity", 1, "重度", None, "环形图无数字; 视觉重度最大(目测~八成); 8折有效粉代理指标, 待悬停截图补真值"),
    ("activity", 2, "中度", None, "无数字"),
    ("activity", 3, "轻度", None, "无数字"),
    ("activity", 4, "低活", None, "无数字"),
]

NOTE_GLOBAL = "热词分析: 粉丝量未达 1000 暂无 (粉丝关注面板原文)"

DDL = """
CREATE TABLE IF NOT EXISTS fan_portrait (
  snapshot_date TEXT NOT NULL,
  scope TEXT NOT NULL,
  dimension TEXT NOT NULL,
  rank INTEGER,
  label TEXT NOT NULL,
  pct REAL,
  note TEXT,
  source TEXT,
  PRIMARY KEY (snapshot_date, scope, dimension, label)
)
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(DDL)
        for dim, rank, label, pct, note in ROWS:
            con.execute(
                "INSERT OR REPLACE INTO fan_portrait "
                "(snapshot_date, scope, dimension, rank, label, pct, note, source) VALUES (?,?,?,?,?,?,?,?)",
                (SNAPSHOT_DATE, SCOPE, dim, rank, label, pct, note,
                 "后台截图+glm-4.6v 切片转录 1021"),
            )
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM fan_portrait WHERE snapshot_date=? AND scope=?",
                        (SNAPSHOT_DATE, SCOPE)).fetchone()[0]
        missing = con.execute(
            "SELECT COUNT(*) FROM fan_portrait WHERE snapshot_date=? AND scope=? AND pct IS NULL",
            (SNAPSHOT_DATE, SCOPE)).fetchone()[0]
        print(f"[ok] fan_portrait {SNAPSHOT_DATE}/{SCOPE}: {n} 行 (无数字 {missing} 行 — 性别/活跃度待补截图)")
        print(f"[note] {NOTE_GLOBAL}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
