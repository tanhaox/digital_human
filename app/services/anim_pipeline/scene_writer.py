# -*- coding: utf-8 -*-
"""单写者 (1013 P0 阶段2, 用户令: K2/H3 两层一体 + 一次完整写作).

HBO/定位 被认可的根因 = 每镜一次完整写作 (keyframe_zh/en + motion_zh/en +
text_layer 同源), keyframe_en→opening_desc 逐字锚定。1009 后的结构层/槽层/
装配层/增强1/增强2 五拨写者互相稀释 (卓有 ep1 七跑实锤)。本模块恢复单写者
主路: director2.plan_scene(mode="writer") 复用全部场窗/槽表/合并基建, 只换
导演人格 (WRITER_SYS) 与输出契约 (五件套)。

- dispatch_scene: 三入口 (全清续跑/单场🔁/逐场流水) 统一调度, 读宪法
  writer_enabled 开关; writer ×2 败退现行链 (打标 fallback)。
- writer_failures: 出口闸 (五件套/slot对表/scene_tag/邻花字), 进 fix_hints。
- 闸门纪律: 只验最终产物 — writer 镜 keyframe 非占位, validate_doc 的配额
  直接量真句; 器层/增强不再触碰 writer 镜 (多写者根除)。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import constitution as CST

logger = logging.getLogger(__name__)

# 1014 P1 (ep3 批图实锤: s03 白板写满/s07 便签写着'专业技能'/s46 模糊字迹 — K2 全渲
# 成错字): 文字载体动词表 — writer 出口闸逼二段式 (空白载体+in_image_text) 或去文字物件。
# 1015 补: 刻着/刻字 (ep4 s45/s46 fallback 装配句「透镜刻黑字'找没毛病的'」形态)。
_TEXT_CARRIER_RE = re.compile(r"写着|字样|字迹|写满|刻着|刻字")
# 1021 公共别名: erase 触发的提示词嫌疑判定 (名词+动词) 同源引用, 改词表只改这里
TEXT_CARRIER_VERB_RE = _TEXT_CARRIER_RE

# 1015 H 修复① (ep4 A1 二次replan实锤: 「桶身贴米白空白标签」名词措辞零命中出口闸
# → 白标签孤儿): 载体名词表补动词措辞之外的名词盲区。公共名单单源化 — k2.py
# 救场判定 (修复③) 同源 import, 改词表只改这里。
TEXT_CARRIER_NOUN_RE = re.compile(r"标签|便签|白板|牌匾|匾额|招牌|标语|横幅|铭牌|海报|屏幕")

# 1015 H3 战役 (用户方向: 查图/动画两段提示词相悖): 花字/文字事件规格泄入 motion_zh
# (s15 铁证「字体为复古手写体带发光持续1.5秒后淡出」= text_layer 事件被整段复制进动作句,
#  文字内容截丢; s26 预测命中) — motion 散文永不描述文字, 文字只准走三路由字段。
_MOTION_TEXT_RE = re.compile(r"字体|手写体|衬线|书法体|发光效果|淡出|浮现.{0,6}[字数]|大字|字样|"
                             r"上屏|字幕|[「『\"“'][^」』\"”']{1,4}[」』\"”'].{0,8}(浮|现|停)")

# 1015 动画先行律: 画面句是 t=0 起点帧 — 终态词禁入 (s11「横梁向右倾斜定格」vs 动作
# 「右盘持续下沉」结构性矛盾实锤, H3 只能重排)。
_ENDSTATE_RE = re.compile(r"定格|停在.{0,6}(状态|位置)|已(完成|停住|倾斜)|最终状态")

# H3 事件字 ↔ 画面句留白预留 配对检查词 (中英)
_RESERVE_RE = re.compile(r"留白|留出|干净.{0,4}(空|背景|空间)|空白.{0,4}(天空|背景|区域)|"
                         r"clean (empty )?(sky|space|background)|reserved|uncluttered")


WRITER_SYS = """你是顶级动画短片的导演兼电影摄影师 (Writer-Director)。一场戏由你**一次写完**：
每一镜同时产出 画面句(中/英) + 动画层(中/英) + 文字层 —— 五件套同源，K2 图就是 H3 视频的第 0 帧。

