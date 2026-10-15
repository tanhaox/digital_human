# -*- coding: utf-8 -*-
"""i2v_prompt — 意象 i2v 提示词模板 (2026-08-27, #2).

Hell Grind 文法三件套落模板: FOV 结果化 + 物理质感 + 针对性负向。
正向骨架直接消费 #4 镜头契约 (shot_contract) 的字段 — 链路:
契约(#4 shot_contract.py) → 本模板(#2) → wan22 生成 → QC/迭代(#3 i2v_iteration.py)。

用法:
  from i2v_prompt import build_prompt, NEG_BASE
  positive = build_prompt(contract)              # 契约驱动
  positive = build_prompt(motion="光点从高处沿重力倾泻", camera="缓慢推进", ...)
  negative = NEG_BASE + "，" + "，".join(targeted_negs(contract))

CLI 自查: python sandbox/i2v_prompt.py demo
"""
from __future__ import annotations

import json
import sys
from typing import Any

# ── 负向基线 (照抄 wan22_i2v_test.py 实测基线, 已含反静态推力) ──────────
NEG_BASE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
            "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
            "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
            "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")
# 亮调版 (兵器谱: 亮调需去"色调艳丽"防偏暗)
NEG_BRIGHT = ("过曝，死白，过暗，死黑，整体发灰，静态，细节模糊不清，字幕，最差质量，"
              "低质量，JPEG压缩残留，静止不动的画面，杂乱的背景，倒着走")

# 风险 → 针对性负向 (HELL-GRIND: 只防本镜风险, 不堆通用词)
# 契约 risk_focus 是中文自由文本 — 中英关键词都匹配
_RISK_NEG: dict[str, dict] = {
    "F-MELT": {"keys": ["melt", "融化", "粘连", "流失"], "negs": ["边缘融化", "纹理流失", "形体粘连"]},
    "F-DRIFT": {"keys": ["drift", "漂移", "重构", "乱飘"], "negs": ["主体漂移出画面", "布局重构"]},
    "F-ARTIFACT": {"keys": ["artifact", "伪影", "多余", "穿透", "肢体"], "negs": ["多余肢体", "物体穿透", "塑料质感"]},
    "F-MOTION-NONE": {"keys": ["none", "静止", "不动"], "negs": []},  # 基线"静态/静止"双覆盖
    "F-STYLE-SLOP": {"keys": ["slop", "塑料", "锐化", "饱和", "AI味"], "negs": ["过度锐化", "色彩过饱和"]},
    "F-PROMPT-OVERDO": {"keys": ["过度", "烟雾", "浓", "过量", "雾霾"], "negs": ["浓重烟雾", "烟雾弥漫"]},
}


# 风格定位 → 前缀/负向 (2026-08-27 用户定稿: "弄成卡通的、手办类的感觉, 别直接往
# 实物上生成" — AI 意象统一走卡通手办美学; 真实画面另有素材API管线, 写实是较劲)
_STYLE_MAP: dict[str, dict] = {
    "示意": {"prefix": "卡通手办风格的微缩立体场景：圆润可爱的形体，材质像磨砂塑料与树脂模型，摆放感强，色彩明快干净，柔和的摄影棚布光",
             "negs": ["照片级写实", "真实照片", "摄影质感", "粗糙纹理"]},
    "抽象": {"prefix": "抽象概念可视化，纯粒子与光的运动，无具体物体",
             "negs": ["具体物体", "照片级写实"]},
    "写实": {"prefix": "", "negs": []},  # 明确要写实时才用 (Wan2.2 打不赢的仗)
    # 未标 = 同示意 (契约默认)
    "": {"prefix": "卡通手办风格的微缩立体场景：圆润可爱的形体，材质像磨砂塑料与树脂模型，摆放感强，色彩明快干净，柔和的摄影棚布光",
         "negs": ["照片级写实", "真实照片", "摄影质感", "粗糙纹理"]},
}


