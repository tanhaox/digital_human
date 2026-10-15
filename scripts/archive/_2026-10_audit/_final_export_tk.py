# -*- coding: utf-8 -*-
"""终验: 高市早苗命中切片内容 + 导出草稿."""
import json
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("data/pipeline.db")
for no in ("V20260830-0013", "V20260828-0757"):
    r = con.execute(
        "select description_zh, description_en, duration_sec "
        "from video_assets where asset_no=?", (no,)).fetchone()
    if r:
        print(no, "|", (r[0] or "")[:32], "|", (r[1] or "")[:42], "|", r[2], "s")
jid = con.execute("select id from director_jobs where id like '5f4a5c7d%'").fetchone()[0]
req = urllib.request.Request(
    f"http://127.0.0.1:54321/api/director/jobs/{jid}/export-jy-draft",
    data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=120) as r:
    d = json.loads(r.read().decode())
    print("草稿:", d.get("draft_name"), "| 视频段:", d.get("video_segments"),
          "| 特效:", d.get("fx_attached"))
