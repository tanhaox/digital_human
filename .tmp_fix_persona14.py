# -*- coding: utf-8 -*-
"""一次性: 人设缩写系统性修复 (14镜) + s06/s31/s08 文字与措辞修正, 全部重roll."""
import sys

sys.path.insert(0, ".")

from sandbox.anim_pipeline import shots  # noqa: E402

F_PERSONA = "灰色短头发、白衬衣、黑色短裙、黑色高跟鞋"
M_PERSONA = "黑色短头发、白衬衣、黑色西裤、黑色皮鞋"


def ensure_persona(p: str, name: str, full: str) -> str:
    """人设全串保证在场: 有碎片→扩全; 完全没有→插在称谓前."""
    if name not in p or full in p:
        return p
    frags = ["灰发白衬衣", "灰色短发", "灰短发", "灰发", "黑短发", "黑色短发", "黑发白衬衣", "黑发"]
    for f in frags:
        if f in p:
            return p.replace(f, full, 1)
    return p.replace(name, full + "的" + name, 1)


doc = shots.load_doc("HBO的内容战略", 1)

# 1) 人设注入 (扫描出的风险镜)
hair_f = ["s04", "s05", "s10", "s11", "s50", "s51", "s53"]
hair_m = ["s06", "s07", "s08", "s09", "s12"]
skirt_f = ["s04", "s05", "s10", "s26"]
redo = set(hair_f) | set(hair_m) | set(skirt_f) | {"s31"}
for sid in redo:
    s = shots.find_shot(doc, sid)
    before = s["image_prompt_zh"]
    s["image_prompt_zh"] = ensure_persona(s["image_prompt_zh"], "女主", F_PERSONA)
    s["image_prompt_zh"] = ensure_persona(s["image_prompt_zh"], "男主", M_PERSONA)
    if s["image_prompt_zh"] != before:
        print(f"{sid}: 人设注入 ✓")

# 2) 三个特修
fixes = {
    "s06": ("小电视台门口", "小电视台门口（门口无招牌无任何文字）"),
    "s31": ("橙色减分标记", "橙色警示光点"),
    "s08": ("线头弹回", "末端停在墙板前泛起一圈红光"),
}
for sid, (old, new) in fixes.items():
    s = shots.find_shot(doc, sid)
    if old in s["image_prompt_zh"]:
        s["image_prompt_zh"] = s["image_prompt_zh"].replace(old, new)
        print(f"{sid}: 措辞修正 ✓ ({new[:18]}…)")
    else:
        print(f"{sid}: ⚠ 没找到目标词「{old}」— 人工看一眼")

# 3) 全部换 seed 重roll
for sid in sorted(redo):
    s = shots.find_shot(doc, sid)
    shots.reject_for_reroll(s, "人设/文字/措辞修复重roll")
shots.save(doc)
print(f"\n待重roll {len(redo)} 镜:", ",".join(sorted(redo)))
