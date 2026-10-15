# -*- coding: utf-8 -*-
"""一次性: 清扫 ending 前缀/白闪漏网 + 重写 4 个动作饥饿镜的节拍 (s02/s03/s04/s05)."""
import sys
import logging

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)

from sandbox.anim_pipeline import shots, planner, llm  # noqa: E402
from sandbox.anim_pipeline.config import load  # noqa: E402

cfg = load()
doc = shots.load_doc("HBO的内容战略", 1)

# 1) 全量清扫 ending (B:/C: 前缀 + B 镜白闪)
n_prefix = n_flash = 0
for s in doc["shots"]:
    anim = s["anim"]
    cleaned = planner._clean_ending(anim.get("ending", ""))
    if cleaned != anim.get("ending", ""):
        n_prefix += 1
    anim["ending"] = cleaned
    if s["page_type"] == "B" and ("flash" in cleaned.lower() or "speed line" in cleaned.lower()):
        anim["ending"] = planner._CALM_HOLD
        n_flash += 1
print(f"ending 清扫: 前缀 {n_prefix} 处, B镜白闪 {n_flash} 处")

# 2) 重写 4 镜节拍 (反静止)
redo = ["s02", "s03", "s04", "s05"]
targets = [shots.find_shot(doc, sid) for sid in redo]
system = open(cfg.director_prompt_file, encoding="utf-8").read()
listing = "\n".join(
    f"- {s['shot_id']} [{s['page_type']}] {s['anim']['duration_s']}s 画面: {s['image_prompt_zh'][:70]}"
    for s in targets
)
schema = '{"segments": [{"shot_id", "beats": [{"t_start","t_end","motion"}], "ending"}]}'
user = f"""# 任务: 重写下列镜头的动画节拍 — 现节拍动作饥饿 (大量 stays still / no object moves), 视频模型没指令就自由发挥导致穿模

# 待重写镜头 (画面不变, 只重写 anim)
{listing}

# 要求
- 画面元素以 image_prompt 为准, 每拍至少一个清晰可见的变化 (光线明暗/发光/物件移动开合/人物微动作/飘尘)。
- 总拍数 2-4 拍, 时间轴首尾相接且总长 = duration_s; 每拍一至两句英文。
- ending: B 镜写定格落点 (the scene settles into a calm, stable hold 类), 不带任何前缀。
- 遵守四禁令与动作安全词。
- 输出 {schema}"""

raw = llm.chat_json(cfg.llm, system, user)
segs = raw.get("segments") or []
assert len(segs) >= len(redo) - 1, f"返回 {len(segs)} 不足"
for seg in segs:
    sid = seg.get("shot_id")
    old = next((t for t in targets if t["shot_id"] == sid), None)
    if not old:
        continue
    fresh = planner._normalize_shot({**old, "anim": {**old["anim"], "beats": seg.get("beats", []), "ending": seg.get("ending", "")}}, 0, old["label"])
    old["anim"] = fresh["anim"]
    for b in old["anim"]["beats"]:
        print(f"{sid} 拍{b['t_start']}-{b['t_end']}s: {b['motion'][:100]}")

shots.save(doc)
print("落盘完成")
