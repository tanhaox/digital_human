# -*- coding: utf-8 -*-
"""查询测试 job 状态."""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
d = json.loads(urllib.request.urlopen(
    "http://127.0.0.1:54321/api/materials/ingest/jobs?limit=5", timeout=10).read())
j = next((x for x in d if x["id"].startswith("803377c5")), None)
print("stage:", j["stage"] if j else "gone")
print("stats:", (j or {}).get("stats"))
print("err:", (j or {}).get("error"))
