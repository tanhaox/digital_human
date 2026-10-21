# -*- coding: utf-8 -*-
"""一次性: 重做 6 个墙隐喻重复镜 (s03/s06/s07/s28/s34/s45), 换隐喻载体."""
import sys
import logging

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)

from sandbox.anim_pipeline import shots, planner, llm  # noqa: E402
from sandbox.anim_pipeline.config import load  # noqa: E402

cfg = load()
doc = shots.load_doc("HBO的内容战略", 1)
redo = ["s03", "s06", "s07", "s28", "s34", "s45"]
targets = [shots.find_shot(doc, sid) for sid in redo]

system = open(cfg.director_prompt_file, encoding="utf-8").read()
listing = "\n".join(
    f"- {s['shot_id']} [{s['page_type']}] {s['t_start']}-{s['t_end']}s "
    f"口播: {s['narration'][:80]} | 现画面(全是墙,作废): {s['image_prompt_zh'][:60]}"
    for s in targets
)
schema_line = '输出 {"segments": [{"shot_id","t_start","t_end","narration","page_type","visual_desc","anim","text_layer","qc_notes"}]}'
user = f"""# 任务: 重做下列镜头的画面 — 它们全用了「墙」做隐喻, 严重重复 (全集墙镜只许保留 s01 开场钩子一处)

# 待重做镜头
{listing}

# 要求
- 每镜给全新画面: 不得出现墙/砖墙/摸墙/面对墙/墙面; 「空间耗尽/增长停滞」概念换不同隐喻载体轮换 (收窄走廊/装满的容器/到顶电梯/楼梯到头/见底水池/关死门/用尽胶卷/停摆钟 等, 每镜不同)。
- t_start/t_end/narration/page_type 原样保留; anim 全部重写 (opening_desc 对齐新画面); 遵守全部硬约束 (人设逐字/零文字/禁悬浮/四禁令)。
- {schema_line}"""

raw = llm.chat_json(cfg.llm, system, user)
segs = raw.get("segments") or []
print(f"导演返回 {len(segs)} 镜 (kimi 额度尽则走 deepseek 兜底)")
assert len(segs) >= len(redo) - 1, "返回镜数不足"

mapping = planner._call_translator(
    cfg, [{"shot_id": s.get("shot_id"), "visual_desc": s.get("visual_desc", "")} for s in segs]
)
for seg in segs:
    sid = seg.get("shot_id")
    old = next((t for t in targets if t["shot_id"] == sid), None)
    if not old:
        continue
    fresh = planner._normalize_shot({**seg, "k2_prompt": mapping.get(sid, "")}, 0, old["label"])
    for k in ("image_prompt_zh", "anim", "text_layer", "qc_notes"):
        old[k] = fresh[k]
    shots.reject_for_reroll(old, "墙面隐喻重复, 换载体重做")
    print(f"{sid} 换新画面: {old['image_prompt_zh'][:60]}")

shots.save(doc)
print("落盘, 待重roll:", redo)
