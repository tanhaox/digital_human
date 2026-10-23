# -*- coding: utf-8 -*-
"""episode_links 种子 — 抖音投稿日 ↔ 系统集数 挂钩表 (1021 立, 看板v2 核心数据).

数据来源 (证据链, 非 pipeline.db — 后者无发布日期):
- 发布日/档期: docs/analytics/数据记录/视频数据记录-20261005-拆书号.md (HBO 九集逐日全账
  + 09-27 非正片异常) + docs/analytics/INDEX.md 当前状态 (定位 ep1 10-06 20:10 → ep7 10-18 收官,
  "10-20 接档") + 18 个投稿日与 analytics.db 逐日核对吻合
- 集标题: pipeline.db book_episodes (现场拉, 不落死数据)
- 10-20 = 卓有成效的管理者 ep1 (48h 验证试验车 book 719051) — 1021 用户口述更正
  (初版误判为值得信赖的顾问, 已改) — 若再有误改本表重跑即可 (按日 upsert 幂等)

用法: python scripts/seed_episode_links.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "analytics.db"
PIPELINE_DB = ROOT / "data" / "pipeline.db"

# (date, series, book_title, ep_index, publish_time, note)  ep_index None = 非正片
LINKS: list[tuple[str, str, str, int | None, str, str]] = [
    ("2026-09-18", "HBO的内容战略", "HBO的内容战略", 1, "15:18", "系列首集, 唯一非20:10档"),
    ("2026-09-20", "HBO的内容战略", "HBO的内容战略", 2, "20:10", ""),
    ("2026-09-22", "HBO的内容战略", "HBO的内容战略", 3, "20:10", "全系列最弱集(窄题)"),
    ("2026-09-24", "HBO的内容战略", "HBO的内容战略", 4, "20:10", ""),
    ("2026-09-26", "HBO的内容战略", "HBO的内容战略", 5, "20:10", ""),
    ("2026-09-27", "非正片", None, None, "", "异常投稿: 281播/65.7s/零转化 (数据记录1005 §五注)"),
    ("2026-09-28", "HBO的内容战略", "HBO的内容战略", 6, "20:10", "收藏王(42)"),
    ("2026-09-30", "HBO的内容战略", "HBO的内容战略", 7, "20:10", "爆款日 净增30; 100粉=当日"),
    ("2026-10-02", "HBO的内容战略", "HBO的内容战略", 8, "20:10", "量级王(3357播)"),
    ("2026-10-04", "HBO的内容战略", "HBO的内容战略", 9, "20:10", "收官集 吸粉40系列新高; 200粉=当日"),
    ("2026-10-06", "定位", "定位", 1, "20:10", "新书首集, 承接水位2.2×"),
    ("2026-10-08", "定位", "定位", 2, "20:10", ""),
    ("2026-10-10", "定位", "定位", 3, "20:10", ""),
    ("2026-10-12", "定位", "定位", 4, "20:10", ""),
    ("2026-10-14", "定位", "定位", 5, "20:10", ""),
    ("2026-10-16", "定位", "定位", 6, "20:10", ""),
    ("2026-10-18", "定位", "定位", 7, "20:10", "收官集 (Tg 书间交接前夜)"),
    ("2026-10-20", "卓有成效的管理者", "卓有成效的管理者", 1, "20:10", "接档首集 (48h试验车 book 719051); 吸粉40平收官王纪录; 1021用户更正: 系此书非顾问"),
    ("2026-10-21", "卓有成效的管理者", "卓有成效的管理者", 2, "20:10", "48h试验车 ep2 (1023 用户确认投稿; 日数据=截图手录-待核, 正式导出后覆盖)"),
]

DDL = """
CREATE TABLE IF NOT EXISTS episode_links (
  date TEXT PRIMARY KEY,
  series TEXT NOT NULL,
  book_id TEXT,
  book_title TEXT,
  ep_index INTEGER,
  ep_title TEXT,
  publish_time TEXT,
  note TEXT,
  source TEXT NOT NULL DEFAULT 'manual'
)
"""


def fetch_titles() -> dict[tuple[str, int], tuple[str, str]]:
    """(book_title, ep_index) → (book_id, ep_title), 现场拉 pipeline.db."""
    con = sqlite3.connect(f"file:{PIPELINE_DB.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for book_id, btitle, ep, etitle in con.execute(
            "SELECT b.id, b.book_title, e.ep_index, e.title "
            "FROM book_episodes e JOIN book_projects b ON e.book_id = b.id"
        ):
            out[(btitle, ep)] = (book_id, etitle)
        return out
    finally:
        con.close()


def main() -> None:
    titles = fetch_titles()
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(DDL)
        for date, series, book, ep, pub, note in LINKS:
            book_id, ep_title = (None, None)
            if ep is not None:
                hit = titles.get((book, ep))
                if not hit:
                    print(f"[warn] {date} {book} ep{ep} 在 pipeline.db 未找到, 标题留空")
                else:
                    book_id, ep_title = hit
            con.execute(
                "INSERT OR REPLACE INTO episode_links "
                "(date, series, book_id, book_title, ep_index, ep_title, publish_time, note, source) "
                "VALUES (?,?,?,?,?,?,?,?, '数据记录1005+INDEX+投稿日核对')",
                (date, series, book_id, book, ep, ep_title, pub, note),
            )
        con.commit()
        n, miss = con.execute("SELECT COUNT(*), SUM(ep_title IS NULL AND ep_index IS NOT NULL) FROM episode_links").fetchone()
        print(f"[ok] episode_links {n} 行 (标题缺 {miss or 0}); 与投稿日核对:")
        posts = {r[0] for r in con.execute("SELECT date FROM douyin_daily WHERE posts > 0")}
        links = {r[0] for r in con.execute("SELECT date FROM episode_links")}
        print("  投稿日无挂钩:", sorted(posts - links) or "无")
        print("  挂钩无投稿:", sorted(links - posts) or "无")
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