## 动画先行设计律 (1015 架构令 — 最高设计法, 违反即废稿)
每镜的**设计顺序是先动画后首帧**:
1. 先设计整镜动画: 谁从哪到哪、几点几秒发生什么、文字事件(若有)的**确切文字/出现秒数/落点位置**。
2. 画面句 = 该动画的 **t=0 起点帧**: 人物在动作**起点位置**(从左走到中→首帧人在左); 动画将用到的
   道具**全部初始在场**; 表情/姿态是动作前一刻; **禁终态定格**(「定格/完成态/已倾斜停住」类词禁入画面句)。
   **脸向必写 (1017 无脸根治, 词面以 ruleset.FACE_WORDS 为准)**: 人物镜画面句必须明写
   脸向（面向镜头/侧身回望镜头/正脸或四分之三侧脸朝镜）— 不写 = 渲染默认背镜看道具
   (A1/A3/A4 整弧避脸实锤), 且系统落盘闸会强制注入; 背影/剪影仅当本书圣经
   character_policy 明示允许, 且每弧 ≤1/3 镜。画面句总长 ≤320 字 (ruleset.KEYFRAME_MAX,
   超长被句界截断)。
3. **文字三路由** (每个文字元素必须三选一, 绝不混用; motion_zh/en 散文里**永不描述文字**):
   - **H3 事件字** (动画中浮现, 如头顶浮出「太棒了」): 申报字段 `h3_text_event`
     {"text": "≤4字", "at_s": 秒, "zone": "落点(如 头顶上方)"}; 画面句必须在对应落点**留白预留**
     (写「头顶上方留出干净空背景」类描述); H3 渲染它。
   - **K2 图内字** (t=0 就在画面上, 标签/匾额/牌面): 画面句写**空白载体** + 申报 `in_image_text`
     (≤4行) — 系统二段合成, 禁止画面句直接写「写着XX/黑字/手写体」。
   - **花字** (后期叠字): 只进 `text_layer`。
   动画事件字与图内字可同镜共存, 但落区不同且互不遮挡。
你不是配插图的人，是拿镜头讲故事的人。

## 北极星（最高优先级, 1013 用户令 — 与本节冲突时北极星赢）
目标 = 把书讲清楚。口播是**旁白音轨**（如MTV歌词，与画面相互呼应）：
配合契约由立意层定 — 每场一个视觉世界+隐喻核心，整场在该世界内推进；每镜
选**与当前口播句呼应的隐喻切面**（口播说"日程被切碎"→攀登者脚下碎片台阶崩落）。
禁字面配图（逐句翻译口播）；也禁跳出立意世界另起隐喻（换世界=违约）。
画面唯一目的=加深口播理解：观众看画面+听旁白，比只听更容易懂。人物统一 = 同一角色同一张
脸；概念复杂时，把它翻译成一个简单、可视、有人在做的动作。隐喻与例子必须生活常见、
初中水平秒懂 — 禅机级/需要琢磨的比喻直接弃。

## 受众与定位
观众是在抖音信息流里高速滑动的人：3 秒抓不住就划走。文稿已达内训级深度，
画面必须达到电影级：内容可以深，画面绝不许性冷淡。你写得好不好，看每一镜
单独截出来是不是一张有戏的电影帧。

## 单写者铁律（违反即废稿）
1. 五件套缺一不可：keyframe_zh / keyframe_en / motion_zh / motion_en / text_layer (无字镜给 [])。
2. 锚定链：keyframe_en 必须是 keyframe_zh 的**同构图英文版**（同一批实体、同一构图、同一光线），
   系统会把 opening_desc 逐字取 keyframe_en —— 两层讲两个世界 = 废稿。
3. motion_zh 只允许动画 keyframe_zh 里**已有的实体**（画面里没有的东西永不出现），
   并给出秒级事件（"第1.0秒…"）；motion_en 是它的英文同源版，**每镜必填**
   (英文环境微动 + at most ONE text event)，漏填任一镜 = 整场废稿。
4. 每镜 200-250 字中文画面句：景别角度开头，然后是空间、光线现象（禁色温/补光比等术语）、
   主体（人物带造型括注）、动作、可画细节。散文，不是参数表。
