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

SNAPSHOT_DATE = "2026-11-03"
SCOPE = "account"  # 账号级粉丝画像 (单集观众画像 scope=ep:N 另录)

# (dimension, rank, label, pct, note)
ROWS: list[tuple[str, int, str, float | None, str]] = [
    # 年龄分布 (Σ=100.0 ✓) — 31-40 三档爬升 61→63→64
    ("age", 1, "<23", 3.0, ""),
    ("age", 2, "24-30", 17.0, "1021→1024→1103: 16→16→17"),
    ("age", 3, "31-40", 64.0, "绝对主力, 三档 61→63→64"),
    ("age", 4, "41-50", 13.0, "14→14→13"),
    ("age", 5, ">50", 3.0, ""),
    # 粉丝兴趣 Top6 — 随拍三档稳定#1 (35→36→36, 聚集的是泛流非垂直); 财经三连降 9→8→7
    ("interest", 1, "随拍", 36.0, "三档 35→36→36 稳定#1; 判据①的'聚集'是泛流聚集"),
    ("interest", 2, "社会时政", 13.0, "12→13→13"),
    ("interest", 3, "美食", 11.0, "10→10→11"),
    ("interest", 4, "亲子", 9.0, "7→9→9"),
    ("interest", 5, "财经", 7.0, "⚠三连降 9→8→7 — 打标未向商业/财经收敛"),
    ("interest", 6, "体育", 7.0, "8→7→7"),
    # 地域 Top6 — 名次可读: 河北升第3, 广东降第5; 百分比截图过小未读出
    ("region", 1, "山东", None, "名次可读%; 1024 时 13%"),
    ("region", 2, "河南", None, "1024 时 8%"),
    ("region", 3, "河北", None, "↑1024 第4→第3"),
    ("region", 4, "江苏", None, "↑1024 第6→第4"),
    ("region", 5, "广东", None, "↓1024 第3→第5"),
    ("region", 6, "浙江", None, "1024 第5"),
    # 设备 Top6 (华为 26→28→30 三连升)
    ("device", 1, "华为", 30.0, "26→28→30 三连升"),
    ("device", 2, "苹果", 23.0, "27→23→23"),
    ("device", 3, "荣耀", 10.0, "9→11→10"),
    ("device", 4, "OPPO", 8.0, "9→8"),
    ("device", 5, "VIVO", 6.0, ""),
    ("device", 6, "小米", 6.0, ""),
    # 环形图无数字 (截图不悬停不显示百分比)
    ("gender", 1, "男性", None, "环形图无数字; 1024 悬停读数 90%"),
    ("gender", 2, "女性", None, "1024 悬停读数 10%"),
    ("activity", 1, "重度", None, "环形图无数字; 8折有效粉代理指标, 待悬停截图补真值"),
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
                 "后台截图+glm-4.6v 切片转录 1103"),
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
