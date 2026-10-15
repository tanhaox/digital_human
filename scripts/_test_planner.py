# -*- coding: utf-8 -*-
"""搜索规划器实测: 高市早苗."""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.services.material_ingest_service import _title_clean, plan_queries  # noqa: E402

plan = plan_queries("高市早苗", "person")
print("规划 queries:", plan["queries"])
print("规划 channels:", plan["channels"])
print("避雷词:", plan["avoid"])
print()
tests = ["[CC] Takaichi full speech with subtitles", "首相官邸 午後記者会見",
         "CNN Breaking: Japan PM speech", "Takaichi press conference full"]
for t in tests:
    print(("弃 " if not _title_clean(t, plan["avoid"]) else "取 "), t)