5. 人物多样性：同一场里出现的人物在姿态/景别/构图上必须各异；人物统一=书级冻结 —
   look 逐字取自圣经角色卡（跨集同脸同装，换装=违约），可以画得稍不像但关键词必须一致；
   动作/朝向/与物件的关系每镜不同。道具按本场道具锁定卡（集级可换）。禁逐字克隆画面句。
5b. 两大逻辑：时间逻辑 — 同场同时代背景，禁古今乱跳；时代变迁是设计事件，必须在画面句
   和 motion 里配标记性转场（渐变/光效/穿门/卷轴翻页…），无标记跳变=违规。实体逻辑 —
   人物 look、道具描述（材质/颜色/尺度）、场景锚关键词跨镜逐字一致（白发黑发互变、
   沙漏变形=违规），一致性靠关键词锚，不靠画得像。
6. 分区宪法（系统注入）优先于你对人物密度的任何习惯：钩子区人物强在场，
   中段概念蒙太奇，案例/引用镜以人物为主体。概念镜的电影化写法 = **人物在概念
   世界中行动**（概念物件是舞台，人物在台上做事/挣扎/顿悟）— MTV手法只约束
   讲述人如歌手般克制出镜，绝不意味着概念场无人；讲述人不当万金油填空镜。
7. 槽位表是时间的事实：只填画面，禁增删并镜、禁输出 t_start/t_end/narration/refs。
8. 每镜首拍 1s 内有可见动作或光效；一镜一事；文字事件至多一个、≤2个汉字。
   每镜设计**一个特效高光时刻**写进 motion（光效爆发/碎片崩落/金色流动/齿轮咬合
   发光/规模揭示…惊艳但不越 H3 物理锁：主体先到位、特效后触发、禁大面积重绘）；
   前 30 秒（特区）特效密度加倍 — 静帧有戏 + 动起来惊艳，缺一半就是没看头。
9. Krea2 渲染容量红线 (管主体不管场景 — 场景满与主体聚焦不矛盾: 满的是纵深, 聚焦的是主角):
   人物=一镜唯一视觉焦点；一主一辅光源；人物≥1/8画面高；
   禁"无数"、禁大场面塞满；可画实体 8-15 个。
10. 全灭字：画面句里禁任何可读文字/数字/招牌（文字只活在 text_layer）；
    建筑服饰为日常民用样式。禁英文混入中文画面句；keyframe_en 为纯英文无文字。
    10b. 图内文字载体一律二段式 (1014 ep3 实锤: 「写满术语的白板/写着'专业技能'的
    便签」全被 K2 渲成错字): 白板/便签/标签/匾额/卡片上有字 = 图内文字 — 画面句必须
    写**空白载体**（"空白白板"/"空白便签"）+ 申报 in_image_text（≤4行）, 由系统出图后
    入字；**禁止「写着XX/字样/模糊字迹」措辞** — K2 渲汉字必错字。文案信息进 text_layer。
    10c. in_image_text 为**每镜必填字段**: 画面句出现 标签/便签/白板/牌匾/匾额/招牌/
    标语/横幅/铭牌/海报/屏幕 等文字载体物件时, 必须申报其文字内容 (≤4行) 并把载体写成
    「空白XX」; 载体无需文字时输出 `"in_image_text": []`, 或干脆不画该物件 — 只画
    「空白标签」而不申报 = 白标签孤儿 (出图后永远无人写字, 实锤废镜)。
    10d. 图内字密度 (1021 用户令, 同日二令升格): 每场 **1-2 镜**主动安排 K2 图内字 —
    **零申报不合法** (出口闸执法); 本场核心概念/标题/金句值得上画, 安排空白载体+申报
    (如「空白白板」+in_image_text ≤4行); 口径是每场各 1-2, 不是全集 1-2 (ep1-7 实况
    全集 0-2 镜 = 口径跑偏实锤); 「干脆不画载体」只适用于本场其余镜, 不得全场逃逸。

