"""Dev 脚本: 把 job 183361f0 的部分 broll_local slot 改为 broll_pexels, 验证 P 线.

背景: LLM 规划全落 broll_local (本地素材库在 catalog 里给太多, 规则 4.1 让 LLM
优先选本地文件), Pexels 0 调用。目标"先跑通 P 线", 这里把约 13 个 broll_local
slot (带英文 keywords 的) 就地改为 broll_pexels, 用现成 keywords 作搜索词。
同步更新 director_slots 表 + plan_json, 保证 UI/执行一致。
"""
from __future__ import annotations

import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.config import load_config, set_config
from app.database import init_db, get_session_maker
from app.models import DirectorJob, DirectorSlot

JOB_ID = "183361f0-b17f-464c-8bc4-efbc6e9fdf2d"
CONVERT_COUNT = 13  # 49 个 broll_local 里转 13 个

cfg = load_config()
set_config(cfg)
init_db(cfg.app.database_url)
db = get_session_maker()()

job = db.get(DirectorJob, JOB_ID)
if job is None:
    raise SystemExit(f"job {JOB_ID} not found")

# 1. 收集 broll_local slots (带英文 keywords 的优先, 按 slot_index 升序)
targets: list[DirectorSlot] = []
for s in sorted(job.slots, key=lambda x: x.slot_index):
    if s.workflow != "broll_local":
        continue
    params = s.params_json or {}
    kw = params.get("keywords") or []
    if any(isinstance(k, str) and all(ord(c) < 128 for c in k) for k in kw):
        targets.append(s)

print(f"broll_local 总数: {sum(1 for s in job.slots if s.workflow=='broll_local')}, 带英文 keywords: {len(targets)}")
convert = targets[:CONVERT_COUNT]
print(f"计划转换 {len(convert)} 个 → broll_pexels")

# 2. 转换 DB slots + plan_json 同步
plan_slots = {p["slot_index"]: p for p in (job.plan_json or {}).get("slots", [])}

for s in convert:
    params = dict(s.params_json or {})
    params.pop("file", None)
    params.pop("fallback_file", None)
    params.setdefault("category", "通用场景")
    params["workflow"] = "broll_pexels"
    s.workflow = "broll_pexels"
    s.visual_type = "broll_pexels"
    s.params_json = params

    ps = plan_slots.get(s.slot_index)
    if ps:
        ps["workflow"] = "broll_pexels"
        ps["visual_type"] = "broll_pexels"
        ps["params"] = params

    print(f"  slot#{s.slot_index} → broll_pexels  keywords={params.get('keywords')}")

db.commit()

# 3. 统计
from collections import Counter
c = Counter(x.workflow for x in job.slots)
print("转换后 workflow 分布:", dict(c))
db.close()