def style_directives(contract: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """契约 style → (正向前缀, 针对性负向). 缺省按示意处理."""
    spec = _STYLE_MAP.get(str((contract or {}).get("style") or "").strip(), _STYLE_MAP["示意"])
    return spec["prefix"], list(spec["negs"])


def brightness_anchor(contract: dict[str, Any] | None = None) -> str:
    """亮度锚 (兵器谱: 亮调需'负面词手术+单亮度锚' — Wan2.2 t2v 暗调默认).

    契约 brightness 中调/亮调 (或未标) → 返回单条亮度锚追加正向;
    暗调 → 空 (不干预, 场景本身要暗)。
    """
    b = (contract or {}).get("brightness") or "中调"
    if b == "暗调":
        return ""
    if b == "亮调":
        return "画面整体明亮通透，如同充足的自然光照亮所有细节"
    return "画面整体明亮，主体细节清晰可见"


def build_prompt(
    contract: dict[str, Any] | None = None,
    *,
    subject: str = "",
    motion: str = "",
    camera: str = "",
    light_material: str = "",
    first_frame: str = "",
) -> str:
    """契约驱动 (推荐) 或字段直给 → wan22 正向提示词.

    骨架: [视角/画面结果] + 主体与物理行为运动 + [光源行为]。
    摄影术语 (mm数/广角长焦) 一律不进提示词 — 契约的 camera 字段必须已是结果式。
    """
    c = contract or {}
    subject = subject or c.get("first_frame") or ""
    motion = motion or c.get("motion") or ""
    camera = camera or c.get("camera") or ""
    light = light_material or c.get("light_material") or ""
    parts = [p.strip("，。 ") for p in (subject, motion, camera, light) if p and p.strip("，。 ")]
    if not parts:
        raise ValueError("空提示词: 需要 contract 或至少一个字段")
    return "，".join(parts)


def targeted_negs(contract: dict[str, Any] | None = None) -> list[str]:
    """契约 risk_focus → 针对性负向 (去重, 不与基线重复)."""
    risks = [str(r) for r in ((contract or {}).get("risk_focus") or [])]
    out: list[str] = []
    for r in risks:
        for spec in _RISK_NEG.values():
            if any(k.lower() in r.lower() for k in spec["keys"]):
                out.extend(spec["negs"])
    seen: set[str] = set()
    return [n for n in out if not (n in seen or seen.add(n))]


# ── A/B 对照集: 10 条典型意象 (两线常用), 旧式(形容词) vs 新式(骨架) ──────
AB_SET: list[dict[str, str]] = [
    {"name": "数据流", "subject": "数据中心机房",
     "old": "震撼的蓝色数据流，未来科技感，高质量，电影感，8K",
     "new": "视角收窄，画面只有服务器机架面板的细节，蓝色光点像水一样从高处沿重力倾泻，落到面板上溅起细小颗粒，颗粒沿面板缓慢铺开，顶部冷色LED灯带直射，光点经过时表面高光依次滑过，镜头缓慢推进"},
    {"name": "算力机房", "subject": "机房走廊",
     "old": "宏大的算力中心，冰冷的机器，紧张的氛围，大片质感",
     "new": "视角约47度能看到走廊两侧机柜的前后关系，指示灯沿机架面板依次快速亮起又熄灭形成光流，冷气从顶部通风口向下沉，在地面铺成低矮雾层，机柜金属表面反射雾层的微光，镜头沿走廊匀速前进"},
    {"name": "关税壁垒", "subject": "港口集装箱",
     "old": "厚重的关税壁垒压迫感，国际紧张局势，写实风格",
     "new": "视角平视，画面里是一整面集装箱墙，一只巨大的闸门因自身重量缓慢下压，铰链处扬起细小灰尘，灰尘沿下落方向缓慢沉降，金属闸门边缘反射傍晚的橙色天光，镜头保持静止"},
    {"name": "供应链", "subject": "传送带",
     "old": "繁忙的供应链，高效的物流，动感十足",
     "new": "视角侧面平视传送带，包裹一个接一个因传送带摩擦被带向画面深处，较重的包裹压得传送带轻微下凹，扬起的少量灰尘跟随包裹方向缓慢飘动，顶灯从上方直射，包裹顶面高光随位置移动，镜头静止"},
    {"name": "资本流向", "subject": "城市夜景",
     "old": "资本涌动，璀璨的金融城市夜景，繁华，大气磅礴",
     "new": "视角俯瞰整片城市夜景，成片窗户灯光像涨潮一样从城市边缘向中心逐排亮起，亮起的速度沿街道方向递进，玻璃幕墙反射这些新亮起的光斑，镜头极缓慢地垂直下压"},
    {"name": "知识入脑", "subject": "书页与光",
     "old": "知识流入大脑，智慧的火花，温暖治愈",
     "new": "视角为特写，画面只有摊开的书页，细小的金色光点从书页文字处升起，上升时受微弱气流影响轻微左右摇摆，升到画面上方后逐渐变暗消失，书页边缘被光源照亮，镜头静止"},
    {"name": "书籍意象", "subject": "书堆",
     "old": "厚重的知识宝库，岁月感，书香氛围",
     "new": "视角低角度仰视，一摞旧书因最上层一本被取走而轻微回弹，扬起的灰尘在光柱中缓慢下落，书脊的烫金字反射光柱的光，镜头保持静止"},
    {"name": "谈判桌", "subject": "会议室",
     "old": "紧张的博弈，针锋相对的谈判现场，压迫感",
     "new": "视角从桌面高度平视桌面另一侧，一杯水因桌面被轻轻敲击而先倾斜再恢复平稳，水面波纹沿径向扩散后逐渐平息，顶光在水面形成晃动的高光，镜头静止"},
    {"name": "光倾泻", "subject": "光束",
     "old": "神圣的光芒倾泻而下，史诗感，震撼画面",
     "new": "视角仰视，一束光从高处破口处沿重力直落，光柱边缘的细小尘埃随气流缓慢翻滚下落，落点处表面被照亮并反射出光柱形状，镜头极缓慢地绕光柱四分之一圈"},
    {"name": "粒子汇聚", "subject": "粒子群",
     "old": "粒子汇聚成形状，炫酷的科技动画，高级感",
     "new": "视角正面，分散的粒子因相互吸引向中心缓慢聚拢，先到的粒子在中心堆积出轮廓，后到的粒子撞上堆积面后轻微弹开再贴附，粒子表面反射汇聚过程的光，镜头缓慢推进"},
]


def _demo() -> None:
    for row in AB_SET:
        print(f"━━ {row['name']} ━━")
        print(f"  旧式({len(row['old'])}字): {row['old']}")
        print(f"  新式({len(row['new'])}字): {row['new'][:80]}…")
    # 契约驱动示例
    c = {"first_frame": "特写，芯片表面电路的细节，背景虚化成暗色",
         "motion": "电流沿电路纹理依次点亮，光沿走线方向传递",
         "camera": "镜头缓慢推进", "light_material": "电路自发光，金属基板反射微光",
         "risk_focus": ["边缘融化"]}
    print("━━ 契约驱动示例 ━━")
    print(" positive:", build_prompt(c))
    print(" negative:", NEG_BASE + ("，" + "，".join(targeted_negs(c)) if targeted_negs(c) else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "json":
        print(json.dumps(AB_SET, ensure_ascii=False, indent=1))
    else:
        _demo()
