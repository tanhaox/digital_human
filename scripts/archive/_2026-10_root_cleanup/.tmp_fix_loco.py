# -*- coding: utf-8 -*-
"""一次性: 扫位移行进类节拍并重写 + 全镜换 h3_seed 重启视频批."""
import sys
import logging
import re

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)

from sandbox.anim_pipeline import shots, planner, llm  # noqa: E402
from sandbox.anim_pipeline.config import load  # noqa: E402

cfg = load()
doc = shots.load_doc("HBO的内容战略", 1)

# 位移/行进类高危动词 (英文 beats)
LOCO_RE = re.compile(
    r"\b(walk|walks|walking|run|runs|running|jog|sprint|rushes|march|steps forward|moves forward|"
    r"advances|pushes (the |a )?(cart|trolley)|strolls|strides|crosses the room|enters|exits)\b", re.I)

flagged = []
for s in doc["shots"]:
    text = " ".join(b.get("motion", "") for b in s["anim"].get("beats") or []) + " " + (s["anim"].get("ending") or "")
    if LOCO_RE.search(text):
        flagged.append(s)
print(f"位移行进高危镜 {len(flagged)}:", ",".join(s["shot_id"] for s in flagged) or "无")

if flagged:
    system = open(cfg.director_prompt_file, encoding="utf-8").read()
    listing = "\n".join(
        f"- {s['shot_id']} [{s['page_type']}] {s['anim']['duration_s']}s 画面: {s['image_prompt_zh'][:70]} | 现节拍: "
        + " / ".join(b.get("motion", "")[:60] for b in s["anim"]["beats"][:3])
        for s in flagged
    )
    schema = '{"segments": [{"shot_id", "beats": [{"t_start","t_end","motion"}], "ending"}]}'
    user = f"""# 任务: 重写下列镜头的动画节拍 — 它们含"角色位移行进"动作(走/跑/推车前进), 视频模型会钉在原地变成"原地跑步"或与道具穿插 (实锤)

# 待重写镜头 (画面不变, 只重写 anim)
{listing}

# 要求
- 人物锚定原地: 只许转身/抬手/点头/俯身/蹲起/微表情; 位移感用镜头缓推、光影流动、背景元素渐变表达。
- 道具(推车等)也钉住: 车不动, 动手部动作与车轮以外的细节(标签翻动、灯光次第亮、光晕呼吸)。
- 每拍至少一个可见变化; 总拍数不变时间轴不变; ending 保留微动(风/尘埃/光晕), 禁纯定格。
- 输出 {schema}"""
    raw = llm.chat_json(cfg.llm, system, user)
    segs = raw.get("segments") or []
    fixed = 0
    for seg in segs:
        sid = seg.get("shot_id")
        old = next((t for t in flagged if t["shot_id"] == sid), None)
        if not old or not seg.get("beats"):
            continue
        fresh = planner._normalize_shot(
            {**old, "anim": {**old["anim"], "beats": seg["beats"], "ending": planner._clean_ending(seg.get("ending", ""))}},
            0, old["label"])
        old["anim"] = fresh["anim"]
        fixed += 1
        print(f"{sid} 节拍已锚定重写")
    print(f"重写 {fixed}/{len(flagged)}")

# 全镜换 h3_seed, 状态归 approved (全新壳+新节拍全量重生成)
for s in doc["shots"]:
    shots.transition(s, "approved")
    shots.retry_anim(s)
shots.save(doc)
print(shots.summary(doc))
