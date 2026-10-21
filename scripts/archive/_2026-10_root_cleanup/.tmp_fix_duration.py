# -*- coding: utf-8 -*-
"""一次性: 长口播镜拆段 + 时长收归口播覆算 + 缺口镜重roll标记."""
import sys
import logging

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)

from sandbox.anim_pipeline import shots, planner, llm  # noqa: E402
from sandbox.anim_pipeline.config import load  # noqa: E402

cfg = load()
doc = shots.load_doc("HBO的内容战略", 1)

# 1) 拆超长口播镜 (>44 字实讲超 8s, s23=94字/s56=78字)
next_idx = max(int(s["shot_id"][1:]) for s in doc["shots"])
for sid in ("s23", "s56"):
    old = shots.find_shot(doc, sid)
    if len(old["narration"]) <= 44:
        print(f"{sid} 已不超长, 跳过")
        continue
    system = open(cfg.director_prompt_file, encoding="utf-8").read()
    schema = '{"segments": [{"narration", "page_type", "visual_desc", "anim", "text_layer", "qc_notes"}]}'
    user = f"""# 任务: 把一个超长口播镜拆成多个镜头 (它被整段塞进了一个语义段, 违反 ≤44字/段)

# 原镜头画面参考 (新镜头画面延续其场景与隐喻, 不重复构图)
{old['image_prompt_zh'][:120]}

# 待拆口播 (逐字保留, 按语义切成 ≤44 字/段)
{old['narration']}

# 要求
- 每段 narration 为原文连续切片 (合起来 = 原文, 不许改字)。
- 每镜完整 schema (page_type 全 B; anim 时长=字数÷5.5; 遵守全部硬约束)。
- 输出 {schema}"""
    raw = llm.chat_json(cfg.llm, system, user)
    segs = raw.get("segments") or []
    if not segs:
        print(f"{sid} 拆分返回空!"); continue
    pieces = []
    for i, sg in enumerate(segs):
        sg["shot_id"] = f"s{next_idx + 1 + i:02d}"
        sg["t_start"] = old["t_start"]  # auto_fix 会重铺
        sg["t_end"] = old["t_start"]
        sg["label"] = old["label"]
        pieces.append(planner._normalize_shot(sg, next_idx + i, old["label"]))
    mapping = planner._call_translator(cfg, [{"shot_id": p["shot_id"], "visual_desc": n.get("visual_desc", "")} for n, p in zip(segs, pieces)])
    for n, p in zip(segs, pieces):
        p["image_prompt_zh"] = mapping.get(p["shot_id"], "")
    # 原位替换
    idx = next(i for i, s in enumerate(doc["shots"]) if s["shot_id"] == sid)
    doc["shots"][idx:idx + 1] = pieces
    next_idx += len(pieces)
    print(f"{sid} ({len(old['narration'])}字) 拆成 {len(pieces)} 镜: {[p['shot_id'] for p in pieces]}")

# 2) 时长收归口播 + 时间轴重铺 + beats 伸缩
for f in planner._auto_fix(doc):
    pass  # 修正清单静默 (量大)
probs = shots.validate(doc)
print("校验:", probs[:3] if probs else "过")

# 3) 缺口镜 + 新拆镜: 标记重roll (视频需重生成)
regen = []
for s in doc["shots"]:
    narr_s = planner.narration_seconds(s["narration"])
    if s.get("video_file") and abs(float(s["anim"]["duration_s"]) - narr_s) > 1.5 and narr_s <= 9.5:
        regen.append(s["shot_id"])
    elif not s.get("image_file"):
        regen.append(s["shot_id"])
        s["status"] = "planned"
# 已有图但时长变了的镜: 图不动只重生成视频
for sid in regen:
    s = shots.find_shot(doc, sid)
    if s.get("image_file"):
        shots.transition(s, "approved")
        shots.retry_anim(s)
shots.save(doc)
c = {}
for s in doc["shots"]:
    c[s["status"]] = c.get(s["status"], 0) + 1
print(f"总镜 {len(doc['shots'])}, 状态 {c}")
print("待生图:", [s['shot_id'] for s in doc['shots'] if s['status'] == 'planned'])
print("待重roll视频:", [s for s in regen if shots.find_shot(doc, s).get('image_file')])