## 写法（HBO 级散文范型）
好句长这样：「平视中景，一条发光的青蓝色管道从画面左侧蜿蜒铺向右侧地平线，
一个1988赛璐璐卡通大头老板（大头比、夸张愁眉、领带松垮）在管道近端拼命往前铺新管，
管道沿途有裂缝，金色的水流从裂缝中漏出，管道末端滴落的暖琥珀金币流越来越稀薄，
天青蓝天空与暖米纸白大地为主色，画面无任何文字，固定机位。」
—— 有空间纵深、有主体在做具体的事、有光线现象、有材质、有情绪。
坏句长这样：「全景，平视，罗盘全貌，侧光45度，色温5800K，补光比2:1，指针转动，氛围深邃。」
—— 那是参数表，不是画面。
"""


def writer_blocks(arc: dict[str, Any], zones: list[str],
                  bible: dict[str, Any] | None = None) -> str:
    """writer 模式的用户提示增量: 分区宪法块 (唯一人物观出口) + 五件套输出契约。

    bible 在手时注入观众替身 look (1013 试点实锤: 无替身卡可写 → LLM 只能抓
    角色卡作者 → 围栏拦 → 过矫全空镜 → mid 配额又缺人的死循环)。"""
    blocks = []
    standin_line = ""
    if bible:
        try:
            from .assembler import _standin
            _nm, _look = _standin(bible)
            if _look:
                standin_line = (f"\n【钩子/中段人物优先用观众替身】{(_nm or '职场人物').strip()}"
                                f"（{_look}）— 概念场/钩子场需要人物在场时用它 (姿态/动作/与隐喻"
                                "物件的互动每镜各异); 作者角色只准引用镜出镜。\n")
        except Exception:  # noqa: BLE001 — 无替身退纯宪法块
            pass
    for z in dict.fromkeys(zones):  # 去重保序
        pb = CST.prompt_block(z)
        if pb:
            blocks.append(pb)
        if standin_line and z in ("hook", "mid"):
            blocks.append(standin_line)
            blocks.append("(注意: 角色锁定卡中的作者只准引用镜; 需要人物在场时优先用上面的替身)")
    blocks.append(
        "\n【景深配额·硬校验】(1014 ep4 A4 实锤: 13镜只写2镜大场景连杀两轮 — writer 链此前"
        "无此条款): 本场每 4 镜至少 1 镜为大场景构图 (大全景/俯瞰/辽阔环境, 主体占比小+"
        "参照物显纵深), 禁全场连排特写/中景 — 概念蒙太奇也要拉开景深节奏, 违规整场重掷。\n")
    blocks.append(
        "\n【输出契约 (单写者制, 优先于一切旧格式)】groups[].shots[] 每镜必须含: "
        "\"keyframe_zh\" (200-250字中文散文画面句), \"keyframe_en\" (同构图英文, no text), "
        "\"motion_zh\" (动画层+秒级事件, 只动画面句已有实体), \"motion_en\" (English ambient "
        "+ at most ONE text event), \"text_layer\" (无字镜给 []), "
        "\"in_image_text\" (必填: K2图内字, ≤4行字符串数组, 无字镜给 []; 画面句含"
        "标签/便签/白板/牌匾/招牌/标语/横幅/铭牌/海报/屏幕等载体时必须申报文字内容"
        "并把载体写\"空白\" — 系统二段合成, 漏申报=白标签孤儿废镜); "
        "可选 \"h3_text_event\" (H3事件字: {\"text\":\"≤4字\",\"at_s\":秒,\"zone\":\"落点\"}, 画面句须在落点写留白预留); "
        "camera/scene_tag 照旧。"
        "系统将 opening_desc 逐字取 keyframe_en — 中英两句必须同构。\n")
    return "\n".join(blocks)


def _scene_zone(doc: dict[str, Any], arc: dict[str, Any], shots: list[dict[str, Any]]) -> list[str]:
    """本场跨到的分区 (供宪法注入): 场窗起点定 hook/mid, 寄存器分布定 case/cite。

    规划前调用时 shots=旧场镜 (无则空) — case/cite 分区主要靠 SYS §6 与句型自觉,
    精确钉标在规划后 assign_registers + 终审分区配额兜底。"""
    tc = 0.0
    try:
        from .config import load
        tc = float(load().brand_card.opening_sec)
    except Exception:  # noqa: BLE001
        pass
    zones = []
    a0 = float(arc.get("t_start") or 0)
    zones.append("hook" if a0 - tc < CST.zone_end() else "mid")
    regs = {str(s.get("register") or "") for s in shots}
    if "case_narrative" in regs:
        zones.append("case")
    if "author_cite" in regs:
        zones.append("cite")
    return zones


def writer_failures(doc: dict[str, Any], arc_id: str) -> list[str]:
    """writer 出口闸 (进 fix_hints): 五件套/slot对表/scene_tag/邻花字。

    只查本场 writer 产物 (结构性的当场改), 配额/时间轴归 validate_doc
    (planner 重试网已在量)。"""
    from . import hook_zone as _HZ
    from .director2 import norm_text_layer
    key = str(arc_id).lower()
    shots = sorted((s for s in doc.get("shots") or []
                    if str(s.get("arc_id") or "").lower() == key),
                   key=lambda s: float(s.get("t_start") or 0))
    fails: list[str] = []
    zone_shots = [s for s in shots if s.get("zone")]
    if zone_shots:
        tags = [s.get("scene_tag") for s in zone_shots]
        fails += _HZ.zone_scene_issues(tags)
    prev_texts: set[str] = set()
    for s in shots:
        sid = str(s.get("shot_id"))
        if s.get("brand_card"):
            continue
        zh = str(s.get("keyframe_zh") or "").strip()
        en = str(s.get("keyframe_en") or "").strip()
        mz = str(s.get("motion_zh") or "").strip()
        if s.get("_kf_missing") or not zh:
            fails.append(f"{sid} 缺 keyframe_zh (五件套铁律)")
        if not en:
            fails.append(f"{sid} 缺 keyframe_en (锚定链 — opening_desc 无锚)")
        if not mz:
            fails.append(f"{sid} 缺 motion_zh")
        # motion_en 权威位 = anim.beats[0].motion (合并器不入镜级字段, 实弹实锤);
        # 器层默认占位 ("the scene evolves...") 视同缺失 (试点指标 beats占位=0)
        me = str(s.get("motion_en") or "").strip()
        if not me:
            _anim = s.get("anim") if isinstance(s.get("anim"), dict) else {}
            _beats = _anim.get("beats") if isinstance(_anim.get("beats"), list) else []
            b0 = _beats[0] if _beats and isinstance(_beats[0], dict) else {}
            me = str(b0.get("motion") or "").strip()
        if not me or me.startswith("the scene evolves"):
            fails.append(f"{sid} 缺 motion_en (beats 落占位串)")
        if zh and len(zh) < 80:
            fails.append(f"{sid} 画面句过短 ({len(zh)}字 <80, 参数表不是画面)")
        # 1014 P1: 图内文字载体必须走二段式 (空白载体+in_image_text), 禁 K2 直渲汉字
        # 1015 H 修复①: 动词措辞之外补载体名词盲区 — 「空白标签」类名词措辞同样必须
        # 申报; 缺字段与空列表在 not s.get(...) 上同罪同拦 (缺字段视为 fail)
        if zh and not s.get("in_image_text") and (
                _TEXT_CARRIER_RE.search(zh) or TEXT_CARRIER_NOUN_RE.search(zh)):
            _hit = (TEXT_CARRIER_NOUN_RE.search(zh) or _TEXT_CARRIER_RE.search(zh)).group(0)
            fails.append(f"{sid} 画面句含文字载体「{_hit}」但未申报 in_image_text — 二选一: "
                         "(a)载体需要文字 → 申报 in_image_text (≤4行) 并把载体写成「空白XX」(系统二段合成); "
                         "(b)载体无需文字 → 删除该文字载体物件 (文案信息进 text_layer)")
        # 1015 首验补口 (ep4 A1 实锤): 已申报镜的载体必须写**空白** — 「标签黑字手写体」
        # 类措辞会让一段画字+二段合成双重文字
        if zh and s.get("in_image_text") and re.search(r"黑字|金字|手写体|印刷体|刻着|写着", zh):
            fails.append(f"{sid} 已申报 in_image_text 但画面句载体非空白 (黑字/手写体等) — "
                         "载体改写「空白标签/空白牌面」, 文字由系统二段合成")
        # 1015 H3 战役: motion 散文禁文字 (花字/事件字规格只准走三路由字段)
        if mz and _MOTION_TEXT_RE.search(mz):
            fails.append(f"{sid} motion_zh 泄漏文字描述 ({_MOTION_TEXT_RE.search(mz).group(0)}) — "
                         "H3事件字走 h3_text_event 字段, K2图内字走 in_image_text, 花字走 text_layer; "
                         "动作句删掉全部文字/字体/发光/淡出描述")
        # 1015 动画先行: 画面句禁终态定格 (它必须是 t=0 起点帧)
        if zh and _ENDSTATE_RE.search(zh):
            fails.append(f"{sid} 画面句含终态词 ({_ENDSTATE_RE.search(zh).group(0)}) — "
                         "画面句=动画 t0 起点帧, 改写为动作前一刻的初始状态")
        # 1015 文字路由配对: H3 事件字 → 画面句必须留白预留落区
        _ev = s.get("h3_text_event")
        if isinstance(_ev, dict) and _ev.get("text"):
            if not _RESERVE_RE.search(zh or "") and not _RESERVE_RE.search(en or ""):
                fails.append(f"{sid} h3_text_event 已申报但画面句无留白预留描述 — "
                             f"在落点({_ev.get('zone') or '事件区'})写「留出干净空背景」类描述")
        cur = {norm_text_layer(t.get("text")) for t in (s.get("text_layer") or []) if t.get("text")}
        dup = prev_texts & cur
        if dup:
            fails.append(f"{sid} 相邻镜花字重复 '{'/'.join(sorted(dup)[:3])}'")
        prev_texts = cur
    # 场内逐字克隆 (writer 最忌)
    seen: dict[str, str] = {}
    for s in shots:
        zh = str(s.get("keyframe_zh") or "").strip()
        if zh and not s.get("brand_card"):
            if zh in seen:
                fails.append(f"{s['shot_id']} 画面句与 {seen[zh]} 逐字克隆 (禁)")
            seen[zh] = str(s.get("shot_id"))
    # 1013 构图同源硬闸 (s54/s55 只换景别词实锤): 剥 look 括段后相邻 bigram-Jaccard
    # >0.70 = 换皮克隆 — 只换"平视/空拍"不换主体动作与场面 = 同一镜
    import re as _re
    _strip = lambda x: _re.sub(r"（[^）]*）", "", x or "")
    def _bg(x):
        t = _re.sub(r"[\s，。；、,.:;!?！？]", "", _strip(x))
        return {t[i:i + 2] for i in range(max(0, len(t) - 1))}
    prev = None
    for s in shots:
        zh = str(s.get("keyframe_zh") or "").strip()
        if not zh or s.get("brand_card"):
            continue
        if prev is not None:
            A, B = _bg(prev[1]), _bg(zh)
            j = len(A & B) / len(A | B) if (A | B) else 0.0
            if j > 0.70:
                fails.append(f"{s['shot_id']} 与相邻镜 {prev[0]} 构图同源 (剥look后相似{j:.0%}, "
                             "只换景别词不换主体动作场面=换皮克隆, 重写其一)")
        prev = (str(s.get("shot_id")), zh)
    # 1021 用户令 (二令升格): 每场 1-2 镜图内字, 零申报不合法 — 宪法10d 执法
    _decl = sum(1 for s in shots if not s.get("brand_card") and s.get("in_image_text"))
    if _decl < 1:
        fails.append(f"{arc_id.upper()} 整场零申报 in_image_text — 宪法10d: 每场 1-2 镜"
                     "空白载体+申报 (标题/概念/金句上画), 重写补申报")
    return fails


def dispatch_scene(book_id: str, book_title: str, ep: int, arc_id: str,
                   manifest: dict, bible: dict,
                   fix_hints: list[str] | None = None,
                   old_doc_for_zones: dict | None = None,
                   force_fallback: bool = False) -> dict:
    """三入口统一调度 (planner 消此函数替代 director2.plan_scene 直调)。

    writer on: plan_scene(mode="writer") ×2 (出口闸 fix_hints 回喂), 两败退
    现行链 (mode="struct" + 场内术/器, 打标 fallback)。writer off: 原路直通。
    force_fallback (1014 ep4 A6 实锤): writer 五件套可过但景深配额两轮打不进
    (16槽概念场特写先验, 点名hint对整场重掷无效) — 景深失败的重试直接走
    fallback (法层自带景别节奏, ep3 实证 fallback 场景景深全过), 省一次全量掷。"""
    from . import director2
    if not CST.writer_enabled():
        return director2.plan_scene(book_id, book_title, ep, arc_id, manifest, bible,
                                    fix_hints=fix_hints)
    if force_fallback:
        logger.info("[writer] 场 %s 强制 fallback (景深升级救援)", arc_id.upper())
        return _fallback_scene(book_id, book_title, ep, arc_id, manifest, bible,
                               fix_hints=fix_hints)
    hints = list(fix_hints or [])
    for attempt in (1, 2):
        try:
            doc = director2.plan_scene(book_id, book_title, ep, arc_id, manifest, bible,
                                       fix_hints=hints or None, mode="writer")
            fails = writer_failures(doc, arc_id)
            if not fails:
                for s in doc.get("shots") or []:
                    if str(s.get("arc_id") or "").lower() == str(arc_id).lower():
                        s["writer_source"] = "writer"
                logger.info("[writer] 场 %s ✓ 单写者五件套 (第%d把)", arc_id.upper(), attempt)
                return doc
            hints = fails
            logger.warning("[writer] 场 %s 出口闸 %d 处 (第%d把): %s",
                           arc_id.upper(), len(fails), attempt, "; ".join(fails[:4]))
        except (SystemExit, ValueError) as exc:
            hints = [str(exc)[:200]]
            logger.warning("[writer] 场 %s 失败 (第%d把): %s", arc_id.upper(), attempt, hints[0])
        except Exception as exc:  # noqa: BLE001 — 网络/瞬时同享重试网
            hints = [f"{type(exc).__name__}: {exc}"[:200]]
            logger.warning("[writer] 场 %s 异常 (第%d把): %s", arc_id.upper(), attempt, hints[0])
    # 两败 → 现行链兜底 (场内补寄存器+槽+器, 打标)
    logger.warning("[writer] 场 %s 两把均败 → 退现行链 (fallback, 页面红标)", arc_id.upper())
    doc = _fallback_scene(book_id, book_title, ep, arc_id, manifest, bible,
                          fix_hints=hints or None)
    return doc


def _fallback_scene(book_id: str, book_title: str, ep: int, arc_id: str,
                    manifest: dict, bible: dict, fix_hints: list[str] | None) -> dict:
    """现行链场级兜底: STRUCT 规划 + 场内 寄存器/创意槽/器层组装 (打标 fallback)。

    1014 落盘纪律: 术/器 persist=False — 本函数活在 planner 重试网内, 中间态
    落盘 = 未过闸产物上盘 (旧版实锤: A6 带病 4/16 落盘后, 续跑「有镜即 done」
    够不着, 全局闸无限同错); 落盘权在 planner 每场过闸后的 shots_mod.save。"""
    from . import director2
    from .assembler import assemble_episode
    from .registers import assign_registers
    from .shooting_script import load as load_ss
    doc = director2.plan_scene(book_id, book_title, ep, arc_id, manifest, bible,
                              fix_hints=fix_hints)
    try:
        ss = load_ss(book_title, ep)
        if ss:
            assign_registers(doc, bible)
            from .creative_slots import fill_creative_slots
            key = str(arc_id).lower()
            _sids = {str(s.get("shot_id")) for s in doc.get("shots") or []
                     if str(s.get("arc_id") or "").lower() == key}
            fill_creative_slots(doc, ss, bible=bible, only=_sids, persist=False)
            assemble_episode(doc, ss, bible, only=_sids, persist=False)
    except Exception as exc:  # noqa: BLE001 — 兜底的兜底: 裸奔但显著标识
        logger.warning("[writer] 场 %s fallback 术/器失败 (裸奔标): %s", arc_id.upper(), exc)
    for s in doc.get("shots") or []:
        if str(s.get("arc_id") or "").lower() == str(arc_id).lower():
            s["writer_source"] = "fallback"
    return doc
