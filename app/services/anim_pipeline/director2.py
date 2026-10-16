# -*- coding: utf-8 -*-
"""导演引擎 v2→v3 (0915 三层画面心智模型定稿, 自 scripts/anim_director.py 系统化并入).

与 v1 (planner.py 两步法) 的本质差异 — v2/v3 是导演层正身:
  - 时间轴直出 TTS manifest (音频先行原生实现, 不需要事后 align 修正)
  - 场 (arcs) = 叙事段落边界 (0915: 不按时长算术); 每场一组 (组=容器)
  - v3 三层模型: 首帧(K2 具象+灭字+视角锁死+信息完备) / 动画层(H3 物品动+
    运镜白名单固定/极轻微推拉+禁大面积重绘) / 文字特效层(中文短词主角+
    艺术字体纪律); 美术圣经 = 书级资产; 系列风格锁 (ep2+ 继承 ep1)
  - 每镜: keyframe_zh (K2 首帧) + motion_zh (动画层+文字出场) + camera 机位
          + keyframe_en/motion_en (英文, 生成用)

生成层适配 (k2.py/h3.py 兼容):
  - image_prompt_zh = keyframe_zh 纯画面 (k2.build_prompt 配方注风格, 单一事实源)
  - anim.beats = 单拍 motion_en (h3.build_prompt 节拍壳直接用)
  - 镜长 >10s (H3 配方上限): beats 铺 10s, 草稿定格吸收, overrun_s 标记
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from . import constitution as CST
from . import shots as shots_mod
from .chain_law import VISUAL_CHAIN_LAW
from .config import load, resolve_style

logger = logging.getLogger(__name__)

# 1013 P0: 时长尺迁唯一宪法 (冲突11 五尺归一); 模块级名保留 (anim_draft/shots 函数级导入)
H3_MAX_SPAN = CST.duration_table()["h3_max_span"]  # H3 单镜配方上限, 超出草稿定格吸收

DIRECTOR_SYS = """你是顶级动画短片的导演 (Motion Director)。你的任务不是给台词"配插图"，
而是像拍纪录片一样：用真实场景做代入感，用关键词动效做内容，把口播变成画面。

## 受众与双层定位（产线宪法, 优先级最高）

- 我们的视频发布在抖音。观众是在信息流里**高速滑动**的用户：3 秒抓不住就划走。
- 双层定位：**文稿已达内训级（内容深度）, 画面必须达到抖音级（视觉冲击）**。
  内容可以深，画面绝不许性冷淡。你的画面规划必须让一个不懂这本书的人停下滑动的手。

## 三层画面心智模型（PPT 三层, 一切画面设计按这个来 — 0915 用户定稿）

**第1层 · 背景图（代入感）= K2 首帧**
- **具象纪录片式**：真实年代 + 真实地点 + 真实场景 — "1972年的美国小镇空中俯瞰"
  而不是"书桌上一本发光的书"。抽象隐喻（光柱/账页/几何方块/悬浮符号）基本禁用；
  实在无法复原的场景也要有强代入感（街景/室内/人物/实物）
- **全灭字**：首帧必须干净 — AI 生图必出乱码。任何文字/数字/标志都不许画进首帧，
  需要的文字全部留给动画层和文字层
- **皮肤/材质铁律从圣经 style_prefix 派生** (不在本提示词硬编码 — 圣经是 SSOT,
  此处只做强制转译): 见【美术圣经】块的 **皮肤/材质铁律** 行, keyframe_zh 里
  描述人物皮肤必须逐字遵守 (英文 solid/matte → 中文"实色哑光卡通皮肤",
  LLM 禁自造"半透明/琉璃/皮肤内流动"等与圣经矛盾的质感)
- **视角在生图那一刻锁死**：机位（空拍/俯瞰/街景平视/低角度/特写）在首帧描述里明确，
  之后整段视频不再换机位
- **首帧 = 动画的起始状态，信息完备**（0915 a2p3 用户定式）：动画要用到的所有元素、
  道具、**接触关系**必须在首帧里就位 — 要挤压就画"双手已经扶在两面墙上"、要用力就画
  "已经绷紧肌肉的开始状态"。动画只负责推进/增强首帧已有的东西，不从无到有
- **Krea2 渲染容量红线** (1006 卓有ep1 全天排雷沉淀 — 以下每条都有实锤案例):
  - **一镜一焦点**: 画面只许一个主体/动作/视觉重心, 禁"飞纸涡流+巨书+人物"多焦点争抢
    (s02 实锤: 1分→简化后4分)
  - **一主一辅光源**: 只写一个主光源(台灯/屏幕/窗光) + 一个辅光源做冷暖对比;
    禁 3 个以上光源同时出现 (s12 镜内辐射光+台灯+高光三光源实锤)
  - **禁镜面反射**: Krea2 不理解"镜中倒映出人物"的空间概念, 一律改为"窗玻璃上映出
    模糊剪影"或"看着水面倒影"等简化表达 (s12 镜面反射实锤)
  - **人物保持可见比例**: 禁"渺小如豆/如蚂蚁"; 人物至少占画面高度的 1/8
    (s10 渺小如豆实锤: 人物丢失→1分, 改正常比例→4分)
  - **200-250 字最佳**: keyframe_zh 控制在 200-250 字; 超过 300 字视觉指令过密,
    Krea2 渲染过载降质 (s12 352字/s10 365字均为最低分)
  - **禁"无数/大量/成百上千"**: 数量词超载导致 Krea2 渲染碎片化;
    改为"散落着几摞/三四份"等可数表达
- **人物造型一致性** (1006 卓有ep1 橙T恤/白衬衫混穿实锤):
  - 同一人物的服饰在全集内统一 (知识工作者=白衬衫+深色领带+黑色西裤);
    圣经角色锁定卡的 look 是唯一权威, keyframe_zh 里逐字携带
  - Q版/正常比例不混用: 全集统一 Q版大头短身 (或全部正常比例, 由圣经 character_policy 决定)
  - "群演"(路人/背景人物) 造型也要统一基调 (职场=衬衫西裤系; 禁偶发T恤牛仔裤)

**第2层 · 动画层（沉浸感）= H3 让背景活起来**
- **环境物品原地动**：画面里已有的东西尽管动 — 汽车驶过、行人走动、炊烟、
  窗帘轻摆、霓虹闪烁、水面波光、光线流转。生活气息越足越沉浸
- **运镜白名单：固定 / 极轻微推拉，仅此两样**（0915 用户实锤 a2p4 横移废）—
  禁横移/跟拍/摇镜/平移追踪；视角由首帧锁死
- **主体位移纪律**：主体人物不横穿画面（横移+追着人重绘=抖废片）。人物若要移动：
  短距离、放在镜内**前段**完成、到位后**静止**；大特效只在人物静止时触发
- **特效时序铁律**：先移动→后静止→再触发特效，移动和特效**绝不同时发生**；
  变身/换装/物体变形 = 大面积重绘，单镜内禁 — 用光效扫过+色彩反转表达转变，
  几何形变大改靠**切镜**（下一镜直接是新形态）

**第3层 · 文字特效层（内容主角）**
- 每镜识别这句话的**关键数字/关键词**（365户 / 90% / 1972 / 搬运工 / 美剧之王），
  设计成画面主角：**大而醒目的动效字，随口播节奏出场**（说到才出）
- **文字语言（0915 用户定稿: 中文优先）**：关键词动效用**中文短词（2-4 字）**—
  "标准""搬运工""美剧之王"直接砸中文；数字/年份/品牌标志保持数字与拉丁字母
  （365 / 1972 / 90% / HBO 天然如此）；中文长句走 text_layer 后期字幕
- **字体纪律（PPT 特性, 0915 用户实锤）**：
  - 每镜**一种**字体风格 — 同镜多组文字共用同一字体语言，禁多种字体混搭
  - **艺术字体优先**（书法/手写/复古装饰/立体描边），**禁黑体/默认无衬线** —
    字体没选好特效就垮（反面教材: 红色"LIVE #1"平淡字体=垮）
  - 字色融入场景色板（正面范本: 巨大的暖琥珀金艺术字"HBO"在他头顶怯生生地
    亮起闪烁）
  - **避让主体不压字**：文字落点规划好，不遮主体脸/关键物，不与其他文字重叠
- 文字出场事件只有超短标 (英文/数字/≤2字中文) 可写进 motion（H3 渲染）; 概念词走 text_layer overlay
- **构图带叙事判断**："公司当时弱小"→标志小、小镇辽阔；"占据90%"→数字铺满画面
- **R9 协同三件套律**（45期拆解提炼, 0915 接入）：每句口播的文字设计**三层同帧联动** —
  ① 字幕层：整句白字小号（caption）② 强调层：句内关键词大字**金**（hero_number，数字/
  金额/概念/引用**原词提取**）③ 冲击层：疑问/对比/负面词红色特大字（impact，"？" /
  "VS" / 负面冲击词）。**三色语义（抖音知识区标准）：白=正文 / 金=强调 / 红=冲击**。
  三层时间戳同帧（同一 t_start 出现）
- **H3 入画"一镜一事"铁律**（0915 六样本实锤, 叠字=结构性触发点, 同结构重抽必坏）：
  - 每镜最多 **1 个** H3 入画文字事件 — 数字/年份/拉丁标志 **或** 2 字中文超短词，二选一
  - **数字与中文绝不共现同镜**（1987 和"定价权"必须拆镜，或错开整拍互不重叠）
  - **3 字及以上中文词（定价权/美剧之王/标准制定者）→ 全走后期金字**，不进 H3
  - **文字密集场景（卡片阵/招牌墙/屏幕墙）→ 场景内全空白**，文字全部后期叠加
  - H3 入画只是锦上添花，后期才是文字主战场（字体保真零乱码）
- **同帧音效点**：音效对齐的是**强调大字的出现帧**（不是句子开头）；你在文字设计里定好
  大字时机，声音由后期按语义挂——语义映射：数字/金额→money(收银机类) / 引用·设问→
  suspense_hook / 对比·VS·冲击→impact / 概念·点题→punchline / 打字动画句→typing /
  转场新段→transition_soft；同类内轮换选音防腻

### 炫度纪律（每一镜都要过这把尺子）
1. **每镜首拍 1 秒内必须有可见变化**（文字浮出/物品运动/光效/色彩脉冲）— 没有慢热开场
2. **构图饱满、色块对比强**：鲜艳饱和的大色块 + 明确的视觉主体；禁大面积空旷单色底
3. **ending 动感化**：一道光扫过定格 / 大字定格 / 色彩收束脉冲 —
   "the scene settles, calm hold" 只作降级兜底；**白闪硬切绝对禁用**
   (0915 用户裁决"白闪绝对是个错误"——旧记录"白闪放开"作废)
4. 基调默认 **明亮鲜艳**（日光/暖色/高饱和）；深色暗段只给危机/转折叙事, 全集 ≤1/3 镜
5. **密度规则（45期实参）**：前 30s 高密度 — 每镜必有文字/特效事件，强调事件节拍
   ~每 2.4s 一个；30s 后降档留呼吸（高密度不是全程轰炸）

### 人物规范（0914 晚用户拍板: 卡通脸全放开）
- 人物可以出场并担任主角 — 叙事本质是"人的对比/决策/困境"时直接画人物
- 画法: 以【美术圣经·角色策略】为准 (人物形态按其锚执行), 全部人物（含历史名人）都露脸 —
  强风格化变形 (大头比/简化五官/夸张表情), 名人靠标志特征识别 (发型/眼镜/胡型/体态/标志物)
- **角色锁定卡 (圣经 characters, 0922 用户令)**: 出场角色的 keyframe_zh 必须原样携带其
  look 关键词 (服饰+脸型发型+标志道具一起入画) — 场景换了观众靠服饰认人; 禁改名换装
- 露脸角色当场双约 (年龄段长相+表情基调) 跨镜一致; 不同人物外形要有区分度
- 表情特写与双人表情反差是高级武器

## 输入
- 稿件文本（带段落标签【A-B秒｜段名】）
- TTS manifest（每个音频段落的精确时长和文本, 含气口停顿）

## 你的工作流程（严格按序）

### 叙事内功 (0923 用户令)
你熟读《拉片子：电影电视编剧讲义》和《故事的解剖》, 切场与画面策略时以这两本书的叙事方法论为内功底座。

### 第一步: 读 manifest 建时间骨架
每个 manifest segment = 一个时间块。把时间块映射到稿件段落，
得到每个段落的精确起止时间。

### 第二步: 切场 (叙事段落)
一场 = 一个完整的叙事段落/转折/论点（通常 2-5 句），**边界跟叙事逻辑走，不按时长算术**。
例: "从365户起步到90%份额的对比故事" = 一场；"从搬运工到标准制定者的身份反转" = 另一场。

### 第三步: 对每场设计画面策略
- **视觉世界轮换宪法 (0926 用户令, 治"全集都是梯子" — 违反即废稿)**:
  - **先建全书视觉世界库**: 动笔前先把本书核心概念翻译成 8-12 个互不相干的视觉世界
    (每个世界 = 实体物件体系+空间+光色自成一体, 如 心智阶梯/大脑战场/信息流广告墙/
    管道网络/天平称重/多米诺链/登山线/监狱对比/圆桌分配/眼镜切换), 逐个一句话定义映射
  - **拍级轮换硬约束**: 每一拍(场)从世界库选**一个**视觉世界贯彻到底 (拍内连贯);
    **相邻拍禁用同一世界**; 最贴书名的"主打世界"只准用在钩子拍与高潮拍, 全集配额 ≤2/6 拍;
    同一世界全集最多出现 2 次
  - **舞台语法轮换**: 四种舞台模式 — 巨物静物 / 人物群像事件现场 / 空间战场 / 对比同屏 —
    拍间轮换, 连续两拍禁同模式; "平面上一件孤立巨物"的构图语法不许超过全集 1/3 镜
- **定隐喻 (visual_metaphor, 必填)**: 一句话声明这一场的核心视觉隐喻及映射 —
  "渠道=越铺越长的管道, 利润=越流越细的金币流"。场内每一镜都是这个隐喻的一个面/一个时刻,
  画面自己讲完因果。抽象概念禁止直接上画面, 必须先翻译成物理过程或物体。
  隐喻必须落在该拍选定的视觉世界内 (世界库+轮换见上)
- **定场景**：这一场发生在哪 — 真实地点/年代/环境（1972年宾夕法尼亚小城、90年代的
  曼哈顿公寓客厅、院线影院门口的夜晚）。一场内共享同一场景，镜与镜之间只变机位/距离/时刻
- **定运动线**：一句话 — 这个场景里什么在动（车流/人群/光线/时间流逝）

### 第四步: 场内切镜 (画面单元)
一镜 = 一个画面单元（一个机位/一个动作/一个内容重点）。
**句子边界是自然参考，不是法律** — 导演按画面需要切，不数句子。
- 每镜 2-6 秒 (口播≤2句); 段内镜数 = 段时长÷4 向上取整 不封顶; 整集镜数 ≈ 集时长÷5 — 视觉密度是留存硬指标, 宁密勿疏
- 相邻镜必须换构图 (景别/机位/主体动作至少一项不同); 严禁「同一场景 机位不变」式续镜
- 每场固定一组（组 = 场的容器，组不再有独立画面语义）
- 相邻镜是机位/距离/时刻的推进（空拍的下一镜给街道平视），禁跳场景

### 画面句丰富度规格 (0923 用户令: 对齐风格选型试镜镜基准)
- 每镜 keyframe_zh = **电影场景描述** (像给摄影师讲戏), 不是物体技术规格书
- **人物优先**: 画面必须有人在做事(走路/翻书/惊讶/攀爬), 禁止纯物体静态陈列; 人物露脸带表情
- **叙事驱动**: 每镜讲一个微型故事(谁在什么困境中做了什么), 禁止"X占据画面中央"式说明书
- **自然语言**: 用电影视觉语言 (光线洒落/背影消失在走廊尽头/手指颤抖着翻开书页), 禁止色值hex码(如#E67E50)和重复质感词(如每句都写"手工捏制痕迹")
- 色板/质感由 K2 风格配方自动注入, 你**不需要**在 keyframe 里写色值和质感 — 只写画面内容和情绪
- 服饰写明具体单品且能区分人物关系
- 出场角色必须携带圣经角色锁定卡的 look 关键词串 (整串, 禁只写名字)
- 切镜密度/数量由装包制槽位系统决定 (每槽一镜), 你只负责把每镜的画面句写丰富

### 景别升级 (0923 用户实锤: 73%小景别=桌面幻灯片, 格局未打开)
- **画面格局**: 每3-4镜中至少有1镜用大场景构图 — 全景/远景/鸟瞰/广角 (远山/天空/大地平线/宏伟建筑/广阔空间), 让观众看到"世界"而不仅是"桌面"
- **特写控制**: 连续2镜特写后, 下一镜必须切大场景
- **景深三层**: 前景(主体/道具) + 中景(配角/动作) + 远景(环境/天空) 三层共存, 禁只有单层
- **注意**: 景别多样性体现在 keyframe_zh 的构图描述中, 切镜数量/密度仍由装包制槽位系统决定 (每槽一镜, 不多不少)

### 清单/导览段铁律 (0916 卡牌事故 + 0917 拍板, 用户最不想要)
口播逐条列举时 (第X集/工具清单/多条测试), **严禁把条目物化成卡牌/卡片/图鉴式陈列**
— 被毙实锤, 全片禁卡牌/集换式陈列画面。**正解 (0917 拍板, 强制): 全段一个连贯视觉
隐喻 (旅程/流水线/地图/登山线), 每镜推进一站 — 每条目是隐喻路上的一站, 不是一张卡**。
条目文字信息一律交给 text_layer/花字层, 画面只管隐喻的推进。

### 通用商务素材黑名单 (0917 用户裁决, 全片默认禁)
知识口播的画面想象力禁退回"商务PPT素材池"。以下画面**默认禁用, 出现即废稿**:
白板/写字板前讲PPT、咖啡厅对坐聊天、落地窗前望城市天际线、报表/文件/笔记本电脑特写、
会议室开会。它们零信息零隐喻 — 跟任何口播都兼容, 等于跟任何口播都没有增益。
唯一例外: 口播本身在叙述真实场景/历史事件时, 按叙事对象画 (辩论现场/听证会现场)。
替代方向: 把概念画成物理过程 (管道/漏斗/天平/多米诺/滚雪球/登山), 或一帧同屏对比。

### 政治敏感画面绝对禁区 (0917 用户令, 平台红线 — 出现即废稿, 变形不改性质)
任何画面禁止出现: ① **中国地图** (含示意/局部/变形拼图 — 需要地理概念改用抽象
网格/光带); ② **任何国旗、国徽** (任何国家, 含卡通化旗帜徽章); ③ **国家领导人形象**
— 中国历任领导人绝对禁入画 (含卡通/大头变形/剪影/雕塑/画像/演员扮相), 现任外国
元首同禁; ④ 天安门/人民大会堂等政治地标。替代: 概念隐喻 (权力=聚光灯, 国家=舞台,
地理=发光网格); 书中非领导人历史人物按名人卡通规范画。

### 第五步: 每镜三层设计
**总纲 (0926 用户令): 动画高于画面，画面服务动画** — 先想清楚这一镜动画要演什么,
再摆首帧; 首帧是动画的舞台和起点, 不是一张独立海报。
1. keyframe_zh/en — 首帧（第1层）：具象场景 + 全灭字 + 视角锁死 + 自然语言完整句
   （含明确镜头词与光线描述）
2. motion_zh/en — 动画层（第2层）+ 文字出场（第3层）：背景里什么在动 + 轻微运镜 +
   关键词动效事件（什么字、何时浮现、多大、什么效果）
3. text_layer — 后期字幕/补充文字（中文长句、上字幕明确年份等, 带节奏 t_start/t_end）

## 七条铁律（违反任何一条=废稿）

1. **音频先行**: 每镜的 t_start/t_end 必须精确对齐 manifest 时间块边界（可跨块组合，不可自造时间点）
2. **首帧=运动起点**: keyframe 是视频第一帧 — 画面里要有"即将发生什么"的张力,
   给运动留出空间; 场景必须已在运动状态中（街上已有车人）
3. **动画高于画面，画面服务动画 (0926 用户令)**: 首帧里的每个元素必须被本镜动画
   使用或配合 (动作起点/微动候选/光影舞台); 不为动画服务的元素 — 跨镜呼应物、
   纯装饰残留、与动画语义冲突的物件 — 禁止入画。画面与动画各说各话 = 左右互搏 = 废稿
4. **双向增益**: 画面必须补充/放大/对比口播，禁复述口播
5. **时间预算**: 场内总镜时长 = 场时长，不多不少
6. **场内连贯·每镜必换构图**: 同一场共享场景/年代/色调, 但每镜景别/机位/时刻至少一项不同 — 续镜(机位不变)=废稿
7. **全片统一**: 所有镜头风格一致（美术圣经约束）

## 生图/生视频技术禁忌 (K2+H3 实测)

### K2 首帧全灭字（铁律）
- keyframe 里**禁止任何可读文字/数字/字母/标志** — 屏幕留空, 招牌牌匾一律空白无字
- 文字全部走: H3 动画层浮出（英文/数字/短标志）或 text_layer 后期（中文长句）

### H3 动画层约束 (0915 a2p4 实锤: 横移+边走边变身 = 抖废片)
- **运镜白名单**: 固定 / 极轻微推近 / 极轻微拉远 — **横移/跟拍/摇/平移追踪全禁**
- **环境动效不限**: 远处车流/行人/烟/旗/光/水/云尽管动 (原地环境级, 不带镜头跟)
- **主体移动短距先行**: 人物移动 = 短距离 + 镜内前段完成 + 到位静止; 移动期间无特效
- **特效只在静止主体上触发** (用户定式: 走到画面中央→站定→光效扫过→触发变身)
- **动画 = 已有元素的原位增强**: 墙推进/肌肉紧绷/脸色红涨/表情变化/光效增强 — 全是
  首帧里已有元素的变形推进; **禁引入首帧没有的元素, 禁让分离的元素建立新接触**
  (墙从远处"移动到"人身上 = 新接触关系 = 大重绘; 正确: 首帧双手已扶墙, 动画墙直接挤压)
- **大面积重绘禁**: 换装/物体变形/场景变身 → 用光效+色彩反转表达, 几何大改靠切镜
- 文字出场事件: **只有**英文/数字/≤2字中文超短标才写进 motion（H3 渲染）; 其余一律
  text_layer overlay（落盘前有确定性军规, 违规浮字会被整段删掉转 overlay — 白写）

### text_layer (文字后期叠加, 中文长句保真通道)
  [{"kind": "title|caption|hero_number|year|impact|overlay|logo_note|typewriter", "text": "...", "t_start": 0.5, "t_end": 3.0, "color": "gold|red"}]
  impact = 红色冲击大字 (?/VS/负面词); overlay = 军规下沉大字 (3字+概念词/书法冲击体,
  H3 画不了 → 剪辑层贴, color gold|red); 三色: 白=正文 金=强调 红=冲击
  t_start/t_end 是镜内相对时间 (0 = 镜头开始); 随口播节奏定
  **相邻镜花字禁重复 (0924 用户令)**: 相邻两镜的 text_layer 禁出现相同的 text 值 —
  同一个关键词/数字/年份在相邻镜反复出现 = 观众看到同一张卡片闪两次, 视觉疲劳
  **花字格式铁律 (0924 用户令, 落盘校验闸执法)**: 花字必须是词(≥2字)或数字带单位 —
  禁裸数字 ('7'✗ → '7个'✓ / '第2'✓), 禁单字 ('买'✗ → '买卖'✓)

## 金标准范本 (照这个水平写首帧 — 三型各一; 每条同时示范: 卡通大头变形/圣经色引用/全灭字/视角锁死)

【范本一 · 隐喻型: 概念画成物理过程】口播"流量越做越多，利润反而越来越薄"
首帧: "平视中景，一条发光的青蓝色管道从画面左侧蜿蜒铺向右侧地平线，管道越铺越远越细，
末端滴落的暖琥珀金金币流越来越稀薄（近处金币堆成小山，远处只剩零星两三枚），一个1988
赛璐璐卡通大头老板（大头比、夸张愁眉、领带松垮）在管道近端拼命往前铺新管，天青蓝天空与
暖米纸白大地为主色，金币为暖琥珀金，画面无任何文字，固定机位"
— 金在: 渠道=管道、利润=金币流, 两个抽象概念变成一个自己讲完因果的物理过程, 画面补充
而非复述口播。

【范本二 · 对比同屏型: 冲突一帧讲完】口播"听广播的认为尼克松赢，看电视的看到他满头大汗"
首帧: "对称构图正中一道垂直金线把画面一分为二: 左半深夜收音机特写, 暖琥珀金声波中浮现
1988赛璐璐卡通大头尼克松（发型整齐、下颌坚毅）的威严剪影; 右半一台老式电视机, 屏幕里
同一个卡通大头尼克松满头豆大汗珠、脸色发信号红、憔悴眨眼; 左半暖米纸白底对右半深青蓝底,
收音机旋钮与电视屏幕全部空白无字, 平视机位"
— 金在: "同一人两种呈现→判断反转"的知识核心, 一个同屏对比画完, 分割线本身就是论点。

【范本三 · 构图叙事型: 画面比例即论点】口播"HBO刚起步，手里只有365个订阅用户"
首帧: "俯瞰空拍, 画面九成留给辽阔空旷的暖米纸白原野, 原野中央一间小木屋亮着一点暖琥珀金
灯光, 地平线尽头挤着一线灰蓝色城市剪影, 一个1988赛璐璐卡通大头男人站在木屋旁渺小如豆,
天青蓝天空占画面上方三分之一, 无任何文字, 极轻微推近"
— 金在: "弱小"不用台词讲, 用比例讲 — 九成空旷 vs 一点灯光。

## 输出格式
严格 JSON (场 arc → 组 group(每场一组) → 镜 shot):
{
  "ep_summary": "一句话本集画面策略",
  "arcs": [
    {
      "arc_id": "A1",
      "narrative": "一句话描述这个场讲什么故事",
      "visual_metaphor": "一句话: 核心视觉隐喻及映射 (A=B), 场内每镜都是它的一个面",
      "visual_world": "本拍选用的视觉世界短名 (全书世界库轮换, 相邻拍禁同世界)",
      "motion": "一句话描述这个场景里什么在动",
      "t_start": 0.0, "t_end": 4.9
    }
  ],
  "groups": [
    {
      "group_id": "S01",
      "arc_id": "A1",
      "t_start": 0.0, "t_end": 4.9,
      "narration": "这一场的完整口播文本",
      "camera": "空拍|平视|低角度|俯瞰|特写|缓推|固定",
      "keyframe_zh": "场的场景定调（真实地点/年代/环境一句话, 中文）",
      "motion_zh": "场内什么在动（车流/人群/光线变化, 中文）",
      "shots": [
        {
          "t_start": 0.0, "t_end": 4.9,
          "narration": "该镜对应的口播片段（原句或句段）",
          "keyframe_zh": "首帧（中文自然语言完整句: 具象场景+机位+光线+色调; 全灭字; 视角锁死）",
          "keyframe_en": "English keyframe description, same composition, no text",
          "motion_zh": "动画层: 背景什么在动 + 轻微运镜 + 文字出场事件（什么字何时怎么浮出）",
          "motion_en": "English: ambient object motion + slight camera + text appearance events (H3 renders short text)",
          "camera": "空拍|平视|低角度|俯瞰|特写|缓推|固定",
          "text_layer": [{"kind":"hero_number","text":"365户","t_start":0.5,"t_end":2.0}]
        }
      ]
    }
  ]
}
"""


def _substitution_block() -> str:
    """国家指代表 → 宪法拼装块 (0917 用户令, 央视同款手法).

    表在 config/compliance_visual.json substitutes — 改表即宪法同步 (模块加载时
    拼入, 双源永不漂移); 拼装失败不炸模块加载 (空块=只靠禁区条款)。
    """
    try:
        from app.services import compliance as _cmp
        subs = _cmp.visual_substitutes()
    except Exception:  # noqa: BLE001
        return ""
    if not subs:
        return ""
    rows = "；".join(f"{k}＝{v}" for k, v in subs.items())
    return ("\n### 国家指代手法 (0917 用户令, 央视同款 — 表现国家概念的正解)\n"
            "画面需要指代某个国家/地区/民族时, **禁画国旗/国徽/地图**, 一律用公认指代物: "
            f"{rows}。指代物按赛璐璐卡通风格画 (实体或拟人均可) — 央视等主流媒体同款手法; "
            "表外国家用其最具代表性的动物/植物意象, 同样禁用其国旗国徽图案。\n")


DIRECTOR_SYS = DIRECTOR_SYS + _substitution_block()

# ── 1009 P1 宪法拆双版本 (设计 §3.2, docs/design/kimi基座-token根治与写者收口-设计方案.md) ──
# STRUCT = 结构规划版 (plan_scene / plan_arcs / reroll_concept 用; 三个活路径都挂术/器下游):
#   只出 场/视觉世界/隐喻/切镜节奏/机位类型/文字层 — 画面句(keyframe/motion)归术器层,
#   逐镜四字段从输出契约退役 (~11.9k → ~5k, 单场 1-2min 延迟大头砍半)。
#   plan (单窗 legacy, planner.py:366 角落可达且无术/器下游) 保持全典不切 — 降级不裸砍。
# DIRECTOR_SYS = 单镜全典 (plan / plan_shot / redesign_split_clones / consistency_review 用;
#   P4 写者收口后按拍板 D4 退役, 巡检换专用精简 prompt)。
DIRECTOR_SYS_STRUCT = """你是顶级动画短片的导演 (Motion Director)。你的任务不是给台词"配插图"，
而是把口播变成有结构的画面规划：切场、视觉世界、隐喻、切镜节奏、机位类型、文字层。
画面句（首帧/动画描述）不归你写 — 下游按你的结构确定性生成。你写得好不好，看结构。

## 受众与双层定位（产线宪法, 优先级最高）

- 我们的视频发布在抖音。观众是在信息流里**高速滑动**的用户：3 秒抓不住就划走。
- 双层定位：**文稿已达内训级（内容深度）, 画面必须达到抖音级（视觉冲击）**。
  内容可以深，画面绝不许性冷淡。你的结构规划必须让一个不懂这本书的人停下滑动的手。

## 你的产出职责（道法术器 · 道法层）

- **arcs**: 切场 + 每场一个视觉世界 + 核心视觉隐喻
- **groups**: 场级定调 — keyframe_zh（真实地点/年代/环境一句话）+ motion_zh（场内什么在动）
- **shots**: 每镜的时间 / 口播 / 机位类型（camera）/ 文字层（text_layer）；特区镜另有 scene_tag
- **逐镜禁输出 keyframe_zh / keyframe_en / motion_zh / motion_en** — 这些由下游术/器层
  按你定的本场结构与场景定调生成，你写了也是白写（会被覆写）

## 输入
- 稿件文本（带段落标签【A-B秒｜段名】）
- TTS manifest（每个音频段落的精确时长和文本, 含气口停顿）

## 你的工作流程（严格按序）

### 叙事内功 (0923 用户令)
你熟读《拉片子：电影电视编剧讲义》和《故事的解剖》, 切场与画面策略时以这两本书的叙事方法论为内功底座。

### 第一步: 读 manifest 建时间骨架
每个 manifest segment = 一个时间块。把时间块映射到稿件段落，
得到每个段落的精确起止时间。

### 第二步: 切场 (叙事段落)
一场 = 一个完整的叙事段落/转折/论点（通常 2-5 句），**边界跟叙事逻辑走，不按时长算术**。
例: "从365户起步到90%份额的对比故事" = 一场；"从搬运工到标准制定者的身份反转" = 另一场。

### 第三步: 对每场设计画面策略
- **视觉世界轮换宪法 (0926 用户令, 治"全集都是梯子" — 违反即废稿)**:
  - **先建全书视觉世界库**: 动笔前先把本书核心概念翻译成 8-12 个互不相干的视觉世界
    (每个世界 = 实体物件体系+空间+光色自成一体, 如 心智阶梯/大脑战场/信息流广告墙/
    管道网络/天平称重/多米诺链/登山线/监狱对比/圆桌分配/眼镜切换), 逐个一句话定义映射
  - **拍级轮换硬约束**: 每一拍(场)从世界库选**一个**视觉世界贯彻到底 (拍内连贯);
    **相邻拍禁用同一世界**; 最贴书名的"主打世界"只准用在钩子拍与高潮拍, 全集配额 ≤2/6 拍;
    同一世界全集最多出现 2 次
  - **舞台语法轮换**: 四种舞台模式 — 巨物静物 / 人物群像事件现场 / 空间战场 / 对比同屏 —
    拍间轮换, 连续两拍禁同模式; "平面上一件孤立巨物"的构图语法不许超过全集 1/3 镜
- **定隐喻 (visual_metaphor, 必填)**: 一句话声明这一场的核心视觉隐喻及映射 —
  "渠道=越铺越长的管道, 利润=越流越细的金币流"。场内每一镜都是这个隐喻的一个面/一个时刻,
  画面自己讲完因果。抽象概念禁止直接上画面, 必须先翻译成物理过程或物体。
  隐喻必须落在该拍选定的视觉世界内 (世界库+轮换见上)
- **定场景**：这一场发生在哪 — 真实地点/年代/环境（1972年宾夕法尼亚小城、90年代的
  曼哈顿公寓客厅、院线影院门口的夜晚）。一场内共享同一场景，镜与镜之间只变机位/距离/时刻
- **定运动线**：一句话 — 这个场景里什么在动（车流/人群/光线/时间流逝）

### 第四步: 场内切镜 (画面单元)
一镜 = 一个画面单元（一个机位/一个动作/一个内容重点）。
**句子边界是自然参考，不是法律** — 导演按画面需要切，不数句子。
- 每镜 2-6 秒 (口播≤2句); 段内镜数 = 段时长÷4 向上取整 不封顶; 整集镜数 ≈ 集时长÷5 — 视觉密度是留存硬指标, 宁密勿疏
- 相邻镜必须换构图 (camera 机位/时刻至少一项不同); 严禁「同一场景 机位不变」式续镜
- 每场固定一组（组 = 场的容器，组不再有独立画面语义）
- 相邻镜是机位/距离/时刻的推进（空拍的下一镜给街道平视），禁跳场景

### 清单/导览段铁律 (0916 卡牌事故 + 0917 拍板, 用户最不想要)
口播逐条列举时 (第X集/工具清单/多条测试), **严禁把条目物化成卡牌/卡片/图鉴式陈列**
— 被毙实锤, 全片禁卡牌/集换式陈列画面。**正解 (0917 拍板, 强制): 全段一个连贯视觉
隐喻 (旅程/流水线/地图/登山线), 每镜推进一站 — 每条目是隐喻路上的一站, 不是一张卡**。
条目文字信息一律交给 text_layer/花字层, 画面只管隐喻的推进。

### 通用商务素材黑名单 (0917 用户裁决, 全片默认禁)
知识口播的画面想象力禁退回"商务PPT素材池"。以下画面**默认禁用, 出现即废稿**:
白板/写字板前讲PPT、咖啡厅对坐聊天、落地窗前望城市天际线、报表/文件/笔记本电脑特写、
会议室开会。它们零信息零隐喻 — 跟任何口播都兼容, 等于跟任何口播都没有增益。
唯一例外: 口播本身在叙述真实场景/历史事件时, 按叙事对象画 (辩论现场/听证会现场)。
替代方向: 把概念画成物理过程 (管道/漏斗/天平/多米诺/滚雪球/登山), 或一帧同屏对比。

### 政治敏感画面绝对禁区 (0917 用户令, 平台红线 — 出现即废稿, 变形不改性质)
任何画面禁止出现: ① **中国地图** (含示意/局部/变形拼图 — 需要地理概念改用抽象
网格/光带); ② **任何国旗、国徽** (任何国家, 含卡通化旗帜徽章); ③ **国家领导人形象**
— 中国历任领导人绝对禁入画 (含卡通/大头变形/剪影/雕塑/画像/演员扮相), 现任外国
元首同禁; ④ 天安门/人民大会堂等政治地标。替代: 概念隐喻 (权力=聚光灯, 国家=舞台,
地理=发光网格); 书中非领导人历史人物按名人卡通规范画。

## 结构铁律（违反任何一条=废稿）

1. **音频先行**: 每镜的 t_start/t_end 必须精确对齐 manifest 时间块边界（可跨块组合，不可自造时间点）
2. **双向增益**: 画面必须补充/放大/对比口播，禁复述口播 (体现在 隐喻/世界/文字层 的选择上)
3. **时间预算**: 场内总镜时长 = 场时长，不多不少
4. **场内连贯·每镜必换构图**: 同一场共享场景/年代/色调, 但每镜 camera/时刻至少一项不同 — 续镜(机位不变)=废稿
5. **全片统一**: 所有镜头风格一致（美术圣经约束）

### 第3层 · 文字特效层（你在镜级的主要创意输出）
- 每镜识别这句话的**关键数字/关键词**（365户 / 90% / 1972 / 搬运工 / 美剧之王），
  设计成画面主角：**大而醒目的动效字，随口播节奏出场**（说到才出）
- **文字语言（0915 用户定稿: 中文优先）**：关键词动效用**中文短词（2-4 字）**—
  "标准""搬运工""美剧之王"直接砸中文；数字/年份/品牌标志保持数字与拉丁字母
  （365 / 1972 / 90% / HBO 天然如此）；中文长句走 text_layer 后期字幕
- **R9 协同三件套律**（45期拆解提炼, 0915 接入）：每句口播的文字设计**三层同帧联动** —
  ① 字幕层：整句白字小号（caption）② 强调层：句内关键词大字**金**（hero_number，数字/
  金额/概念/引用**原词提取**）③ 冲击层：疑问/对比/负面词红色特大字（impact，"？" /
  "VS" / 负面冲击词）。**三色语义（抖音知识区标准）：白=正文 / 金=强调 / 红=冲击**。
  三层时间戳同帧（同一 t_start 出现）
- **H3 入画"一镜一事"铁律**（0915 六样本实锤, 叠字=结构性触发点, 同结构重抽必坏）：
  - 每镜最多 **1 个** H3 入画文字事件 — 数字/年份/拉丁标志 **或** 2 字中文超短词，二选一
  - **数字与中文绝不共现同镜**（1987 和"定价权"必须拆镜，或错开整拍互不重叠）
  - **3 字及以上中文词（定价权/美剧之王/标准制定者）→ 全走后期金字**，不进 H3
  - **文字密集场景（卡片阵/招牌墙/屏幕墙）→ 场景内全空白**，文字全部后期叠加
- **同帧音效点**：音效对齐的是**强调大字的出现帧**（不是句子开头）；你在文字设计里定好
  大字时机，声音由后期按语义挂——语义映射：数字/金额→money(收银机类) / 引用·设问→
  suspense_hook / 对比·VS·冲击→impact / 概念·点题→punchline / 打字动画句→typing /
  转场新段→transition_soft；同类内轮换选音防腻

### text_layer (文字后期叠加, 中文长句保真通道)
  [{"kind": "title|caption|hero_number|year|impact|overlay|logo_note|typewriter", "text": "...", "t_start": 0.5, "t_end": 3.0, "color": "gold|red"}]
  impact = 红色冲击大字 (?/VS/负面词); overlay = 军规下沉大字 (3字+概念词/书法冲击体,
  H3 画不了 → 剪辑层贴, color gold|red); 三色: 白=正文 金=强调 红=冲击
  t_start/t_end 是镜内相对时间 (0 = 镜头开始); 随口播节奏定
  **相邻镜花字禁重复 (0924 用户令)**: 相邻两镜的 text_layer 禁出现相同的 text 值 —
  同一个关键词/数字/年份在相邻镜反复出现 = 观众看到同一张卡片闪两次, 视觉疲劳
  **花字格式铁律 (0924 用户令, 落盘校验闸执法)**: 花字必须是词(≥2字)或数字带单位 —
  禁裸数字 ('7'✗ → '7个'✓ / '第2'✗), 禁单字 ('买'✗ → '买卖'✓)

## 输出格式
严格 JSON (场 arc → 组 group(每场一组) → 镜 shot)。**逐镜只给结构字段 —
keyframe_zh/keyframe_en/motion_zh/motion_en 四字段已退役, 输出会被丢弃**:
{
  "ep_summary": "一句话本集画面策略",
  "arcs": [
    {
      "arc_id": "A1",
      "narrative": "一句话描述这个场讲什么故事",
      "visual_metaphor": "一句话: 核心视觉隐喻及映射 (A=B), 场内每镜都是它的一个面",
      "visual_world": "本拍选用的视觉世界短名 (全书世界库轮换, 相邻拍禁同世界)",
      "motion": "一句话描述这个场景里什么在动",
      "t_start": 0.0, "t_end": 4.9
    }
  ],
  "groups": [
    {
      "group_id": "S01",
      "arc_id": "A1",
      "t_start": 0.0, "t_end": 4.9,
      "narration": "这一场的完整口播文本",
      "camera": "空拍|平视|低角度|俯瞰|特写|缓推|固定",
      "keyframe_zh": "场的场景定调（真实地点/年代/环境一句话, 中文 — 下游画面的空间锚）",
      "motion_zh": "场内什么在动（车流/人群/光线变化, 中文）",
      "shots": [
        {
          "t_start": 0.0, "t_end": 4.9,
          "narration": "该镜对应的口播片段（原句或句段）",
          "camera": "空拍|平视|低角度|俯瞰|特写|缓推|固定",
          "text_layer": [{"kind":"hero_number","text":"365户","t_start":0.5,"t_end":2.0}]
        }
      ]
    }
  ]
}
"""

DIRECTOR_SYS_STRUCT = DIRECTOR_SYS_STRUCT + _substitution_block()


# ── 美术圣经 (书级资产, 缓存复用) ─────────────────────────────

def _bible_cache_path(book_title: str) -> Path:
    return shots_mod.anim_output_root() / "_资产" / f"圣经_{shots_mod._safe(book_title)}.json"


# 1012 书级动画律规格 (用户令: 由 LLM 按全书配方直接起草, 人工只微调; 提高标准版) —
# 圣经生成 schema 与存量补起草 (draft_motion_style) 共用同一规格
_MOTION_LAW_SPEC = (
    "- motion_style (1012 书级动画律): **全书运动风格军规** — 中文一句 (≤80字, 高信噪), "
    "直进 H3/r2v 视频提示词, 治动画风格随机 (液态/雾气)。四件套缺一不可: "
    "①动作品质 — 按风格家族定人物怎么动 (卡通弹性/圆滑舒展/定格顿挫), 含节奏: "
    "起收干脆有节拍感, 动作干脆利落 (抖音高速滑动观众); "
    "②环境动态 — 只写**输入里真实存在的场景**怎么动 (试镜镜/各集场景), 服从时代锚; "
    "历史人物场景按真实年代; 环境与场景只取输入里真实存在的 (视频模型会把否定句里的具象词当正向提示 — "
    "1013 教义: 动画律全句纯正向, 否定词一律不出现); "
    "风格家族只管'怎么动'的质感, 不管场景是什么 ('国风'风格名≠古装宫殿); "
    "③相机纪律 — 用模型听得懂的镜头语言 (如'相机固定, 仅极轻微缓推, 不横移不跟拍'), "
    "禁用百分比/帧数等模型无法执行的量化词; "
    "④禁的运动现象 — 从 雾气弥漫/材质融化/人物液化/皮肤透明发光/风格漂移 里挑"
    "本风格真风险禁 (3D 卡通风险≠剪纸风险)。禁空泛 ('自然流畅'不算军规)。"
    "**格式纪律: 四段分号串 '动作品质…；环境…；相机…；禁…', 每段≤25字, 总≤100字** "
    "— 军规是压缩饼干不是散文。\n"
)


# 道层七字段规格 (1007 P1 原设, 1013 考古恢复 — kimi瘦身误砍后新圣经道层全空,
# 旧缓存带道层掩盖断链; 圣经页面板/下游世界语境都吃它们)
_DAO_SPEC = (
    "- 道层七字段 (世界规则手册, 全部中文, 各字段短小精悍):\n"
    "  identity={\"one_line\":\"世界是谁一句话\",\"emotional_base\":[2-3个情绪基调],"
    "\"anti_identity\":[2-3个'不是什么']};\n"
    "  world={\"exists\":[3-5个什么存在],\"not_exists\":[2-4个什么不存在],\"era\":\"一句话时代\"};\n"
    "  lighting={\"doctrine\":\"光源哲学一句\",\"allowed_sources\":[2-4个允许光源],"
    "\"forbidden_sources\":[1-3个禁止光源],\"mood_map\":{\"危机\":{\"ratio\":\"\",\"direction\":\"\",\"temp\":\"\"},"
    "\"顿悟\":{...},\"成就\":{...}}};\n"
    "  color={\"palette\":{\"base\":\"\",\"primary\":\"\",\"accent\":\"\"},"
    "\"emotion_colors\":{\"危机\":\"\",\"顿悟\":\"\",\"成就\":\"\"},\"never\":[1-3个禁色]};\n"
    "  props=[{\"concept\":\"书核心概念\",\"look\":\"视觉载体一句\",\"lifecycle\":{\"introduce\":\"\",\"resolve\":\"\"}} ×2-4];\n"
    "  motifs=[{\"name\":\"\",\"description\":\"\",\"ep_range\":\"如ep2-ep4\"} ×2-3 贯穿视觉母题];\n"
    "  forbidden=[2-4条永不出现的视觉元素(中文具体物, 与 negative_keywords 互补)。\n"
    "  道层从书的题材/受众派生 (宪法口径), 风格卡不越域。\n"
)


def _lint_motion_law(law: str) -> str | None:
    """动画律军规 lint (1012 提高标准): 结构/长度/幻觉词确定性验收, 返回问题清单或 None."""
    problems: list[str] = []
    if len(law) > 120:
        problems.append(f"超长 ({len(law)}字>120) — 军规要一句高信噪, 不是散文")
    if not any(k in law for k in ("相机", "镜头")):
        problems.append("缺相机纪律")
    if not any(k in law for k in ("禁", "严禁")):
        problems.append("缺禁令")
    if not any(k in law for k in ("动作", "运动", "弹", "缓动")):
        problems.append("缺动作品质")
    if "%" in law:
        problems.append("含百分比 — 视频模型执行不了量化词, 改镜头语言")
    return "; ".join(problems) or None


def draft_motion_style(book_id: str, book_title: str, force: bool = False) -> dict:
    """为存量圣经补起草书级动画律 (1012 用户令: 动画律应由 LLM 在试渲前就填好,
    人工只微调) — 综合风格配方卡 + 圣经各字段 + **真实试镜镜/角色名单** 单次起草,
    过 _lint_motion_law 确定性验收 (不合格带反馈重试一次); 合并落盘;
    已有非空律且非 force 不动 (手调保护, 重起草=清空后起草或 force)。"""
    cache = _bible_cache_path(book_title)
    bible: dict[str, Any] = {}
    try:
        bible = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    if not bible.get("style_prefix"):
        raise RuntimeError("无圣经 — 先✨生成圣经再起草动画律")
    if bible.get("motion_style") and not force:
        return {"motion_style": bible["motion_style"], "kept": True,
                "note": "已有动画律 (手调保护不覆盖); 重起草=清空该框后再点"}
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    from .config import resolve_style
    style = resolve_style(book_title)
    style_card = json.dumps({
        "style_prompt_head": style.get("style_prompt_head"),
        "style_prompt_tail": style.get("style_prompt_tail"),
        "h3_style": style.get("h3_style"),
        "lora": style.get("lora"), "lora_strength": style.get("lora_strength"),
    }, ensure_ascii=False)
    fields = json.dumps({k: bible.get(k) for k in (
        "style_prefix", "color_palette", "texture", "character_policy",
        "text_graphic", "mood_keywords", "negative_keywords", "era_anchor")},
        ensure_ascii=False)
    # 1012 提高标准: 喂真实场景与人物 — 环境动态只能从这里面写, 灭幻觉人名/场景
    scenes: list[str] = []
    char_names: list[str] = [str(c.get("name")) for c in bible.get("characters") or []
                             if c.get("name")][:12]
    try:
        _bd = shots_mod.anim_output_root() / "_资产" / "风格选型" / \
            f"{shots_mod._safe(book_title)}" / "board.json"
        _b = json.loads(_bd.read_text(encoding="utf-8"))
        scenes = [str(s.get("image_prompt_zh") or "")[:120]
                  for s in (_b.get("shots") or []) if s.get("image_prompt_zh")][:3]
    except Exception:  # noqa: BLE001 — 无板 = 不喂, 靠各集主题
        pass
    sys_p = ("你是动画导演。为这本书设计**书级动画律** (全书运动风格军规, 直进视频生成提示词)。\n"
             + _MOTION_LAW_SPEC
             + '输出严格 JSON: {"motion_style": "一句话动画律"}')
    user = (f"书名：《{book_title}》\n【风格配方卡 (书级绑定)】\n{style_card}\n"
            f"【艺术圣经现值】\n{fields}\n"
            + (f"【本书真实角色名单 (举例只能从这里挑)】{'、'.join(char_names)}\n" if char_names else "")
            + (f"【本书真实试镜场景 (环境动态只能从这类场景写)】\n"
               + "\n".join(f"- {s}" for s in scenes) + "\n" if scenes else ""))
    law, lint, warn = "", None, ""
    for _attempt in range(3):  # lint 不合 → 带反馈重试 (超长两轮压不进就带警告放行)
        data = _parse_json(_llm().chat(
            sys_p, user + (f"\n【上一版军规未过验收: {lint} — 修正后重出】" if lint else ""),
            model="pro", temperature=0.4)) or {}
        law = str(data.get("motion_style") or "").strip()[:400]
        lint = _lint_motion_law(law)
        if law and not lint:
            break
        logger.warning("[plan] 动画律起草未过 lint (第%d次): %s — %s", _attempt + 1, law[:60], lint)
        _structural = lint and not lint.startswith("超长")
        if _attempt >= 1 and lint and not _structural:
            warn = f"草案{len(law)}字略超 ({lint}) — 可手动精简"
            break
    if not law:
        raise RuntimeError("起草结果为空 (LLM 未按 schema 出)")
    if lint and not warn:  # 结构性缺失 (缺件/百分比) 两轮修不掉 = 不可用
        raise RuntimeError(f"起草未过验收: {lint} — 可手填或再点一次重掷")
    bible["motion_style"] = law
    cache.write_text(json.dumps(bible, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("[plan] 动画律已起草落盘 (%s): %s", book_title[:16], law[:60])
    return {"motion_style": law, "kept": False, "warn": warn or None}


def load_or_derive_bible(book_id: str, book_title: str, force: bool = False) -> dict[str, Any]:
    """圣经一次生成全系列锁定 (书级资产); 缓存在 _资产/圣经_{书}.json.

    force (0922 艺术圣经页): 无视缓存重掷一版 (风格绑定/灵魂三问可能已变).
    1012: 绑定变了自动重掷 — 缓存里烙 _bound_style, 与当前风格绑定不符 = 旧经配新风格
    (换绑后点✨生成圣经秒静默复用旧经实锤) → 视同 force; 无标记的存量经不触发
    (防全库旧书连环重掷), 下次生成/重掷自然烙上新标记。"""
    cache = _bible_cache_path(book_title)
    if cache.exists() and not force:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("style_prefix"):
                _cur_bind = ""
                try:
                    _bp = cache.parent / f"风格绑定_{shots_mod._safe(book_title)}.json"
                    _cur_bind = str(json.loads(_bp.read_text(encoding="utf-8")).get("style") or "")
                except Exception:  # noqa: BLE001 — 无绑定 = 不比对
                    pass
                _stale = bool(data.get("_bound_style") and _cur_bind
                              and data["_bound_style"] != _cur_bind)
                if _stale:
                    logger.warning("[plan] 风格绑定已变 (%s → %s) — 旧经作废自动重掷",
                                   data.get("_bound_style"), _cur_bind)
                else:
                    logger.info("[plan] 圣经复用 (书级锁定): %s", cache.name)
                    return data
        except Exception:  # noqa: BLE001
            logger.warning("[plan] 圣经缓存损坏, 重新生成: %s", cache)
    import sqlite3
    con = sqlite3.connect(load().db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("select input_json from book_projects where id=?", (book_id,)).fetchone()
    # 0923 角色依据增强 (用户问"人物造型依据是什么"): 总纲各集主题+贯穿人物喂给圣经 LLM —
    # 角色不再是凭核心主张瞎推的原型, 而是吃书内真实叙事脊柱 (导演层的贯穿人物字段)
    eps_rows = con.execute(
        "select ep_index, title, roadmap_json from book_episodes where book_id=? order by ep_index",
        (book_id,)).fetchall()
    con.close()
    ij = json.loads((row["input_json"] if row else None) or "{}")
    soul = ij.get("灵魂三问") or {}
    audience = (ij.get("受众地图") or {}).get("value") or {}
    ep_lines = []
    for er in eps_rows or []:
        try:
            rm = json.loads(er["roadmap_json"] or "{}")
        except Exception:  # noqa: BLE001
            rm = {}
        ep_lines.append(f"- 第{er['ep_index']}集《{er['title']}》贯穿人物: {rm.get('贯穿人物') or '未定'}")
    ep_block = ("\n【总纲·各集主题与贯穿人物 (叙事脊柱背景, 供风格与角色出场策略参考)】\n"
                + "\n".join(ep_lines)) if ep_lines else ""
    # 0923 樊登全书稿 (用户令): 讲述稿含书内真实人物/故事原味 — 视觉气质背景
    # (0928 用户裁定: 角色生成移角色设计台, 圣经只管视觉身份, 不再产出 characters)
    ff_text = ""
    from .feeds import get_feed
    _feed = get_feed("fandeng_full")  # B08: 读料经注册点 (外壳 anim_service 注入), 管线零 book_service import
    if _feed:
        try:
            ff_text = str(_feed(book_title) or "")[:20000]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[plan] 圣经喂樊登稿跳过: %s", exc)
    else:
        logger.debug("[plan] fandeng_full 供给未注册, 跳过樊登稿加料")
    ff_block = ("\n【樊登全书讲述稿 (书的叙事气质与真实人物形象参考)】\n" + ff_text) if ff_text else ""

    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json
    sys_p = (
        "你是动画短片的美术指导 (Art Director)。为一系列拆书动画短片制定美术圣经 — "
        "全系列从头到尾一个视觉身份, 像迪士尼动画一样统一。\n\n"
        + VISUAL_CHAIN_LAW + "\n"
        "产出以下字段:\n"
        "- style_prefix: ComfyUI 提示词前缀 (英文, 逗号分隔, 定义整体风格; 每镜必带); "
        "皮肤铁律 (0925 用户实锤琉璃人, 1006 卓有ep1 12/45 镜复燃): 人物皮肤一律实色卡通皮肤 — "
        "禁半透明/透明皮肤、禁皮肤内能量流/发光/琉璃化, 流体/液态/金属感只许用于环境与道具不上身人物\n"
        "- color_palette: 色彩体系 (主色/辅色/情绪色/禁用色, 中文描述)。"
        "**域界 (宪法②)**: 只描述环境光色基调与道具点缀, **从书的题材/时代/受众派生** "
        "(当代职场书=屏幕冷光/台灯暖光/会议室白光这类职场光色, 不是风格名的文化色); "
        "风格卡只给饱和度执行方式。**禁写人物服装颜色** (服装归逐镜画面句), "
        "禁从风格名联想选色 (国风≠朱砂红 — 1012 朱砂红烧到衬衫实锤)\n"
        "- texture: 线条与质感 (扁平/厚涂/水彩/3D 选一, 加描述); "
        "**皮肤铁律同步** (1006 卓有实锤: texture 写'人物半透明霓虹纹理'→LLM 忠实继承→玻璃人): "
        "描述人物质感时必须写'实色哑光卡通皮肤', **禁写半透明/透明/琉璃/皮肤内流动/"
        "皮肤下透出** — 能量流/液态/金属感只描述环境物件, 不描述人物皮肤\n"
        "- character_policy: 角色出场策略 (出场密度与叙事用法; 画法以【风格家族】为准 — 人物形态按其锚 "
        "(2D/3D/Q版) 执行, 全部人物含名人都露脸, 名人靠标志特征识别不做写实肖像); "
        "**皮肤铁律同步** (1006 实锤: policy 写'皮肤半透明质感,内部可见能量流动'→玻璃人): "
        "角色皮肤一律'实色哑光卡通皮肤', 情绪/状态变化用**表情+体态+服饰光效**表达, "
        "**禁用皮肤透明度/皮肤内能量流**表达 — 三字段 (prefix/texture/policy) 皮肤描述必须一致\n"
        "- 服饰与露脸铁律 (0922 用户令): **卡通脸必须有脸有表情** — 禁无脸/背影/剪影偷懒; "
        "**服饰细节到位且能快速区分人物关系** — 身份/阵营/地位一眼可辨\n"
        "(角色锁定卡 characters 不在此产出 — 0928 起归角色设计台提炼, 本经只管视觉身份)\n"
        "- text_graphic: 大字卡/数字卡与动画的融合方式 (底色/入场动画风格)\n"
        "- mood_keywords: 整体氛围关键词 (英文, 3-5 个)\n"
        "- era_anchor (1010 ID-079 道层先行): **本书时代锚** — 一句话钉死全书时空与\n"
        " 服饰基调 (如 '当代中国职场, 2020s: 衬衫/针织衫/西装西裤/休闲裤, 现代办公与城市空间; "
        "仅书中明写的历史人物按其真实年代近代正装')。这是全书人物服饰与场景物件的\n"
        " 时代宪法 — 下游按它执法, 禁含糊 ('现代感' 不算锚, 要具体到单品与年代)。\n"
        " **禁被风格卡反向污染 (1012, 国风卡写进'宫殿混搭'实锤)**: 风格家族只管画法\n"
        " 不管场景 — 宫殿/古装/红灯笼/旗等风格刻板符号, 只在书内容明写时才许入锚。\n"
        "- negative_keywords (1010 书级美学负通道): **负面关键词** — 英文逗号表 3-8 个, \n"
        "  直进 K2 负面提示词通道 (cfg=2.0)。写'绝不出现'的视觉实体: 本风格最怕的\n"
        "  污染源 (如 plastic shiny skin / 古装误入 / photorealistic; 城市外景书加 \n"
        "  red flag / red banner on buildings — 楼顶红旗 K2漏检+H3继承实锤)。与 \n"
        "  era_anchor 互补 — 时代锚管'该穿什么'(正向), 负词管'绝不出现什么'(负向)。禁中文、\n"
        "  禁整句、禁与 style_prefix 重复 (正负同词互相对冲)。\n"
        + _MOTION_LAW_SPEC +
        "\n"
        "铁律: 风格选一种并锁死, 禁多风格混用; 色板贯穿全片; 质感统一。\n"
        "受众铁律 (产线宪法): 视频发布在抖音, 观众是高速滑动的用户 — 基调必须 "
        "bright / vivid / 高视觉密度, 构图饱满色块对比强; 禁大面积暗底、禁 restrained "
        "性冷淡、禁'一束光照暗房'式电影感低调; 深色仅限危机叙事段且全集 ≤1/3 镜。\n"
        + _DAO_SPEC +
        '输出严格 JSON: {"style_prefix": "...", "color_palette": "...", '
        '"texture": "...", "character_policy": "...", "text_graphic": "...", '
        '"mood_keywords": "...", "era_anchor": "一句话时代锚", '
        '"negative_keywords": "英文逗号表 3-8 个", '
        '"motion_style": "一句话动画律", '
        '"identity": {...}, "world": {...}, "lighting": {...}, "color": {...}, '
        '"props": [...], "motifs": [...], "forbidden": [...]}'
    )
    l1 = ((audience or {}).get("L1") or {}).get("reader", "")
    # 0923 宪法进源头 (用户令): 圣经/角色卡生成时注入画面宪法 — 角色look从源头规避
    # 政治图像/军警制式/文字载体道具, 下游守门只是兜底
    from app.services import compliance as _cmp_law
    _law = _cmp_law.visual_constitution_prompt()
    from .config import resolve_style
    style = resolve_style(book_title)
    # 1009 大坑修 (backlog ID-080): 注入**完整配方**而非 desc 一行 — 旧版圣经 LLM
    # 拿一句摘要自由再创作, "实色哑光表面无纹理"塑料感药即从这来 (风格卡侧修过
    # 一次 .bak_skin_1006, 圣经侧从没同步)。铁律锁质感词来源: 只准翻译/合并卡上
    # 已有词, 禁自造质感限定。
    _k2_ov = (style.get("page_overrides") or {}).get("k2") or {}
    _style_card = json.dumps({
        "style_prompt_head": style.get("style_prompt_head"),
        "style_prompt_tail": style.get("style_prompt_tail"),
        "h3_style": style.get("h3_style"),
        "k2_head": _k2_ov.get("style_prompt_head"),
        "k2_tail": _k2_ov.get("style_prompt_tail"),
    }, ensure_ascii=False)
    user = (f"书名：《{book_title}》\n"
            f"核心：{soul.get('core', '')}\n"
            f"收益：{str(soul.get('gain', ''))[:150]}\n"
            f"目标观众：{l1[:80]}\n"
            f"系列集数：约 6-9 集, 每集 5 分钟\n"
            f"【风格配方卡 (已由书级风格选型绑定, 全书渲染以它为准)】\n{_style_card}\n"
            f"铁律: 你的 style_prefix 必须**从配方卡派生** — 翻译成英文或直接合并 h3_style/k2_head"
            f" 均可, 但**禁另起风格、禁自造卡上没有的质感限定词** (如'表面无纹理/半透明/琉璃'"
            f"类渲染指令 — 皮肤头发军规由渲染层全局注入, 圣经不写); texture 只描述"
            f"卡内质感 + 色板执行方式。\n"
            f"{style.get('desc') or ''}"
            + ep_block + ff_block + "\n" + _law)
    data = _parse_json(_llm().chat(sys_p, user, model="pro", temperature=0.4)) or {}
    if not data.get("style_prefix"):
        logger.warning("[plan] 圣经生成失败, 走空 (每镜自带风格尾兜底)")
        return {}
    # 0928 重掷保卡: 角色卡归角色设计台管后, 圣经重掷只换视觉身份字段 —
    # 缓存里已有 characters (手工攒/角色台提炼) 必须原样带过去, 落盘即全量覆盖
    # 1012 补: motion_style 已是 LLM 起草字段 — 同绑定重掷保手调 (清空+重掷=重起草);
    # 换绑则旧律随旧风格作废 (NeonDrip 液态律不能留给 chibi 书), 由新 schema 重起草
    # 1012 补: 烙 _bound_style (本经由哪个风格绑定生成) — 之后换绑自动检测重掷
    try:
        _bind_now = json.loads(
            (cache.parent / f"风格绑定_{shots_mod._safe(book_title)}.json")
            .read_text(encoding="utf-8")).get("style") or ""
    except Exception:  # noqa: BLE001
        _bind_now = ""
    if cache.exists():
        try:
            _old = json.loads(cache.read_text(encoding="utf-8"))
            if _old.get("characters"):
                data["characters"] = _old["characters"]
            if _old.get("motion_style") and _old.get("_bound_style") == _bind_now:
                data["motion_style"] = _old["motion_style"]
        except Exception:  # noqa: BLE001
            pass
    data["_bound_style"] = _bind_now
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("[plan] 圣经生成并缓存 (书级锁定): %s", cache)
    return data


# ── 规划主流程 ─────────────────────────────────────────────────

# ── 字符级对账: LLM 只管切分意图, 时间轴/narration 由 manifest 确定性推导 ──

_STRIP_CH = set(" \t\r\n，。！？；：、,.!?:;\"'“”‘’（）《》〈〉【】[](){}<>—…·")


_TTS_MARK_RE = re.compile(r"-\d+(?:\.\d+)?s-|<[^|>]+\|[^>]+>")


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """规范化文本 + norm 字符 → 原文下标映射 (剥标点空白, 同 shots.norm_for_match 语义).

    0918 口径同步: TTS 指令同步剥 — 停顿标记 -Xs- 整段丢; 拼音 <字|PINYIN>
    保留"字"的下标 (丢 <、|PINYIN> 部分)。不同步则旧形态镜 narration 锚定
    失配 → 错位重分配 (align s07 槽 7.7→8.8 误打回链实锤)。"""
    drop: set[int] = set()
    for m in _TTS_MARK_RE.finditer(text):
        s = m.group(0)
        if s.startswith("<"):
            word_len = len(s[1:].split("|", 1)[0])
            drop.add(m.start())  # '<'
            drop.update(range(m.start() + 1 + word_len, m.end()))  # '|PINYIN>'
        else:
            drop.update(range(m.start(), m.end()))  # -Xs- 整段
    keep = [i for i, ch in enumerate(text) if i not in drop and ch not in _STRIP_CH]
    return "".join(text[i] for i in keep), keep


# ── 窗口锁定对齐的公共件 (plan_scene 用; _realign_narrations 内有同款闭包,
#    全集路径已实证不动, 刻意不合并 — 0915) ──────────────────────────

def _packs_stream(segs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """manifest segs → (packs, norm 流). pack = 单个 TTS 包的字符区间/原文/时间."""
    packs: list[dict[str, Any]] = []
    parts: list[str] = []
    off = 0
    for sg in segs:
        norm, keep = _norm_with_map(sg["text"])
        if norm:
            packs.append({"lo": off, "hi": off + len(norm), "text": sg["text"],
                          "keep": keep, "t0": sg["t_start"], "dur": sg["duration"]})
            parts.append(norm)
            off += len(norm)
    return packs, "".join(parts)


def _pos_to_time(packs: list[dict[str, Any]], pos: int) -> float:
    for p in packs:
        if p["lo"] <= pos < p["hi"]:
            frac = (pos - p["lo"]) / max(p["hi"] - p["lo"], 1)
            return p["t0"] + frac * p["dur"]
    return packs[-1]["t0"] + packs[-1]["dur"] if packs else 0.0


def _pos_to_orig(packs: list[dict[str, Any]], pos: int) -> tuple[dict[str, Any], int]:
    for p in packs:
        if p["lo"] <= pos < p["hi"]:
            return p, min(pos - p["lo"], len(p["keep"]) - 1)
    return packs[-1], len(packs[-1]["keep"]) - 1


def _slice_orig(packs: list[dict[str, Any]], a: int, b: int) -> str:
    """norm 区间 [a,b) → 原文切片 (跨包拼接)."""
    out: list[str] = []
    cur = a
    while cur < b:
        p, i0 = _pos_to_orig(packs, cur)
        end_rel = min(b - p["lo"], p["hi"] - p["lo"])
        i1 = p["keep"][min(end_rel - 1, len(p["keep"]) - 1)] + 1
        out.append(p["text"][i0:i1] if i1 > i0 else p["text"][i0:i0 + 1])
        cur = p["hi"]
    return "".join(out).strip()


def _realign_narrations(doc: dict[str, Any], segs: list[dict[str, Any]]) -> dict[str, int]:
    """组/镜的 t_start/t_end 与 narration 按 manifest 字符流重切 (确定性, 0916 v2).

    v1 (0914): 镜 narration 头 14 字 locate → 切点时间包内字符比例插值.
    v1 之坑 (ep1 实锤, 用户诊断"规划期时间轴没算明白"): ①包内插值=漂移源,
    定格补差泛滥; ②切点落包中=劈句尾 ("用户。"型 ragged); ③重跑对账时 ragged
    头做锚 + 空镜保留旧时间 → 时间轴跑散 (断裂/重叠).
    v2 (0916): 切点吸附包边界 (0913 包化=一包一句, 包边=句边=唯一合法切点,
    时间=包边界精确值零漂移); 文本锚失败用现有 t_start 时间锚兜底; 切点严格
    递增 + 空镜剔除; 口播超 H3_MAX_SPAN 自动按整句包拆镜 (用户令: 一个不够
    就两个, 物理上限不动).
    """
    import copy as _copy

    tc = float(load().brand_card.opening_sec)
    # 包级 norm 流 (全局)
    packs = []
    stream_parts = []
    offset = 0
    for sg in segs:
        norm, keep = _norm_with_map(sg["text"])
        if norm:
            packs.append({"lo": offset, "hi": offset + len(norm), "text": sg["text"],
                          "keep": keep, "t0": sg["t_start"], "dur": sg["duration"]})
            stream_parts.append(norm)
            offset += len(norm)
    if not packs:
        return {"split": 0, "dropped": 0}
    stream = "".join(stream_parts)
    audio_end_t = packs[-1]["t0"] + packs[-1]["dur"]

    def pos_to_time(pos: int) -> float:
        for p in packs:
            if p["lo"] <= pos < p["hi"]:
                frac = (pos - p["lo"]) / max(p["hi"] - p["lo"], 1)
                return p["t0"] + frac * p["dur"]
        return packs[-1]["t0"] + packs[-1]["dur"]

    def time_to_pos(t: float) -> int:
        """音频相对时间 → 字符位 (包内线性, 仅作吸附前粗锚/拆镜取文).
        缝隙容差 ±0.05s: 句包 t 值 2 位舍入会产生发丝缝 (ep1 实锤 59.27s
        落缝 → 兜底流尾 → 整组饿死), 落缝映射到邻包边."""
        if t <= 0:
            return 0
        for p in packs:
            if p["t0"] <= t < p["t0"] + p["dur"]:
                frac = (t - p["t0"]) / max(p["dur"], 1e-6)
                return int(round(p["lo"] + frac * (p["hi"] - p["lo"])))
            if t < p["t0"] and t >= p["t0"] - 0.05:
                return p["lo"]
            if t >= p["t0"] + p["dur"] and t < p["t0"] + p["dur"] + 0.05:
                return p["hi"]
        return len(stream) if t >= audio_end_t else packs[0]["lo"]

    def pos_to_orig(pos: int) -> tuple[str, int]:
        """norm 流位置 → (所在包原文, 包内原文下标)."""
        for p in packs:
            if p["lo"] <= pos < p["hi"]:
                return p, min(pos - p["lo"], len(p["keep"]) - 1)
        return packs[-1], len(packs[-1]["keep"]) - 1

    def slice_orig(a: int, b: int) -> str:
        """norm 区间 [a,b) → 原文切片 (跨包拼接)."""
        out = []
        cur = a
        while cur < b:
            p, i0 = pos_to_orig(cur)
            end_rel = min(b - p["lo"], p["hi"] - p["lo"])
            i1 = p["keep"][min(end_rel - 1, len(p["keep"]) - 1)] + 1
            out.append(p["text"][i0:i1] if i1 > i0 else p["text"][i0:i0 + 1])
            cur = p["hi"]
        return "".join(out).strip()

    def locate(needle_norm: str, frm: int, to: int) -> int | None:
        if not needle_norm:
            return None
        p = stream.find(needle_norm, frm)
        if 0 <= p < to:
            return p
        head = needle_norm[:10]
        if len(head) >= 5:
            p = stream.find(head, frm)
            if 0 <= p < to:
                return p
        return None

    def snap_mono(pos: int, floor: int) -> int:
        """切点吸附最近包边界 (整包归内容占比大的一侧, frac>0.5 归前段);
        吸附点已被前界占用则顺延下一个包边 — 严格递增, 杜绝空镜带旧时间."""
        if floor >= len(stream):
            return len(stream)
        if pos <= floor:
            pos = floor + 1
        cand = pos
        for p in packs:
            if p["lo"] <= pos < p["hi"]:
                frac = (pos - p["lo"]) / max(p["hi"] - p["lo"], 1)
                cand = p["hi"] if frac > 0.5 else p["lo"]
                break
        if cand <= floor:
            for p in packs:
                if p["lo"] > floor:
                    cand = p["lo"]
                    break
                if p["hi"] > floor:
                    cand = p["hi"]
                    break
            cand = min(cand, len(stream))
        return cand

    def anchor_pos(head: str, t_abs: float, frm: int, to: int) -> int:
        """切点粗锚: 时间锚为主 (t_start 反查字符位, 天然单调), 文本锚仅在
        ±25 字内采纳 (ragged 头总能"命中"但位置漂 — 文本只配微调不配导航)."""
        tp = time_to_pos(t_abs - tc)
        tp = min(max(tp, frm if to > frm else tp), to)
        lp = locate(head, frm, to)
        if lp is not None and abs(lp - tp) <= 25:
            return lp
        return tp

    # 组按时间序处理 (doc 列表序≠时间序 — ep1 实锤 S08 开场组在列表尾,
    # 按列表序单调推进会把整组压到流尾饿死; 时间锚+严格单调必须时间序)
    groups = sorted(doc.get("groups") or [], key=lambda g: float(g.get("t_start") or 0))
    # 组边界 (首组强制 0; 吸附包边)
    bounds: list[int] = []
    for gi, g in enumerate(groups):
        if gi == 0:
            bounds.append(0)
            continue
        pos = anchor_pos(_norm_with_map(g.get("narration") or "")[0][:16],
                         float(g.get("t_start") or 0), bounds[-1], len(stream))
        bounds.append(snap_mono(pos, bounds[-1]))
    bounds.append(len(stream))
    by_group_pre: dict[str, list[dict[str, Any]]] = {}
    for s in doc["shots"]:
        by_group_pre.setdefault(s.get("group_id"), []).append(s)
    for gi, g in enumerate(groups):
        _gshots = by_group_pre.get(g["group_id"]) or []
        # 0919 装包制组 (镜全 zone/packed): 组窗=槽包络已对, 不按包边重切
        if _gshots and all(s.get("zone") or s.get("packed") for s in _gshots):
            continue
        a, b = bounds[gi], bounds[gi + 1]
        if b > a:
            g["t_start"] = round(tc + pos_to_time(a), 2)
            g["t_end"] = round(tc + pos_to_time(b), 2)
            g["narration"] = slice_orig(a, b)[:300]
    # 镜边界 (组内; 组首镜锚组界; 空镜剔除不保留旧时间)
    by_group: dict[str, list[dict[str, Any]]] = {}
    for s in doc["shots"]:
        by_group.setdefault(s.get("group_id"), []).append(s)
    dropped = 0
    for gi, g in enumerate(groups):
        gs = by_group.get(g["group_id"]) or []
        if not gs:
            continue
        a, b = bounds[gi], bounds[gi + 1]
        mb: list[int] = [a]
        for s in gs[1:]:
            pos = anchor_pos(_norm_with_map(s.get("narration") or "")[0][:14],
                             float(s.get("t_start") or 0), mb[-1], b)
            mb.append(snap_mono(pos, mb[-1]) if pos < b else b)
        mb.append(b)
        keep: list[dict[str, Any]] = []
        for i, s in enumerate(gs):
            if s.get("zone") or s.get("packed"):  # 0919 特区/装包镜: 槽位神圣, 不按包边重切
                keep.append(s)
                continue
            ma, mbnd = mb[i], mb[i + 1]
            if mbnd <= ma:
                dropped += 1
                logger.warning("[d2] 空镜剔除 %s (切点吸附包边后无口播区间)", s.get("shot_id"))
                continue
            s["t_start"] = round(tc + pos_to_time(ma), 2)
            s["t_end"] = round(tc + pos_to_time(mbnd), 2)
            s["narration"] = slice_orig(ma, mbnd)[:300]
            keep.append(s)
        by_group[g["group_id"]] = keep
    doc["shots"] = [s for g in groups for s in (by_group.get(g.get("group_id")) or [])]
    doc["shots"].sort(key=lambda s: float(s["t_start"]))
    # 0919 特区缝保护 (🎚对齐落盘实锤: s68 头被包边吸附 65.5→64.41, 与 s67 槽叠
    # 1.1s → 时间轴断裂, 全片校验连坐单镜操作): zone 镜边界神圣, 非特区邻镜让位 —
    # 与 plan_scene 对齐段的保护同款, 此前漏了独立对齐链路
    _zs = [s for s in doc["shots"] if s.get("zone")]
    if _zs:
        _ordered = doc["shots"]
        for i, s in enumerate(_ordered):
            if s.get("zone") or s.get("packed"):
                continue
            if i > 0 and _ordered[i - 1].get("zone"):
                s["t_start"] = max(float(s["t_start"]), float(_ordered[i - 1]["t_end"]))
            if i + 1 < len(_ordered) and _ordered[i + 1].get("zone"):
                s["t_end"] = min(float(s["t_end"]), float(_ordered[i + 1]["t_start"]))

    # ── 自动拆镜 (0916 用户令: 物理上限不动, 超长口播一镜不够就两镜) ──
    # 整句包贪心分块 (每块 ≤ H3_MAX_SPAN); 块1 继承原镜 id+素材 (已生成视频
    # 直接复用), 块2+ 新 id 进 planned 生成队列; text_layer 按绝对时间分家
    # (字幕/强调词跟句走). 0917: deepcopy 只是占位 — 分身逐字继承提示词=同一
    # 张图换噪点 (违反"每镜必换构图"), 调用方须跑 redesign_split_clones 换构图.
    split = 0
    out_shots: list[dict[str, Any]] = []
    all_ids = {str(s["shot_id"]) for s in doc["shots"]}
    for s in doc["shots"]:
        t0, t1 = float(s["t_start"]), float(s["t_end"])
        span = t1 - t0
        my_packs = [p for p in packs
                    if p["t0"] + tc >= t0 - 0.01 and p["t0"] + p["dur"] + tc <= t1 + 0.01]
        chunks: list[list[float]] = []
        if span > H3_MAX_SPAN + 0.05 and my_packs:
            for p in my_packs:
                ps, pe = p["t0"] + tc, p["t0"] + p["dur"] + tc
                if chunks and pe - chunks[-1][0] > H3_MAX_SPAN:
                    chunks[-1][1] = ps
                    chunks.append([ps, pe])
                elif chunks:
                    chunks[-1][1] = pe
                else:
                    chunks.append([ps, pe])
            chunks = [c for c in chunks if c[1] > c[0] + 0.01]
        if len(chunks) <= 1:
            if span > H3_MAX_SPAN + 0.05:
                s["overrun_s"] = round(span - H3_MAX_SPAN, 2)  # 单句超限 (极少), 留人决策
            else:
                s.pop("overrun_s", None)
            out_shots.append(s)
            continue
        old_tls = [(float(t.get("t_start") or 0), float(t.get("t_end") or 0), t)
                   for t in (s.get("text_layer") or [])]
        base_id = str(s["shot_id"])
        for k, (c0, c1) in enumerate([(round(c[0], 2), round(c[1], 2)) for c in chunks]):
            if k == 0:
                ch = s
            else:
                ch = _copy.deepcopy(s)
                suffix = "b"
                while f"{base_id}{suffix}" in all_ids:
                    suffix = chr(ord(suffix) + 1)
                ch["shot_id"] = f"{base_id}{suffix}"
                all_ids.add(str(ch["shot_id"]))
                ch["split_parent"] = base_id  # 溯源标记 (克隆占位, 待重设计换构图)
                ch["status"] = "planned"
                ch.pop("video_file", None)
                ch.pop("image_file", None)
                ch.pop("brand_card", None)  # 0916: 分身镜不得继承品牌卡旗标 (s07b 毒根)
                if "seed" in ch:
                    ch["seed"] = int(ch["seed"]) + k
                split += 1
            ch["t_start"], ch["t_end"] = c0, c1
            ch["narration"] = slice_orig(time_to_pos(c0 - tc), time_to_pos(c1 - tc))[:300]
            ch_tl = []
            for rt0, rt1, t in old_tls:
                a0, a1 = t0 + rt0, t0 + rt1
                if a0 < c1 and a1 > c0:  # 与本块有交
                    nt0 = max(a0 - c0, 0.0)
                    nt1 = min(a1, c1) - c0
                    if nt1 > nt0:
                        ch_tl.append({**t, "t_start": round(nt0, 2), "t_end": round(nt1, 2)})
            ch["text_layer"] = ch_tl
            ch.pop("overrun_s", None)
            if isinstance(ch.get("anim"), dict):
                shots_mod.stretch_beats(ch, min(round(c1 - c0, 2), H3_MAX_SPAN))
            _clamp_motion_events(ch)  # 拆前绝对时间事件不可达 → 删
            out_shots.append(ch)
    doc["shots"] = out_shots
    # 场窗元数据回同步 (0917 深查: 拆镜/吸附后 arcs t 与镜实际起止漂 ±1s,
    # 时长核验永远报小残) — 以本场镜实际 min/max 为准
    by_arc: dict[str, list[dict[str, Any]]] = {}
    for s in doc["shots"]:
        by_arc.setdefault(str(s.get("arc_id")), []).append(s)
    for a in doc.get("arcs") or []:
        ss2 = by_arc.get(str(a.get("arc_id"))) or []
        if ss2:
            a["t_start"] = round(min(float(s["t_start"]) for s in ss2), 2)
            a["t_end"] = round(max(float(s["t_end"]) for s in ss2), 2)
    if split or dropped:
        logger.info("[d2] 拆镜 %d 个新镜, 剔除空镜 %d 个", split, dropped)
    return {"split": split, "dropped": dropped}


def _series_lock_block(book_title: str, ep: int) -> str:
    """系列风格锁 (0915 用户令: 第1集生成后锁定, 一书一系列; 新书自由).

    锚 = 第1集当前 doc — 磨合到哪版系列就跟到哪版 (ep1 重规划自动刷新锚)。
    ep2+ 整集规划时注入继承块: 场景基调/文字字体语言/角色造型必须一致。
    """
    if ep <= 1:
        return ""
    try:
        anchor = shots_mod.load_doc(book_title, 1)
    except Exception:  # noqa: BLE001 — 第1集未规划 = 无锁 (自由)
        return ""
    if not (anchor.get("shots") or []):
        return ""
    lines = [
        "\n【系列风格锁定 (本书第1集已定稿, 一书一系列铁律 — 必须继承, 只换叙事内容)】",
        f"- 风格配方: {anchor.get('style_recipe') or resolve_style(book_title).get('name')}",
        f"- 第1集画面策略: {str(anchor.get('ep_summary') or '')[:120]}",
    ]
    for a in (anchor.get("arcs") or [])[:8]:
        nar = str(a.get("narrative") or "")[:60]
        if nar:
            lines.append(f"- 场 {a.get('arc_id')}: {nar}")
    for g in (anchor.get("groups") or [])[:8]:
        kf = str(g.get("keyframe_zh") or "")[:80]
        if kf:
            lines.append(f"  场景语言: {kf}")
    n_typo = 0
    for s in anchor["shots"]:
        if n_typo >= 5:
            break
        mot = str(s.get("motion_zh") or "")
        tl = s.get("text_layer") or []
        hit = next((f"{t.get('text')} ({t.get('kind')})" for t in tl), "") if tl else ""
        if not hit and ("艺术字" in mot or "书法" in mot or "字体" in mot):
            hit = mot[:70]
        if hit:
            lines.append(f"- 文字风格样例: {hit[:90]}")
            n_typo += 1
    lines.append("继承要求: 场景基调/色调/机位语言/文字字体/角色造型与第1集完全一致; "
                 "同角色跨集外形一致 (年龄/发型/体态/标志特征不变)。")
    return "\n".join(lines) + "\n"


_MODULE_MAX_SPAN = 70.0  # 单模块超此时长 → 按包界分窗 (防单窗预算摊薄, 0916 教训)


def _audience_rows(book_id: str) -> tuple[str, list[str]]:
    """取受众数据 (0918 用户令"立意层带入目标群"): (L1 书定读者画像, 两轴池组列表).

    L1 = book_projects.input_json['目标读者画像'].value (tier=persona 时被
    人设覆盖不取, 与 episode_gen 口径一致); 池 = 受众地图.value 上下游/上下层。"""
    import json as _json
    import sqlite3 as _sq
    try:
        from app.config import load_config
        db_url = str(load_config().app.database_url)
        db_path = db_url.split("///", 1)[-1] if "///" in db_url else db_url
        con = _sq.connect(db_path)
        row = con.execute("select input_json from book_projects where id=?",
                          (book_id,)).fetchone()
        con.close()
        inp = _json.loads(row[0]) if row and row[0] else {}
    except Exception as exc:  # noqa: BLE001 — 取不到受众不炸立意, 留痕
        logger.warning("[plan-arcs] 受众取数失败 (立意不带受众锚): %s", exc)
        return "", []
    l1 = inp.get("目标读者画像") if isinstance(inp.get("目标读者画像"), dict) else {}
    l1v = str(l1.get("value") or "") if l1.get("tier") != "persona" else ""
    pools: list[str] = []
    am_raw = inp.get("受众地图") if isinstance(inp.get("受众地图"), dict) else {}
    am = am_raw.get("value") if isinstance(am_raw.get("value"), dict) else am_raw
    for axis in ("上下游", "上下层"):
        for p in (am.get(axis) or []):
            if isinstance(p, dict) and p.get("group"):
                pools.append(str(p["group"]) + (f"(桥:{p['bridge']})" if p.get("bridge") else ""))
    return l1v, pools


def _audience_block(l1: str, pools: list[str]) -> str:
    """立意受众块: 隐喻必须戳中 L1 的处境 (同构处境铁律), 账号池可搭桥."""
    if not l1 and not pools:
        return ""
    parts = ["\n【受众锚 (立意必须对着这些人设计)】"]
    if l1:
        parts.append(f"- 本书目标读者 (L1, 全篇画面只对他说话): {l1[:220]}")
    if pools:
        parts.append(f"- 老谭账号人群 (两轴池, 桥式画面可兼雇): {'、'.join(pools[:8])}")
    parts.append("- 铁律: 每场隐喻里'人的处境'必须与 L1 同构 (他见过/正在经历的场景), "
                 "禁画 L1 无感的行业画面; 隐喻承载句优先选 L1 会心头一紧的口播。")
    return "\n".join(parts) + "\n"


def module_windows(segs: list[dict[str, Any]], max_span: float = _MODULE_MAX_SPAN
                   ) -> list[dict[str, Any]] | None:
    """模块总线 (0917 架构令): TTS manifest 包 (带 module_id) → 场窗列表.

    模块界=场界 (六拍结构 = 创作骨架 = 规划单元, 用户定案); 单模块超 max_span
    按包界贪心分窗 (窗界永远落在包界, 不腰斩包)。返回 None = 包无 module_id
    (旧 manifest) — 调用方走 LLM 自由切场旧路径。窗 = {module_id,t_start,t_end,text}。
    """
    if not segs or any(s.get("module_id") is None for s in segs):
        return None
    wins: list[dict[str, Any]] = []
    mod_bounds: list[tuple[int, list[dict[str, Any]]]] = []
    for s in segs:
        mid = int(s["module_id"])
        if not mod_bounds or mod_bounds[-1][0] != mid:
            mod_bounds.append((mid, []))
        mod_bounds[-1][1].append(s)
    for mid, msegs in mod_bounds:
        # 模块内贪心分窗: 窗时长累计 ≥ max_span 且非末包 → 在包后切窗
        parts: list[list[dict[str, Any]]] = []
        cur: list[dict[str, Any]] = []
        for i, s in enumerate(msegs):
            cur.append(s)
            if s["t_end"] - cur[0]["t_start"] >= max_span and i < len(msegs) - 1:
                parts.append(cur)
                cur = []
        if cur:
            parts.append(cur)
        for p in parts:
            wins.append({"module_id": mid,
                         "t_start": float(p[0]["t_start"]), "t_end": float(p[-1]["t_end"]),
                         "text": "".join(str(x.get("text") or "") for x in p)})
    return wins


_SHOT_MIN_SPAN = CST.duration_table()["shot_min_span"]  # 密度宪法下限 (0917): 不足此长并入邻镜 (1013 P0 迁宪法)


def refs_parse(refs: Any) -> list[int] | None:
    """LLM refs 字段 → 0 基句号列表 (升序去重).

    认 "c01-c03" 区间 / "c01,c03" 列表 / ["c01","c02"] / "c01" / 1 (裸数字);
    无法解析返回 None (调用方整体回退旧路径 — 混合模式半查半猜最危险)."""
    import re as _re
    if refs is None:
        return None
    if isinstance(refs, (int, float)):
        return [int(refs) - 1] if int(refs) >= 1 else None
    if not isinstance(refs, (str, list)):
        return None
    items = refs if isinstance(refs, list) else [x.strip() for x in str(refs).split(",")]
    out: list[int] = []
    for it in items:
        m = _re.fullmatch(r"[cC](\d+)(?:\s*-\s*[cC]?(\d+))?", str(it).strip())
        if not m:
            return None
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a:
            return None
        out.extend(range(a, b + 1))
    return sorted(set(x - 1 for x in out)) or None


def expand_refs_groups(raw_groups: list[dict[str, Any]], sent_table: list[dict[str, Any]],
                       a0: float, a1: float, min_shot_s: float = _SHOT_MIN_SPAN,
                       ) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """refs 协议展开 (0917 架构令): LLM raw shots 的 refs 查句级表回填
    t_start/t_end/narration — 时间零 LLM 参与, 结构上不可能漂移.

    确定性修复: 引用越界 clamp 进场窗; 漏句 (窗内句未被引用) 并入时轴最近镜;
    密度执法: < min_shot_s 的镜并入前镜 (首镜并后镜), 句集合并内容保留前镜。
    任一 shot refs 无法解析 → 返回 (None, notes), 调用方整场回退旧路径。"""
    notes: list[str] = []
    win = [s for s in (sent_table or [])
           if float(s.get("t_end", 0)) > a0 + 0.01 and float(s.get("t_start", 0)) < a1 - 0.01]
    if not win:
        return None, [f"场窗 {a0:.1f}-{a1:.1f}s 句级表为空 (manifest 无 sent_table?)"]
    n_sent = len(win)
    shots_flat: list[dict[str, Any]] = []
    for g in raw_groups or []:
        for raw in (g.get("shots") or []):
            idxs = refs_parse(raw.get("refs"))
            if idxs is None:
                return None, [f"shot refs 不可解析: {str(raw.get('refs'))[:40]!r} — 整场回退旧路径"]
            bad = [i for i in idxs if i >= n_sent]
            if bad:
                notes.append(f"refs 越界 {bad} (窗内仅 c01-c{n_sent}) — clamp")
                idxs = [i for i in idxs if i < n_sent]
            if not idxs:
                return None, ["shot refs clamp 后为空 — 整场回退旧路径"]
            shots_flat.append({**raw, "_idx": sorted(set(idxs))})
    if not shots_flat:
        return None, ["refs 模式无 shots — 整场回退旧路径"]
    # 漏句修复: 窗内句未被引用 → 并给句号相邻的镜 (无邻并首镜)
    covered = {i for s in shots_flat for i in s["_idx"]}
    leaked = [i for i in range(n_sent) if i not in covered]
    for i in leaked:
        host = next((s for s in shots_flat if i - 1 in s["_idx"] or i + 1 in s["_idx"]),
                    shots_flat[0])
        host["_idx"] = sorted(set(host["_idx"]) | {i})
    if leaked:
        notes.append(f"漏句 {len(leaked)} 个并入邻镜: {leaked[:6]}")
    # 时间排序 (LLM 引用乱序 → 按 idx 排)
    shots_flat.sort(key=lambda s: s["_idx"][0])
    # 密度执法: 短镜并入前镜 (首镜并后镜)
    def _span(s: dict[str, Any]) -> float:
        return win[s["_idx"][-1]]["t_end"] - win[s["_idx"][0]]["t_start"]
    merged = 0
    i = 0
    while len(shots_flat) > 1:
        if _span(shots_flat[i]) < min_shot_s:
            if i == 0:
                host, gone = shots_flat[1], shots_flat[0]
            else:
                host, gone = shots_flat[i - 1], shots_flat[i]
            host_idx = shots_flat.index(host)
            host["_idx"] = sorted(set(host["_idx"]) | set(gone["_idx"]))
            host["_idx"].sort()
            shots_flat.remove(gone)
            merged += 1
            i = max(host_idx - 1, 0)
            continue
        i += 1
        if i >= len(shots_flat):
            break
    if merged:
        notes.append(f"密度执法: {merged} 短镜 (<{min_shot_s}s) 并入邻镜")
    # H3 上限执法 (0918 s26 实锤: refs 镜界=句边, 旧拆镜的包级贪心失配不触发):
    # >H3_MAX_SPAN 的镜按句贪心切块 (每块 ≤ 上限), 第 2 块起标 split_parent —
    # 拼接链既有 redesign_split_clones 会对分身自动重设计构图 (克隆不落地)。
    split = 0
    out_flat: list[dict[str, Any]] = []
    for s in shots_flat:
        idxs = s["_idx"]
        if _span(s) <= H3_MAX_SPAN + 0.05 or len(idxs) < 2:
            out_flat.append(s)
            continue
        # 句贪心切块: 每块以自身首句起算累计到上限即切; 末句不单独成块
        # (孤句尾并前块, 防 <4s 碎尾镜)
        chunks: list[list[int]] = []
        cur_chunk: list[int] = []
        for i in idxs:
            cur_chunk.append(i)
            t0 = float(win[cur_chunk[0]]["t_start"])
            if i != idxs[-1] and float(win[i]["t_end"]) - t0 >= H3_MAX_SPAN - 2.0:
                chunks.append(cur_chunk)
                cur_chunk = []
        if cur_chunk:
            if chunks and len(cur_chunk) == 1:  # 孤句尾并前块 (防碎尾)
                chunks[-1].extend(cur_chunk)
            else:
                chunks.append(cur_chunk)
        # 尾块均衡 (0918 s08b 实锤): 尾块 <4s 且前块有余 (末句移过去后前块仍 ≥4s
        # 且 ≤上限) → 前块末句下放, 拆镜分身不再出短镜
        while (len(chunks) >= 2 and len(chunks[-1]) < len(chunks[-2])
               and float(win[chunks[-1][-1]]["t_end"]) - float(win[chunks[-1][0]]["t_start"]) < min_shot_s
               and len(chunks[-2]) >= 2):
            moved = chunks[-2].pop()
            chunks[-1].insert(0, moved)
            tail_ok = (float(win[chunks[-1][-1]]["t_end"]) - float(win[chunks[-1][0]]["t_start"]) >= min_shot_s
                       and float(win[chunks[-2][-1]]["t_end"]) - float(win[chunks[-2][0]]["t_start"]) >= min_shot_s)
            head_ok = float(win[chunks[-2][-1]]["t_end"]) - float(win[chunks[-2][0]]["t_start"]) <= H3_MAX_SPAN + 0.05
            if tail_ok and head_ok:
                break
            chunks[-2].append(chunks[-1].pop(0))  # 回滚
            break
        for k, ch in enumerate(chunks):
            piece = {**s, "_idx": sorted(ch)}
            if k > 0:
                piece["split_parent"] = "refs"  # 分身标记 (重设计钩子识别)
            out_flat.append(piece)
        split += len(chunks) - 1
    shots_flat = out_flat
    if split:
        notes.append(f"H3上限执法: {split} 刀拆分超长镜 (> {H3_MAX_SPAN:.0f}s)")
    # 回填 t/narration (音频轴); refs 原文留档 (审计可追溯: 这镜引用了哪些句)
    out_groups: list[dict[str, Any]] = []
    n = 0
    for s in shots_flat:
        n += 1
        idxs = s["_idx"]
        s.pop("_idx", None)
        s["t_start"] = round(float(win[idxs[0]]["t_start"]), 2)
        s["t_end"] = round(float(win[idxs[-1]]["t_end"]), 2)
        s["narration"] = "".join(str(win[i].get("text") or "") for i in idxs).strip()
        s["refs_resolved"] = [f"c{i + 1:02d}" for i in idxs]  # 展开后句号留档
        s["shot_id"] = f"r{n:02d}"  # 系统编号 (拼接方统一重编)
    # 单场一组 (plan_scene 语义): 全部 shots 归一组, 组 t = 场窗
    out_groups.append({**(raw_groups[0] if raw_groups else {}),
                       "t_start": round(a0, 2), "t_end": round(a1, 2),
                       "shots": shots_flat})
    return out_groups, notes


# ── 立意模块 v2 (0918 用户令: 多维度+可编辑一等公民) ────────────
# 每场结构化维度: metaphor_core(核心映射A=B) / metaphor_mappings(分映射) /
# carrier_lines(口播承载句) / visual_motifs(可画母题) / motion(运动线) /
# banned_motifs(重掷否决历史, LLM 禁区) / concept_notes(人批注, 全链可见)。
# visual_metaphor = 系统投影 (core+拼接), 不由 LLM 直出 — 单一事实源。
_CONCEPT_KEYS = ("metaphor_core", "metaphor_mappings", "carrier_lines",
                 "visual_motifs", "banned_motifs", "concept_notes")


def _concept_fields(a: dict[str, Any]) -> dict[str, Any]:
    """arc → 结构化立意字段 (旧 arc 兼容: visual_metaphor 拆入 core)。"""
    out: dict[str, Any] = {}
    for k in _CONCEPT_KEYS:
        if a.get(k):
            out[k] = a[k]
    if not out.get("metaphor_core") and a.get("visual_metaphor"):
        out["metaphor_core"] = str(a["visual_metaphor"]).split("（")[0].split("(")[0][:80]
    return out


def _project_visual_metaphor(a: dict[str, Any]) -> str:
    """结构化 → visual_metaphor 投影 (老消费方兼容)。"""
    core = str(a.get("metaphor_core") or a.get("visual_metaphor") or "").strip()
    maps = [str(m) for m in (a.get("metaphor_mappings") or []) if str(m).strip()]
    carriers = [str(c) for c in (a.get("carrier_lines") or []) if str(c).strip()]
    parts = [core] + [f"{m}" for m in maps]
    if carriers:
        parts.append("承载句: " + "+".join(f"「{c}」" for c in carriers[:3]))
    return ";".join(parts)


def write_concept_md(doc: dict[str, Any]) -> None:
    """立意.md = 立意模块的人读投影 (骨架 arcs 为真相源, 本文件只读)。

    0926 用户令表格化: 首屏 = 世界库 + 六拍分配表 (跑立意的固定格式 — 用户判词
    "一大堆文字看的人想吐"), 逐场明细降为次级附录; 编辑走动画工坊立意面板。"""
    arcs = doc.get("arcs") or []
    lines = ["# 立意案 (立意模块投影 — 编辑请在动画工坊立意面板)", "",
             f"> 本集策略: {doc.get('ep_summary', '')}", ""]
    wl = doc.get("world_library") or []
    if wl:
        lines.append(f"## 全书视觉世界库 ({len(wl)})")
        lines += [f"- {w}" for w in wl]
        lines.append("")
    lines.append("## 六拍分配")
    lines.append("| 拍 | 时段 | 视觉世界 | 隐喻核心 |")
    lines.append("|---|---|---|---|")
    for a in arcs:
        t0, t1 = float(a.get("t_start") or 0), float(a.get("t_end") or 0)
        core = str(a.get("metaphor_core") or a.get("visual_metaphor") or "(缺)")
        lines.append(f"| {a.get('arc_id')} | {t0:.0f}–{t1:.0f}s "
                     f"| {str(a.get('visual_world') or '(未定)')} | {core.replace('|', '／')} |")
    lines.append("")
    if doc.get("_concept_warnings"):
        lines.append("## ⚠ 宪法警告")
        lines += [f"- {w}" for w in doc["_concept_warnings"]]
        lines.append("")
    lines.append("## 逐场明细 (附录)")
    lines.append("")
    for a in arcs:
        span = float(a.get("t_end") or 0) - float(a.get("t_start") or 0)
        lines.append(f"### {a.get('arc_id')} ({span:.0f}s) {a.get('narrative', '')}")
        lines.append(f"- **核心映射**: {a.get('metaphor_core') or a.get('visual_metaphor', '(缺)')}")
        for m in (a.get("metaphor_mappings") or []):
            lines.append(f"  - 映射: {m}")
        for c in (a.get("carrier_lines") or []):
            lines.append(f"  - 承载句: {c}")
        for v in (a.get("visual_motifs") or []):
            lines.append(f"  - 画面母题: {v}")
        if a.get("banned_motifs"):
            lines.append(f"- 🚫 禁用意象: {'、'.join(a['banned_motifs'])}")
        if a.get("concept_notes"):
            lines.append(f"- ✍️ 人批注: {a['concept_notes']}")
        lines.append(f"- 运动线: {a.get('motion', '')}")
        lines.append("")
    ep_dir = shots_mod.ep_dir(str(doc.get("book_title")), int(doc.get("ep") or 1))
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "立意.md").write_text("\n".join(lines), encoding="utf-8")


def _audit_concept(arcs: list[dict]) -> list[str]:
    """立意审计一把尺 (1013 抽出): 隐喻缺失/隐喻撞车 Jaccard/世界轮换+配额.

    规划 (plan_arcs) / 立意手改 (save_concept) / 单场重掷 (reroll_concept) 三路
    共用 — 旧版审计只活在规划里, 立意面板改好违规后警告不清 = 人闸读物永久
    陈旧 (卓有ep1 实锤: A1 重掷换世界后「A1/A2 同世界」警告仍挂)。"""
    def _bg(x: str) -> set[str]:
        x = re.sub(r"[，。；：、\s＝=（）()0-9a-zA-Z]", "", x or "")
        # 0925 修: 空隐喻返空集 — 旧版 {""} 是真值集合, 空对空 Jaccard=100%,
        # 一集隐喻全空时 21 对全"撞车" (HBO ep9 实锤), 真信号 (隐喻缺失) 反被淹没
        if not x:
            return set()
        return {x[i:i + 2] for i in range(len(x) - 1)} if len(x) > 1 else {x}
    warns: list[str] = []
    # 0925 修: 空隐喻单列缺失警 — 立意人闸真正要的信号
    for a in arcs:
        if not str(a.get("visual_metaphor") or "").strip():
            w = f"[立意] {a['arc_id']} 隐喻缺失 — 立意面板重掷/手补后重跑"
            warns.append(w)
            logger.warning("[plan-arcs] %s", w)
    for i in range(len(arcs)):
        for j in range(i + 1, len(arcs)):
            bi, bj = _bg(str(arcs[i].get("visual_metaphor") or "")), \
                     _bg(str(arcs[j].get("visual_metaphor") or ""))
            if not bi or not bj:
                continue
            jac = len(bi & bj) / len(bi | bj)
            if jac >= 0.35:
                w = (f"[立意] {arcs[i]['arc_id']} 与 {arcs[j]['arc_id']} 隐喻撞车 "
                     f"(相似 {jac:.0%}) — 分镜前人工改其中一场 (立意.md 可编辑重跑)")
                warns.append(w)
                logger.warning("[plan-arcs] %s", w)
    # 世界轮换审计 (0926 宪法): 相邻场同世界 / 主打世界超配额 → 人闸警告可见
    _wl = [str(a.get("visual_world") or "").strip() for a in arcs]
    for i in range(1, len(_wl)):
        if _wl[i] and _wl[i] == _wl[i - 1]:
            w = (f"[立意] A相邻场 {arcs[i - 1]['arc_id']}/{arcs[i]['arc_id']} "
                 f"视觉世界相同「{_wl[i]}」— 违反轮换宪法")
            warns.append(w)
            logger.warning("[plan-arcs] %s", w)
    if _wl:
        from collections import Counter as _C
        _top, _n = _C([x for x in _wl if x]).most_common(1)[0] if any(_wl) else ("", 0)
        if _n > max(2, (len(_wl) + 2) // 3):
            w = f"[立意] 视觉世界「{_top}」占 {_n}/{len(_wl)} 场 — 超轮换配额 (主打世界应 ≤2/6)"
            warns.append(w)
            logger.warning("[plan-arcs] %s", w)
    # 1014 载体诚实律审计 (ep3 A3 实锤: 立意把镜子定义为发光显示器 — 镜子只能
    # 反射在场物, 载体错配则 writer 全链忠实放大成「镜框屏幕」)
    _REFLECT_RE = re.compile(
        r"(镜|水面|金属面).{0,12}(映出|显示|播放|浮现).{0,24}(场景|画面|另一|远处|办公室|房间|工位)")
    # 1015 口径修正 (用户令: 抖音/法规禁的是**中国地图**, 非任何地图 — 裸词+沙盘地形
    # 过宽误伤「办公室地形/藏宝图」实锤): 敏感核心=中国地图族+国界线族; 孪生只认
    # 疆域信号 (ep3 s51 实锤: 棋盘+疆域线渲成带国界的地图被生图硬闸连杀)。
    _SENS_RE = re.compile(r"中国地图|国界|省界|边界线|疆域|九段线"
                          r"|(地图|棋盘|沙盘).{0,12}(疆域|国界|省界|边界线|九段线)")
    for a in arcs:
        blob = "；".join([str(a.get("narrative") or ""), str(a.get("motion") or ""),
                          str(a.get("visual_world") or "")]
                         + [str(v) for v in (a.get("visual_motifs") or [])])
        if _REFLECT_RE.search(blob):
            w = (f"[立意] {a['arc_id']} 反射体被当展示体 (镜/水面映出远程场景) — 载体诚实律: "
                 "换屏幕/门/对话框等展示体, 或改为照见在场人物")
            warns.append(w)
            logger.warning("[plan-arcs] %s", w)
        if _SENS_RE.search(blob):
            w = (f"[立意] {a['arc_id']} 母题含敏感物/地图视觉孪生 — K2 生图硬闸会连杀, "
                 "换纯棋盘格/指代物 (疆域信息进旁白)")
            warns.append(w)
            logger.warning("[plan-arcs] %s", w)
    return warns


def save_concept(book_title: str, ep: int, payload: dict[str, Any]) -> dict[str, Any]:
    """立意编辑保存 (人闸改判正身): 页面编辑 → 回写骨架 arcs + 投影立意.md。

    banned_motifs 只增不减 (重掷否决历史); visual_metaphor 重投影。"""
    doc = shots_mod.load_doc(book_title, ep)
    arcs = doc.get("arcs") or []
    by_id = {str(a.get("arc_id")): a for a in arcs}
    touched = 0
    for sc in payload.get("scenes") or []:
        a = by_id.get(str(sc.get("arc_id")))
        if a is None:
            continue
        old_banned = list(a.get("banned_motifs") or [])
        for k in ("narrative", "motion", "visual_world", "prop_lock") + _CONCEPT_KEYS:
            if k in sc and sc[k] is not None:
                if k == "metaphor_core" and str(sc[k]).strip() and a.get("metaphor_core") \
                        and str(sc[k]).strip() != str(a.get("metaphor_core")):
                    # 核心映射被人改掉 → 旧核心进禁用史 (重掷 LLM 不再回弹)
                    old_core = str(a["metaphor_core"]).strip()
                    if old_core not in old_banned:
                        old_banned.append(old_core)
                if isinstance(sc[k], list):
                    a[k] = [str(x).strip() for x in sc[k] if str(x).strip()]
                else:
                    a[k] = str(sc[k]).strip()
        if old_banned:
            a["banned_motifs"] = old_banned
        a["visual_metaphor"] = _project_visual_metaphor(a)
        touched += 1
    if payload.get("ep_summary"):
        doc["ep_summary"] = str(payload["ep_summary"]).strip()
    if touched:
        # 1013 一把尺: 手改立意后重算审计 — 旧版只活在规划里, 改好违规警告仍挂
        doc["_concept_warnings"] = _audit_concept(arcs)
        shots_mod.save(doc)
        write_concept_md(doc)
    logger.info("[concept-save] %d 场立意已回写骨架", touched)
    return {"touched": touched}


def reroll_concept(book_id: str, book_title: str, ep: int, arc_id: str,
                    direction: str = "") -> dict[str, Any]:
    """单场立意重掷 (0918 立意人闸闭环): 只重出该场隐喻, 其余场不动.

    LLM 输入 = 该场口播窗 + 全场隐喻板 (撞车防线) + 受众锚 + 用户改判方向
    ("不喜欢"的理由当指令); 产出回写骨架 arcs + 立意.md 同步重写。"""
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json
    doc = shots_mod.load_doc(book_title, ep)
    arcs = doc.get("arcs") or []
    idx = next((i for i, a in enumerate(arcs) if str(a.get("arc_id")).lower()
                == str(arc_id).strip().lower()), None)
    if idx is None:
        raise SystemExit(f"场不存在: {arc_id}")
    arc = arcs[idx]
    tc = float(load().brand_card.opening_sec)
    a0, a1 = float(arc["t_start"]) - tc, float(arc["t_end"]) - tc

    # 该场口播 (从 groups 取 narration — 骨架占位组携带)
    g = next((x for x in (doc.get("groups") or [])
              if str(x.get("arc_id")).lower() == str(arc_id).lower()), {})
    scene_text = str(g.get("narration") or "")[:1200]
    board = "\n".join(
        f"- {a['arc_id']}: 世界「{a.get('visual_world') or '未定'}」"
        f" | {str(a.get('metaphor_core') or a.get('visual_metaphor') or '')[:80]}"
        for a in arcs if a["arc_id"] != arc["arc_id"])
    wl_lines = [str(w) for w in (doc.get("world_library") or [])]
    wl_block = ("\n【全书视觉世界库 (新世界优先从这里选, 也可新增)】\n"
                + "\n".join(f"- {w}" for w in wl_lines) + "\n") if wl_lines else ""
    _aud = _audience_block(*_audience_rows(book_id))
    banned = [str(x) for x in (arc.get("banned_motifs") or [])]
    banned.append(str(arc.get("metaphor_core") or arc.get("visual_metaphor") or "旧隐喻"))
    ask = ("【单场立意重掷 — 只重出这一场的隐喻 (结构化多维度), 其余场不动】\n"
           f"场: {arc['arc_id']} 窗口 {a0:.1f}-{a1:.1f}s (音频轴)\n"
           f"本场口播:\n{scene_text}\n\n"
           f"【禁用意象 (历史否决, 核心意象禁止复用)】\n" + "\n".join(f"- {b[:90]}" for b in banned) + "\n\n"
           + (f"【用户改判方向 (必须遵守)】\n{direction}\n\n" if direction else "")
           + wl_block
           + _CARRIER_LAW
           + f"【其余场隐喻板 (新隐喻禁撞车; 视觉世界禁与相邻场相同 — 轮换宪法)】\n{board}\n"
           + _aud
           + "\n【输出】严格 JSON: {\"narrative\": \"一句话这场讲什么\", "
             "\"metaphor_core\": \"核心映射 A=B (一句话)\", "
             "\"metaphor_mappings\": [\"x=y\", \"x=y\"], "
             "\"carrier_lines\": [\"口播承载句1\", \"句2\"], "
             "\"visual_motifs\": [\"可直接画的物理画面1\", \"画面2\"], "
             "\"visual_world\": \"本场选用的视觉世界短名\", "
             "\"motion\": \"一句话场景里什么在动\"}")
    raw = _llm().chat(DIRECTOR_SYS_STRUCT, ask, model="pro", temperature=0.6, max_tokens=4000, tag="reroll_concept")
    data = _parse_json(raw) or {}
    core = str(data.get("metaphor_core") or "").strip()
    if not core:
        raise SystemExit("重掷产出无核心映射 — 再点一次即可 (零 GPU)")
    arcs[idx] = {**arc,
                 "narrative": str(data.get("narrative") or arc.get("narrative") or ""),
                 "metaphor_core": core,
                 "metaphor_mappings": [str(m) for m in (data.get("metaphor_mappings") or []) if str(m).strip()],
                 "carrier_lines": [str(c) for c in (data.get("carrier_lines") or []) if str(c).strip()],
                 "visual_motifs": [str(v) for v in (data.get("visual_motifs") or []) if str(v).strip()],
                 "visual_world": str(data.get("visual_world") or arc.get("visual_world") or "").strip()[:40],
                 "motion": str(data.get("motion") or arc.get("motion") or ""),
                 "banned_motifs": banned}
    arcs[idx]["visual_metaphor"] = _project_visual_metaphor(arcs[idx])
    # 1013 一把尺: 重掷后重算审计 — 单场换世界可能消/生相邻警告, 旧快照不再残留
    doc["_concept_warnings"] = _audit_concept(arcs)
    shots_mod.save(doc)
    write_concept_md(doc)  # 人读投影同步 (多维度渲染)
    logger.info("[concept-reroll] %s 立意已重掷 (结构化, 禁用史 %d 条) ✓",
                arc["arc_id"], len(banned))
    return {k: arcs[idx].get(k) for k in
            ("arc_id", "narrative", "metaphor_core", "metaphor_mappings",
             "carrier_lines", "visual_motifs", "visual_world", "motion", "banned_motifs")}


# 1014 概念层两法 (ep3 实锤: A3 镜子被立意定义为「发光显示场景」变屏幕 / s51 母题
# 「战场地图」经视觉孪生被生图闸连杀): plan_arcs 与 reroll_concept 同源注入。
_CARRIER_LAW = (
    "【载体诚实律】母题要展示『不在场/远程』的内容时, 载体必须是诚实的展示体"
    "(屏幕/投影/全息/门/时间裂缝/对话框/水晶球); 反射体(镜子/水面/金属面)只准"
    "反射在场之物(照见人物自身/本空间内实体), 禁『镜面显示文字和场景』"
    "『镜中映出另一地点』— 镜子当屏幕=载体与机制错配; 同一场禁两种模式混用。\n"
    "【敏感物防线】母题禁出现中国地图及带国界线/省界的地图 (1015 口径修正: 藏宝图/"
    "游戏示意地图不在此列), 禁国旗/红旗/领导人; 棋盘/沙盘上禁画疆域线 — "
    "带国界的画面会被生图硬闸连杀 (ep3 s51 实锤)。\n"
)


def plan_arcs(book_id: str, book_title: str, ep: int, ep_title: str,
              script: str, manifest: dict, bible: dict) -> dict[str, Any]:
    """两级化·第一级 (0917 用户令): 只切场 + 每场画面策略/隐喻, 不分镜.

    整集单窗大调用输出预算摊薄 → 镜少而长 + 提示词贫瘠 (0916 实锤 16镜vs35镜;
    0917 判定 A1-A7 提示词全军覆没, 单场重做技术合规显著更好 = 预算效应实锤)。
    两级化 = 本函数切场 (小输出) + plan_scene 逐场分镜 (单窗预算足)。
    输出骨架 doc: arcs (含 visual_metaphor) + 每场一个占位组 (窗口/口播原文,
    组 keyframe 留空由二级生成), shots 空。场窗时间做确定性归一: 首尾相接铺满
    [0, 真实集长], LLM 时间只当排序/比例参考 (防重叠缝隙)。
    """
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    segs = []
    t_cursor = 0.0
    for s in manifest.get("segments") or []:
        dur = float(s.get("duration") or 0)
        txt = str(s.get("text") or "").strip()
        segs.append({"t_start": round(t_cursor, 2), "t_end": round(t_cursor + dur, 2),
                     "duration": round(dur, 2), "text": txt,
                     "module_id": s.get("module_id")})
        t_cursor += dur
    total = round(max((s["t_end"] for s in segs if s["text"]), default=0.0), 2)

    # 0917 模块总线: manifest 带模块 → 场界=模块界 (系统定, LLM 零时间输出);
    # 旧 manifest (无 module_id) → 原路径 (LLM 自由切场 + 确定性归一)
    mod_wins = module_windows(segs)
    # 场列表文本 (模块定场模式喂 LLM, ep4 首踩 NameError 根修 0919):
    # 每场一行带口播全文 — carrier_lines 要求取本场原句, 截断会断粮
    scene_list = "\n".join(
        f"A{i + 1} | 模块{w['module_id']} | {w['t_end'] - w['t_start']:.1f}s | 口播: {w['text']}"
        for i, w in enumerate(mod_wins)) if mod_wins else ""
    # 0918 用户令: 立意层带入目标群 (L1 书定读者 + 老谭账号两轴池) — 隐喻戳中处境
    _aud_block = _audience_block(*_audience_rows(book_id))

    bible_block = ""
    if bible:
        # 1006 用户令 (卓有ep1 玻璃人 12/45 镜实锤): 圣经 style_prefix 是 SSOT —
        # 英文渲染词 (solid matte cartoon skin) 必须显式转译为中文铁律行注入,
        # 否则场规划 LLM 被隐喻吸引自造"半透明皮肤" (英文前缀约束力不够)
        _sp = str(bible.get("style_prefix") or "")
        _skin_rule = ""
        if "matte" in _sp or "solid" in _sp:
            _skin_rule = ("- **皮肤/材质铁律 (从风格前缀派生, keyframe_zh 里必须逐字遵守)**: "
                          "人物皮肤一律写\"实色哑光卡通皮肤\" — **禁写\"半透明/透明/琉璃/皮肤下透出/"
                          "皮肤内流动/能量纹路\"** — 能量流/流体/液态/金属感只许用于环境与道具, 不上身人物\n")
        bible_block = (f"\n【美术圣经 (全片统一, 每场隐喻与画面必须继承)】\n"
                       f"- 风格前缀: {bible.get('style_prefix', '')}\n"
                       + _skin_rule +
                       f"- 色彩: {bible.get('color_palette', '')}\n"
                       f"- 质感: {bible.get('texture', '')}\n"
                       f"- 角色策略: {bible.get('character_policy', '')}\n"
                       f"- 氛围: {bible.get('mood_keywords', '')}\n")
        # 0922 角色锁定卡 (用户令): 服饰+脸+道具关键词串全书带入 — 场景换了观众靠服饰认人;
        # 模型跨镜会漂, 关键词串大体锁形。出场角色在 keyframe_zh 里必须原样携带其 look。
        chars = bible.get("characters") or []
        if chars:
            lines = "\n".join(f"- {c.get('name')}（{c.get('role', '')}）: {c.get('look', '')}"
                              for c in chars if isinstance(c, dict) and c.get("look"))
            if lines:
                bible_block += ("【角色锁定卡 (出场角色的 keyframe_zh 必须原样携带其 look 关键词, "
                                "禁改名换装; 服装+脸+道具一起写进画面)】\n" + lines + "\n")
    # 0918 立意模块 v2: LLM 直出结构化多维度 (核心映射/分映射/承载句/画面母题),
    # visual_metaphor 由系统投影 (单一事实源, LLM 不再直出)
    _schema_out = ("\"metaphor_core\": \"核心映射 A=B (一句话)\", "
                   "\"metaphor_mappings\": [\"x=y\", \"x=y\"], "
                   "\"carrier_lines\": [\"口播承载句1\", \"句2\"], "
                   "\"visual_motifs\": [\"可直接画的物理画面1\", \"画面2\"], "
                   "\"visual_world\": \"本拍选用的视觉世界短名 (从世界库选, 相邻拍禁同世界)\", "
                   "\"prop_lock\": \"道具锁定卡: 本场主道具的唯一权威描述 (材质/颜色/结构/"
                   "尺寸全部定死, 含一句尺度锚如'站在脚边的人只及XX高度', 涉及多实体时"
                   "配角物件也定死固定色/造型 — 跨镜逐字复用防漂移)\", "
                   "\"motion\": \"一句话场景里什么在动\"")
    if mod_wins:
        user = ("【模式: 模块定场 (结构总线) — 场界已按六拍模块定死 (模块界=场界), "
                "你只做创作决策: 禁增删并场, 禁输出任何时间字段】\n"
                f"【场列表 (固定, 共 {len(mod_wins)} 场)】\n{scene_list}\n"
                "同模块多场=同一叙事段的不同画面面 (隐喻同源不同面, 禁断裂)。\n"
                + bible_block + _aud_block
                + _series_lock_block(book_title, ep)
                + "\n【硬约束】每场 metaphor_core 必填且互不重复; carrier_lines 取本场口播原句;"
                "场景库与场景选择 (立法分层·世界观层备货+场景组层选用): 世界观素材库备 8-12 个"
                "候选场景供多样性 (数量为指导非配额); 每个场景组(A1-AX)从库中选一个场景设定"
                "贯彻该组全部画面, 相邻场景组场景禁同; 时代属世界观层一書一基调 — 场景组换的是"
                "场景不是时代, 跨时代设定必须配标记转场; 主打场景只给钩子组与高潮组 (≤2/6); "
                "巨物静物/人物群像事件/空间战场/对比同屏 四种舞台语法组间轮换; "
                "visual_motifs 至少一半必须有人物在场 (人物操作/观察/与隐喻物件互动), "
                "纯物件空镜母题不得超过一半 — 分镜层人物在场配额闸执法 (1013)。\n"
                "北极星⑥: 隐喻载体必须生活常见、初中水平秒懂 (梯子/沙漏/天平/传送带级日常物), "
                "禁禅机级隐喻(文学化/需解读/抽象对抽象) — 需要琢磨才懂的比喻直接弃 (1013)。\n"
                + _CARRIER_LAW
                + "【输出】严格 JSON: {\"world_library\": [\"世界短名:一句话映射\", ...共8-12个], "
                  "\"ep_summary\": \"一句话本集画面策略\", \"arcs\": [{\"arc_id\": \"A1\", "
                  "\"narrative\": \"一句话这场讲什么\", " + _schema_out + "}]} — arc_id 逐场对位 A1.."
                  f"A{len(mod_wins)}。")
    else:
        user = ("【模式: 只切场 (两级化第一级) — 只执行到第三步 (切场 + 画面策略 + 定隐喻), "
                "禁止输出 groups/shots】\n"
                f"【稿件】\n{script[:3000]}\n\n"
                "【TTS manifest (时间骨架, 精确到 0.1s; 场边界参考包边界, t 用音频轴 0 起)】\n"
                + "\n".join(json.dumps({"t_start": s["t_start"], "t_end": s["t_end"],
                                        "duration": s["duration"], "text": s["text"][:120]},
                                       ensure_ascii=False) for s in segs)
                + bible_block + _aud_block
                + _series_lock_block(book_title, ep)
                + f"\n【硬约束】场与场首尾相接铺满 0 → {total:.1f}s, 不重叠不留缝; 通常 4-9 场; "
                  "每场 metaphor_core 必填且互不重复; carrier_lines 取本场口播原句。\n"
                + "【输出】严格 JSON: {\"ep_summary\": \"一句话本集画面策略\", \"arcs\": [{\"arc_id\": \"A1\", "
                  "\"narrative\": \"一句话这场讲什么\", " + _schema_out + ", \"t_start\": 0.0, \"t_end\": 24.3}]}")
    logger.info("[plan-arcs] 一级切场中… (LLM 1-2 分钟静默属正常)")
    raw = _llm().chat(DIRECTOR_SYS_STRUCT, user, model="pro", temperature=0.5, max_tokens=8000, tag="plan_arcs")
    data = _parse_json(raw) or {}
    # 立意确认轮 (0917 用户令 "先答个明白再动笔"): 多轮 — 模型看着自己的初稿
    # 把隐喻映射答明白并自检修正, 误解在零成本时暴露 (好于画完 47 张图再打回)。
    # 历史携带原 user (DeepSeek KV 前缀缓存可命中); 失败沿用初稿不炸。
    try:
        raw2 = _llm().chat_turns(
            [{"role": "system", "content": DIRECTOR_SYS_STRUCT},
             {"role": "user", "content": user},
             {"role": "assistant", "content": str(raw)[:12000]},
             {"role": "user", "content":
              "【立意确认轮 — 不画任何镜, 只把隐喻答明白】先逐场简答: ①映射关系"
              "(什么=什么, 口播里哪句话承载) ②与前后场隐喻是否重复/断裂 ③是否踩"
              "黑名单(商务素材/政治禁区/国旗国徽领导人) ④口播关键句能否落进画面。"
              "答完后输出修正版完整 arcs JSON (格式与初稿一致; 只许改 narrative/"
              "metaphor_core/metaphor_mappings/carrier_lines/visual_motifs/"
              "visual_world/motion, 不许改 arc_id 和 t) — 只输出 JSON。"}],
            model="pro", temperature=0.4, max_tokens=8000, tag="plan_arcs")
        data2 = _parse_json(raw2) or {}
        if data2.get("arcs"):
            data, raw = data2, raw2
            logger.info("[plan-arcs] 立意确认轮 ✓ 隐喻已自检修正")
    except Exception as exc:  # noqa: BLE001 — 确认轮失败沿用初稿
        logger.warning("[plan-arcs] 立意确认轮失败 (沿用初稿): %s", exc)
    raw_arcs = [a for a in (data.get("arcs") or []) if a.get("arc_id")]
    if not raw_arcs:
        raise SystemExit("切场产出不可解析 (无 arcs) — 重跑 plan 即可 (零 GPU)")
    raw_arcs.sort(key=lambda a: float(a.get("t_start") or 0))
    tc = float(load().brand_card.opening_sec)
    arcs: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    if mod_wins:
        # 模块定界 (0917): 场窗=模块窗 (系统), LLM arcs 按 arc_id 对位只填创作字段 —
        # 时间零参与, 结构上不可能漂移; 对位缺场 = 字段空, 立意.md 人闸可见。
        by_id = {str(a.get("arc_id") or "").upper(): a for a in raw_arcs}
        cursor = 0.0
        for i, w in enumerate(mod_wins):
            a = by_id.get(f"A{i + 1}", {})
            a0 = w["t_start"]
            t1 = w["t_end"]
            cursor = t1
            arc_new: dict[str, Any] = {"arc_id": f"A{i + 1}", "module_id": w["module_id"],
                                       "narrative": str(a.get("narrative") or ""),
                                       "motion": str(a.get("motion") or ""),
                                       "t_start": round(tc + a0, 2), "t_end": round(tc + t1, 2)}
            for k in _CONCEPT_KEYS:  # 立意模块 v2: 结构化维度透传 (列表清洗)
                if a.get(k):
                    arc_new[k] = ([str(x).strip() for x in a[k] if str(x).strip()]
                                  if isinstance(a[k], list) else str(a[k]).strip())
            if str(a.get("visual_world") or "").strip():  # 0926 轮换宪法: 世界字段透传
                arc_new["visual_world"] = str(a["visual_world"]).strip()[:40]
            if str(a.get("prop_lock") or "").strip():  # 0926 道具锁定卡透传
                arc_new["prop_lock"] = str(a["prop_lock"]).strip()[:400]
            arc_new["visual_metaphor"] = _project_visual_metaphor(arc_new)
            arcs.append(arc_new)
            groups.append({"group_id": f"S{i + 1:02d}", "arc_id": f"A{i + 1}",
                           "module_id": w["module_id"],
                           "t_start": round(tc + a0, 2), "t_end": round(tc + t1, 2),
                           "narration": w["text"][:600], "camera": "固定",
                           "keyframe_zh": "", "motion_zh": "", "shot_ids": []})
        _tail_gap = round(total - cursor, 2)
        if abs(_tail_gap) > 0.5:
            logger.warning("[plan-arcs] 模块窗尾 %.1fs ≠ manifest 总长 %.1fs (差 %.1fs)",
                           cursor, total, _tail_gap)
    else:
        cursor = 0.0
        for i, a in enumerate(raw_arcs):
            last = i == len(raw_arcs) - 1
            if last:
                t1 = total
            else:
                want = max(float(a.get("t_end") or 0), cursor + 3.0)
                rest = len(raw_arcs) - i
                t1 = min(max(want, cursor + (total - cursor) / rest), total)
            t1 = round(max(t1, cursor + 1.0), 2)
            a0 = round(cursor, 2)
            window_text = "".join(s["text"] for s in segs
                                  if s["t_end"] > a0 + 0.01 and s["t_start"] < t1 - 0.01)[:600]
            arcs.append({**a, "t_start": round(tc + a0, 2), "t_end": round(tc + t1, 2)})
            groups.append({"group_id": f"S{i + 1:02d}", "arc_id": a.get("arc_id"),
                           "t_start": round(tc + a0, 2), "t_end": round(tc + t1, 2),
                           "narration": window_text, "camera": "固定",
                           "keyframe_zh": "", "motion_zh": "", "shot_ids": []})
            cursor = t1
    doc = {
        "version": shots_mod.SCHEMA_VERSION,
        "book_id": book_id, "book_title": book_title, "ep": ep, "ep_title": ep_title,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "planner_flow": ("director2 模块总线 (场=六拍模块, 系统定界)"
                         if mod_wins else "director2 arcs v2 两级化 (切场+逐场分镜)"),
        "planner_model": "pro",
        "ep_summary": data.get("ep_summary", ""),
        "world_library": [str(w)[:80] for w in (data.get("world_library") or [])
                          if str(w).strip()][:12],
        "style_recipe": resolve_style(book_title).get("name"),
        "bible": bible,
        "arcs": arcs,
        "groups": groups,
        "shots": [],
    }
    # 立意审计 (0917 隐喻查重 + 0926 世界轮换): 一把尺 _audit_concept — 与
    # save_concept/reroll_concept 共用 (1013 抽出, 旧内联版三路各自为政)
    concept_warns: list[str] = _audit_concept(arcs)
    # 1013 修: 无条件写 — 旧版 `if concept_warns:` 只写不清, 空结果时旧警告残留
    doc["_concept_warnings"] = concept_warns
    # 立意案落盘 (0917 人闸): 人读表 — 确认/改判后再续跑分镜
    try:
        ep_dir = shots_mod.ep_dir(book_title, ep)
        ep_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# 立意案 (两级化·第一级产物 — 人闸读物)", "",
                 f"> 本集策略: {data.get('ep_summary', '')}", ""]
        for a in arcs:
            lines.append(f"## {a['arc_id']} ({float(a['t_end']) - float(a['t_start']):.0f}s) "
                         f"{a.get('narrative', '')}")
            lines.append(f"- **隐喻**: {a.get('visual_metaphor', '(缺 — 必须补)')}")
            lines.append(f"- 运动线: {a.get('motion', '')}")
            lines.append("")
        if concept_warns:
            lines += ["## ⚠ 立意警告 (隐喻缺失/撞车)", ""] + [f"- {w}" for w in concept_warns]
        (ep_dir / "立意.md").write_text("\n".join(lines), encoding="utf-8")
        logger.info("[plan-arcs] 立意案已落盘 → %s", (ep_dir / "立意.md").name)
    except Exception as exc:  # noqa: BLE001 — 落盘失败不炸规划
        logger.warning("[plan-arcs] 立意.md 落盘失败: %s", exc)
    logger.info("[plan-arcs] ✓ %d 场 铺满 %.1fs: %s", len(arcs), total,
                " | ".join(f"{a['arc_id']}:{float(a['t_end']) - float(a['t_start']):.0f}s"
                           for a in arcs))
    return doc


def plan(book_id: str, book_title: str, ep: int, ep_title: str,
         script: str, manifest: dict, bible: dict) -> dict[str, Any]:
    """导演规划 (弧+运动+分镜, LLM) → 完整 doc (含产线生产字段)."""
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    segs = []
    t_cursor = 0.0
    for s in manifest.get("segments") or []:
        dur = float(s.get("duration") or 0)
        txt = str(s.get("text") or "").strip()
        segs.append({"t_start": round(t_cursor, 2), "t_end": round(t_cursor + dur, 2),
                     "duration": round(dur, 2), "text": txt})  # 全文 (prompt 侧截, 对账用全量
        t_cursor += dur

    bible_block = ""
    if bible:
        bible_block = (f"\n【美术圣经 (全片统一, 每镜必须继承)】\n"
                       f"- 风格前缀: {bible.get('style_prefix', '')}\n"
                       f"- 色彩: {bible.get('color_palette', '')}\n"
                       f"- 质感: {bible.get('texture', '')}\n"
                       f"- 角色策略: {bible.get('character_policy', '')}\n"
                       f"- 氛围: {bible.get('mood_keywords', '')}\n")
        # 0923 修 (用户实锤 货不对版): 本站点漏了角色锁定卡 — 规划层 look 串全丢
        chars = bible.get("characters") or []
        if chars:
            _lines = "\n".join(f"- {c.get('name')}（{c.get('role', '')}）: {c.get('look', '')}"
                               for c in chars if isinstance(c, dict) and c.get("look"))
            if _lines:
                bible_block += ("【角色锁定卡 (出场角色的 keyframe_zh 必须原样携带其 look 关键词串, "
                                "禁只写名字不写造型 — 服装+脸+道具一起入画)】\n" + _lines + "\n")
    user = (f"【稿件】\n{script[:3000]}\n\n"
            f"【TTS manifest (时间骨架, 精确到 0.1s)】\n"
            + "\n".join(json.dumps({"t_start": s["t_start"], "t_end": s["t_end"],
                                    "duration": s["duration"], "text": s["text"][:120]},
                                   ensure_ascii=False) for s in segs)
            + bible_block
            + _series_lock_block(book_title, ep))
    logger.info("[plan] 导演规划中… (LLM pro 链, 整集一次大调用, 2-4 分钟无中间输出属正常)")
    raw = _llm().chat(DIRECTOR_SYS, user, model="pro", temperature=0.5, tag="plan")
    logger.info("[plan] 导演返回 %d 字符, 建档展开…", len(raw or ""))
    data = _parse_json(raw) or {}
    if not (data.get("groups") or data.get("shots")):
        raise SystemExit("导演产出不可解析 (无 groups/shots) — 重跑 plan 即可 (零 GPU)")
    doc = _build_doc(book_id, book_title, ep, ep_title, data, bible)
    # 0916: 句级 segs (巨型多句包的包内插值=漂移/劈句之源; 与 align/本场重规划同源)
    try:
        from app.services import anim_draft as _ad
        _files = _ad.fetch_episode_audio(book_id, ep)["files"]
        _sseg = _ad._sentence_segs(_files)
        if _sseg:
            segs = _sseg
    except Exception as exc:  # noqa: BLE001
        # 0919 弃包级回退 (同 plan_scene: 包级吸附=剔镜漂组窗破坏模式)
        raise SystemExit(f"句级 segs 失败 (包级回退已废): {exc}") from exc
    _realign_narrations(doc, segs)  # 字符级对账: 时间/narration 按 manifest 确定性重切
    # 0917: 拆镜分身不是复印机 — 克隆家系当场批量换构图 (失败=保持克隆, 软警告亮灯)
    try:
        redesign_split_clones(doc, bible)
    except Exception as exc:  # noqa: BLE001 — 重设计失败不炸整集规划 (克隆+警告兜底)
        logger.warning("[plan] 拆镜分身重设计失败 (保持克隆, 软警告亮灯): %s", exc)
    return doc


def plan_shot(book_id: str, book_title: str, ep: int, shot_id: str,
              manifest: dict, bible: dict) -> dict[str, Any]:
    """单镜重规划 (0915 用户令: 一镜设计坏了, 不想整场陪葬).

    钉死: 时间槽 [t0,t1] / 口播 narration / shot_id (原位替换, 页面槽位不动).
    重设计: keyframe + motion + camera + text_layer; 兄弟镜/图全不动。
    返回整个 doc (调用方校验后落盘)。
    """
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    doc = shots_mod.load_doc(book_title, ep)
    try:
        s = shots_mod.find_shot(doc, shot_id)
    except KeyError as exc:
        raise SystemExit(f"镜不存在: {shot_id}") from exc
    t0, t1 = float(s["t_start"]), float(s["t_end"])
    span = round(t1 - t0, 2)
    tc = float(load().brand_card.opening_sec)
    arc = next((a for a in doc.get("arcs") or []
                if str(a.get("arc_id", "")) == str(s.get("arc_id"))), {})
    group = next((g for g in doc.get("groups") or []
                  if g.get("group_id") == s.get("group_id")), {})
    ordered = sorted(doc["shots"], key=lambda x: float(x["t_start"]))
    oi = ordered.index(s)
    prev_kf = str(ordered[oi - 1].get("keyframe_zh") or "")[:70] if oi > 0 else ""
    nxt_kf = str(ordered[oi + 1].get("keyframe_zh") or "")[:70] if oi + 1 < len(ordered) else ""

    bible_block = ""
    if bible:
        bible_block = ("- 风格: " + str(bible.get("style_prefix", ""))[:100]
                       + " | 色彩: " + str(bible.get("color_palette", ""))[:80])

        _chars = bible.get("characters") or []
        if _chars:
            _ls = "; ".join(f"{c.get('name')}({c.get('look', '')})" for c in _chars if c.get("look"))
            if _ls:
                bible_block += chr(10) + "- 角色锁定卡 (出场角色 keyframe_zh 原样携带 look): " + _ls
    # 1004 修 (s16 死循环实锤): 违禁判决反馈 — 旧设计被图片守门连杀的镜, 重规划
    # 必须根除致死载体 (同类/同观感/同视角都禁), 换隐喻载体重新表达 — 否则换seed=
    # 同构图=再杀, 死循环。
    _gr = s.get("gate_rejects") or []
    _gate_fb = ""
    if _gr:
        from collections import Counter as _Ctr
        _cnt = _Ctr(str(r.get("reason") or "?") for r in _gr)
        _gate_fb = ("【违禁审判反馈 (旧设计已被图片守门连杀 — 新设计必须根除, 这是最优先约束)】"
                    + "; ".join(f"{k}×{v}" for k, v in _cnt.items()) + chr(10)
                    + "上述元素/观感一个都不能再出现 (含相似变体: 同类载体/同轮廓/同视角全貌); "
                      "换一个完全不同的隐喻载体表达同样的信息, 构图与旧版显著错开。" + chr(10))
    user = ("【模式: 单镜重规划】这一镜的设计坏了, 只重新设计这一镜 (整集其余全部不动)。" + chr(10)
            + "【本镜口播 (钉死不改)】" + str(s.get("narration") or "")[:120] + chr(10)
            + "【本镜时长】" + f"{span}s (音频轴 {t0 - tc:.1f}-{t1 - tc:.1f}s, 时间钉死)" + chr(10)
            + "【所在场】" + str(arc.get("narrative") or "")[:60] + " | 场景定调: "
            + str(group.get("keyframe_zh") or "")[:80] + chr(10)
            + (("[\"邻镜画面 (构图要衔接但错开)] 前镜: " + prev_kf + " / 后镜: " + nxt_kf + chr(10)) if (prev_kf or nxt_kf) else "")
            + "【旧版本镜 (打磨不是复读, 新构图显著错开)】" + str(s.get("keyframe_zh") or "")[:100] + chr(10)
            + _gate_fb
            + "【圣经】" + bible_block + chr(10)
            + "【输出】严格 JSON 单对象: {\"keyframe_zh\": \"首帧(具象+灭字+视角锁死+信息完备)\", "
              "\"keyframe_en\": \"English, no text\", \"motion_zh\": \"动画层+文字事件(一镜一事)\", "
              "\"motion_en\": \"English ambient + at most ONE text event\", \"camera\": \"...\", "
              "\"text_layer\": [{\"kind\":\"hero_number|caption|...\",\"text\":\"..\",\"t_start\":0.5,\"t_end\":2.0}]}")
    logger.info("[plan-shot] %s 单镜重规划中… (槽 %.1f-%.1fs 钉死, LLM 30-60s)", shot_id, t0, t1)
    raw = _llm().chat(DIRECTOR_SYS, user, model="pro", temperature=0.5, max_tokens=2000, tag="plan_shot")
    d = _parse_json(raw) or {}
    kf_zh = str(d.get("keyframe_zh") or "").strip()
    motion_en = str(d.get("motion_en") or "").strip() or "Gentle ambient motion, very slight push-in."
    if not kf_zh:
        raise ValueError("导演产出不可解析 (无 keyframe_zh)")

    gen = min(span, H3_MAX_SPAN)
    s.update({
        "keyframe_zh": kf_zh,
        "keyframe_en": str(d.get("keyframe_en") or "").strip(),
        "image_prompt_zh": kf_zh,
        "motion_zh": str(d.get("motion_zh") or "")[:300],
        "camera": d.get("camera") or s.get("camera") or "固定",
        # 1014 修: 同场合并口 — text_layer 字符串条目归一化 (t.get 下游全线炸)
        "text_layer": [{"text": str(x)} if isinstance(x, str) else x
                       for x in (d.get("text_layer") or [])
                       if isinstance(x, (str, dict))],
        # 1015 修: 同上场合并口 — in_image_text 申报必须存活 (二段式断链根因)
        "in_image_text": [str(x).strip() for x in (d.get("in_image_text") or [])
                          if str(x).strip()][:4],
        "h3_text_event": ({k: v for k, v in (d.get("h3_text_event") or {}).items()
                          if k in ("text", "at_s", "zone")}
                         if isinstance(d.get("h3_text_event"), dict) else {}),
        "political_flag": False,  # 0924 修: 重规划成功即清政治标记 (旧标记残留致K2永远跳过)
        "gate_rejects": [],       # 1004 修: 新设计=判决清零 (旧判决不拖累新生图)
        "anim": {
            "duration_s": gen,
            "opening_desc": str(d.get("keyframe_en") or kf_zh),
            "physical_lock": "the camera viewpoint, the main structural elements and layout",
            "screen_exception": "nothing",
            "beats": [{"t_start": 0, "t_end": gen, "motion": motion_en}],
            "ending": "the scene holds with gentle ambient light variation",
        },
        "seed": shots_mod.new_seed(),
        "h3_seed": shots_mod.new_seed(),
        "status": "planned",
        "image_file": None, "video_file": None,
        "attempts": {"k2": 0, "h3": 0},
        "error": None, "reject_note": "",
    })
    s.pop("overrun_s", None)
    _clamp_motion_events(s)
    _gate_motion_text(s)  # 单镜重规划同为新 LLM 产出 — 浮字军规同闸
    _ensure_motion_text(s)  # 0920 补字回填: 军规只拦不补, 合规短标确定性注入
    return doc


def _sys_prompt(mode: str) -> str:
    """plan_scene 系统提示选路 (1013 P0): writer=单写者全典, struct=现行结构版。"""
    if mode == "writer":
        from .scene_writer import WRITER_SYS  # 延迟导入防环
        return WRITER_SYS
    return DIRECTOR_SYS_STRUCT


def _plan_tokens(mode: str, n_shots: int = 0) -> int:
    """writer 五件套输出预算 — 按镜量缩放 (1014 ep3 实锤: 旧定额 16000 按 ×8 镜
    设计, 15-16 槽大场需求翻倍 → 必截断 'Extra data' 两把全灭退 fallback —
    A3/A4 共 28/54 镜降级参数表风格)。≤9 镜维持 16000 实战值不回归; 大场
    ≈1800 token/镜; 帽 30000 — 平台若拒则报错进重试网, 不劣于静默截断)。"""
    if mode != "writer":
        return 8192
    if n_shots <= 9:
        return 16000
    return min(30000, 2400 + 1800 * n_shots)


def plan_scene(book_id: str, book_title: str, ep: int, arc_id: str,
               manifest: dict, bible: dict,
               fix_hints: list[str] | None = None,
               mode: str = "struct") -> dict[str, Any]:
    """单场重规划 (逐场磨合): 只重生该场的组/镜, 其余场原样拼接保留.

    磨合循环 = 本场 规划→K2→H3 反复测. 链路是全集 plan 的单窗版:
    同一 DIRECTOR_SYS 宪法/圣经; 新组镜用全局新 id (旧 id 退役, 防与存量撞);
    进场先把全集槽镜按当版句级表重钉 (1001 修: 音频重生成后旧账窗界会切进
    邻场句包, 同版音频恒等不扰); 校验在调用方 (planner.replan_scene 重试网),
    本函数只产 doc 不落盘.
    mode (1013 P0): "struct"=现行结构版 (宪法禁逐镜keyframe, 术/器下游组装);
    "writer"=单写者版 (scene_writer.WRITER_SYS 五件套一次写全, 宪法分区注入,
    person_rule 让位) — 场窗/槽表/合并基建两模式完全共享。
    """
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    doc = shots_mod.load_doc(book_title, ep)
    arcs = doc.get("arcs") or []
    key = str(arc_id).strip().lower()
    idx = next((i for i, a in enumerate(arcs) if str(a.get("arc_id", "")).lower() == key), None)
    if idx is None:
        raise SystemExit(f"场不存在: {arc_id} (已有 {[a.get('arc_id') for a in arcs]})")
    arc = arcs[idx]
    g_all = doc.get("groups") or []
    s_groups = sorted([g for g in g_all if str(g.get("arc_id", "")).lower() == key],
                      key=lambda g: float(g["t_start"]))
    if not s_groups:
        raise SystemExit(f"场 {arc_id} 没有组 (旧数据?)")
    # 1004 修: 场窗 = 组/镜界 ∪ 骨架场窗。镜界是"已覆盖"下界, 骨架 (切场+立意
    # 人闸确认) 是场界权威 — 只按镜/组界取窗会把历史欠账 (品牌句守卫钳断/塌缩
    # 剔除) 永久固化: 定位 ep5 A2 实锤, 镜界止 62.2 骨架止 108.9, 46.7s 中段
    # 口播无主 → A3 分镜死锁 ×2。取并集, 超长部分由品牌句原子槽/装包器接管,
    # 邻场边界由场窗连续性闸 (validate_doc) 背书。
    t0 = min([float(g["t_start"]) for g in s_groups] + [float(arc.get("t_start") or 0)])
    t1 = max([float(g["t_end"]) for g in s_groups] + [float(arc.get("t_end") or 0)])
    tc = float(load().brand_card.opening_sec)

    segs = []
    t_cursor = 0.0
    for s in manifest.get("segments") or []:
        dur = float(s.get("duration") or 0)
        segs.append({"t_start": round(t_cursor, 2), "t_end": round(t_cursor + dur, 2),
                     "duration": round(dur, 2), "text": str(s.get("text") or "").strip()})
        t_cursor += dur
    # 0917 refs 协议: 句级表 (包实测+静音吸附) 在场窗内局部编号 — LLM 只引句号,
    # 零绝对时间输出; 展开/校验/密度执法在 expand_refs_groups (确定性)。
    sent_table = list(manifest.get("sent_table") or [])

    # ── 1001 修 (ep4 s99/s83 双品牌卡实锤): 单场重规划必须重新代入音频时长 ──
    # 音频在规划后重生成 → doc 时间轴=旧账; 槽镜 (zone/packed) 连归真都跳过
    # (0919 槽位神圣), 旧组界取窗必切进邻场句包 (仪式句包被 s99 切走 89%)。
    # ① 全集槽镜按新句级表重钉 (restamp_slots, 同版音频恒等); ② 窗界改从重钉
    # 后的镜界取 (组 t 不随归真, 以镜为准); ③ 外来品牌句包 (本场镜不持有的
    # 身份句/收官句) 钳在窗外 — 品牌句归签名镜独占。
    arc_shots = [s for s in doc["shots"]
                 if str(s.get("arc_id", "")).lower() == key]
    if sent_table and arc_shots:
        from app.services import anim_draft as _ad
        _rs = _ad.restamp_slots(doc, sent_table, tc)
        if _rs["moved"]:
            logger.warning("[replan] 音频漂移 → 槽镜重钉 %d 镜 (%s)",
                           len(_rs["moved"]), ",".join(_rs["moved"][:12]))
        if _rs["skipped"]:
            logger.warning("[replan] 槽镜文本锚失败 %d 镜 (不动, 人检): %s",
                           len(_rs["skipped"]), ",".join(_rs["skipped"][:12]))
        arc_shots = [s for s in doc["shots"]
                     if str(s.get("arc_id", "")).lower() == key]
        # 1004 修同上: 重钉后的镜界只是覆盖下界, 并上骨架场窗 (历史欠账回填)
        t0 = min([float(s["t_start"]) for s in arc_shots] + [float(arc.get("t_start") or 0)])
        t1 = max([float(s["t_end"]) for s in arc_shots] + [float(arc.get("t_end") or 0)])
    a0, a1 = t0 - tc, t1 - tc  # 音频轴窗 (LLM 用音频轴, 拼装时 +tc 归一)
    # 外来品牌句包守卫: 窗界切进品牌句包内部 → 钳回包界 (品牌句不得被窗界腰斩);
    # 完整落在窗内 → 不钳, 本场接手 (hook_zone 原子槽保证品牌句独占一镜,
    # splice 后 tag_brand_cards 确定性定卡)。1004 定位 ep5 教训: 旧版一律钳回,
    # 中段身份句 (56.7s "我是老谭…") 把 A2 场窗拦腰砍 46.7s → 骨架洞 → A3 分镜
    # 死锁 ×2。钳回只在腰斩时发生; 若钳回造洞, validate_doc 场窗连续性闸会拦下。
    for _b0, _b1 in shots_mod.brand_pack_spans(sent_table):
        if not (a0 < _b0 - 0.5 and a1 > _b0 - 0.01):
            continue  # 品牌句包不在窗内
        if a1 >= _b1 - 0.01:
            continue  # 完整落在窗内 → 本场接手 (原子槽成镜)
        logger.warning("[replan] 场 %s 窗界 [%.1f,%.1f]s 切进品牌句包 [%.1f,%.1f]s → "
                       "钳回包界, 品牌句留给邻场签名镜",
                       arc["arc_id"], a0, a1, _b0, _b1)
        a1 = round(float(_b0), 2)
        t1 = round(tc + a1, 2)
        break
    win_segs = [s for s in segs if s["t_end"] > a0 - 8 and s["t_start"] < a1 + 8]
    # 0919 60s 特区 (用户令): 场窗与正文 [0,60s) 相交 → 镜槽系统预装, LLM 只填画面;
    # 场结构 (A1/A2 界) 不动 — 特区是镜级规则。装包失败静默降级走旧 refs 协议。
    from app.services.anim_pipeline import hook_zone as _HZ
    zone_slots: list[dict[str, Any]] = []
    if a0 < _HZ.ZONE_END - 0.01:
        try:
            zone_slots = _HZ.pack_zone_slots(sent_table, a0, a1)
        except Exception as exc:  # noqa: BLE001 — 装包失败不炸场, 走旧协议
            logger.warning("[replan] 场 %s 特区装包失败 (降级旧协议): %s",
                           arc["arc_id"], exc)
    zone_tail = zone_slots[-1]["t_end"] if zone_slots else a0
    # 0919 全片装包制 (用户令: LLM 零时长决策): 特区外余窗同 DP 装包 — 镜界
    # 全系统定, LLM 只填画面; refs 协议 (LLM 组句) 退役, 四集均值漂移根治
    scene_slots: list[dict[str, Any]] = []
    if a1 - zone_tail > 0.1:
        try:
            scene_slots = _HZ.pack_zone_slots(sent_table, zone_tail, a1, zone_end=a1)
        except Exception as exc:  # noqa: BLE001 — 装包失败走旧 refs 兜底
            logger.warning("[replan] 场 %s 余窗装包失败 (降级 refs): %s",
                           arc["arc_id"], exc)
    slot_mode = bool(zone_slots or scene_slots)
    refs_mode = bool(sent_table) and not slot_mode
    refs_block = ""
    if refs_mode:
        _win_sents = [s for s in sent_table
                      if float(s.get("t_end", 0)) > zone_tail + 0.01
                      and float(s.get("t_start", 0)) < a1 - 0.01]
        if not _win_sents:
            refs_mode = False
        else:
            table_txt = "\n".join(
                f"c{i + 1:02d} | {float(s['t_start']):.1f}-{float(s['t_end']):.1f}s | "
                f"{str(s.get('text') or '')[:60]}"
                for i, s in enumerate(_win_sents))
            refs_block = (f"\n【句级时间表 (本场, 音频轴, 精确实测) — 镜边界只许落在句边界上】\n"
                          f"{table_txt}\n"
                          f"【refs 输出制 (覆盖全局格式中的时间字段)】每镜必须给 \"refs\": "
                          f"\"c01-c02\" (引句号区间, 可跨句); **禁输出 t_start/t_end/narration** "
                          f"— 时间轴与口播原文由系统查表展开, 你报的时间一律丢弃。"
                          f"镜时长 = 引用句实测秒数: 单镜至少引够 4s (密度宪法, 短句必须与邻句同镜), "
                          f"一般 4-8s 一镜; 全部 c01-c{len(_win_sents)} 必须被引用 (漏句系统会并邻镜, "
                          f"尽量自己规划好)。\n")

    # 0919 60s 特区注入: 槽位表 + 特区宪法 (覆盖"场内共享场景"等冲突条款)
    zone_block = ""
    if zone_slots:
        _tbl = "\n".join(
            f"槽{k + 1} | {s['t_start']:.1f}-{s['t_end']:.1f}s ({s['t_end'] - s['t_start']:.1f}s) | "
            f"{(str(s['text'] or '').strip() or '(垫静音段)')[:56]}"
            for k, s in enumerate(zone_slots))
        _tail_note = (f"槽后剩余段 ({a1 - zone_tail:.1f}s) 已由系统装包为普通槽 "
                      f"(槽号接续特区, 见普通镜槽表)。"
                      if scene_slots else "本场全部为特区镜。")
        zone_block = (f"\n{_HZ.ZONE_RULES}\n"
                      f"【镜槽表 (系统已定, 音频轴) — 一槽一镜, 禁增删并、禁改镜界】\n{_tbl}\n"
                      f"【特区输出制 (优先于 refs 制)】特区镜 ({len(zone_slots)} 个) 一律放进 groups[0], "
                      f"每镜给 \"slot\": 槽号(1-{len(zone_slots)}), **禁输出 t_start/t_end/narration/refs** "
                      f"— 时间与口播由系统按槽回填; groups[0].keyframe_zh 写特区蒙太奇主题。{_tail_note}\n")
    # 0919 普通镜槽表 (全片装包制): 非特区段同 slot 制 — LLM 只填画面
    scene_block = ""
    if scene_slots:
        _off = len(zone_slots)
        _tbl2 = "\n".join(
            f"槽{_off + k + 1} | {s['t_start']:.1f}-{s['t_end']:.1f}s ({s['t_end'] - s['t_start']:.1f}s) | "
            f"{(str(s['text'] or '').strip() or '(垫静音段)')[:56]}"
            for k, s in enumerate(scene_slots))
        scene_block = (f"\n【普通镜槽表 (系统已定, 音频轴) — 一槽一镜, 禁增删并、禁改镜界】\n{_tbl2}\n"
                       f"【普通镜输出制】普通镜 ({len(scene_slots)} 个) 放进 groups[1+] (可自行分组), "
                       f"每镜给 \"slot\": 槽号({_off + 1}-{_off + len(scene_slots)}), "
                       f"**禁输出 t_start/t_end/narration/refs** — 时间与口播由系统按槽回填; "
                       f"每镜画面必须承接本场隐喻且与前镜构图错开。\n")

    scene_text = "\n".join(g.get("narration") or "" for g in s_groups)  # 已对齐的真实音频原文
    old_kf = "\n".join(f"- {g['group_id']}: {(g.get('keyframe_zh') or '')[:60]}" for g in s_groups)
    prev_arc = arcs[idx - 1] if idx > 0 else None
    next_arc = arcs[idx + 1] if idx + 1 < len(arcs) else None

    bible_block = ""
    if bible:
        bible_block = (f"\n【美术圣经 (全片统一, 每镜必须继承)】\n"
                       f"- 风格前缀: {bible.get('style_prefix', '')}\n"
                       f"- 色彩: {bible.get('color_palette', '')}\n"
                       f"- 质感: {bible.get('texture', '')}\n"
                       f"- 角色策略: {bible.get('character_policy', '')}\n"
                       f"- 氛围: {bible.get('mood_keywords', '')}\n")
        # 0923 修: 单场重规划漏角色锁定卡 — 与全集规划对齐 (货不对版根修, 全站点覆盖)
        chars = bible.get("characters") or []
        if chars:
            _lines = "\n".join(f"- {c.get('name')}（{c.get('role', '')}）: {c.get('look')}"
                               for c in chars if isinstance(c, dict) and c.get("look"))
            if _lines:
                bible_block += ("【角色锁定卡 (出场角色的 keyframe_zh 必须原样携带其 look 关键词串, "
                                "禁只写名字不写造型)】\n" + _lines + "\n")
    # 1013 人物在场铁律 (A1 全场空镜实锤): character_policy 是散文, 确定性闸在
    # validate_doc (每场 ≥⌈n/4⌉ 镜人物在场, 不达标整场带反馈重掷) — 此处结构注入
    # 降低重掷率; 纯物件/空镜镜合法 (PPT三层物品动层), 但不许全场无人
    person_rule = ("\n【人物在场铁律 (确定性闸执法, 违者整场重掷)】本场至少四分之一的镜必须"
                   "有人物出场 (露脸/有表情/带姿态, 携带角色锁定卡 look 与圣经角色同源, "
                   "人物与隐喻物件互动优先); 纯物件镜与空镜合计不得超过四分之三, "
                   "开场场与钩子场人物须更早出场建立叙述锚。\n")
    # 1013 P0 阶段2: writer 模式人物观让位分区宪法 (唯一源; 两套散文人物观退场)
    # 试点实锤: 须带 bible 注入替身 look, 否则 hook/mid 缺人时 LLM 只能写作者 → 围栏死循环
    if mode == "writer":
        from .scene_writer import writer_blocks
        person_rule = writer_blocks(None, ["hook" if a0 < CST.zone_end() else "mid"], bible=bible)

    user = (f"【模式: 单场重规划 (逐场磨合) — 本集其余场已定且不动, 你只重新规划一个场】\n"
            f"场编号: {arc['arc_id']}\n\n"
            f"【本场口播原文 (全集时间轴 {t0:.1f}-{t1:.1f}s 已含开场预留; 你输出组/镜时间用音频轴 = 全集时间 - {tc:.1f}）】\n"
            f"{scene_text}\n\n"
            f"【本场时间窗 (音频轴): {a0:.1f}s → {a1:.1f}s — 组/镜必须首尾相接恰好覆盖全窗, 不得越界】\n\n"
            f"【上下文 (只读, 禁输出)】\n"
            f"- 前一场: {prev_arc['arc_id'] + ' ' + str(prev_arc.get('narrative') or '') if prev_arc else '(本集第一场)'}\n"
            f"- 后一场: {next_arc['arc_id'] + ' ' + str(next_arc.get('narrative') or '') if next_arc else '(本集最后一场)'}\n\n"
            f"【TTS manifest (本场附近, 音频轴, 精确到 0.1s)】\n"
            + "\n".join(json.dumps({"t_start": s["t_start"], "t_end": s["t_end"],
                                    "duration": s["duration"], "text": s["text"][:120]},
                                   ensure_ascii=False) for s in win_segs)
            + bible_block
            + person_rule
            + _series_lock_block(book_title, ep)
            + f"\n【旧版画面 (打磨不是复读 — 新构图必须与旧版显著错开)】\n{old_kf}\n"
            # 0918 立意模块 v2: 结构化多维度注入 (分镜吃立意模块, 人批注/禁用意象全可见)
            + (lambda a: (
                f"【本场立意 (立意模块, 每镜都必须是它的一个面)】\n"
                f"- 核心映射: {a.get('metaphor_core') or a.get('visual_metaphor')}\n"
                + "".join(f"- 映射: {m}\n" for m in (a.get("metaphor_mappings") or []))
                + "".join(f"- 口播承载句 (这些句的画面必须由本场扛): {c}\n"
                          for c in (a.get("carrier_lines") or []))
                + "".join(f"- 画面母题 (构图从这些母题取): {v}\n"
                          for v in (a.get("visual_motifs") or []))
                + (f"- 🚫 禁用意象 (历史否决): {'、'.join(a['banned_motifs'])}\n"
                   if a.get("banned_motifs") else "")
                + (f"- ✍️ 人批注 (必须遵守): {a['concept_notes']}\n"
                   if a.get("concept_notes") else "")
            ))(arc)
            + (f"\n【道具锁定卡 (本场主道具/尺度/配角物件的唯一权威描述 — 0926 用户令, "
               f"A4 漏斗三病: 尺度不巨/品牌不可见/样式跨镜漂移)】\n{arc['prop_lock']}\n"
               "每一镜涉及这些道具的描述必须逐字取自上卡 (材质/颜色/结构/尺寸词不得自行"
               "发明); 全景/仰拍镜必须带上卡的尺度锚句。此卡在立意面板可编辑。\n"
               if arc.get("prop_lock") else "")
            # 0926 景深配额结构注入 (ep3"困在微观"根修): 宪法景别条款是散文, 规划时
            # 给硬数字 — 与 validate_doc 审计闸/fix_hints 回喂构成四层同向
            + (lambda n: (f"\n【景深配额 (硬指标, 落盘校验闸执法)】本场共约 {n} 镜: "
                          f"大场景构图 ≥{max(1, -(-n // 4))} 镜 (全景/俯瞰/辽阔环境+主体占比小+"
                          f"参照物拉纵深, 画面句必须带具体尺度词如辽阔/地平线/延伸到画面外); "
                          f"特写/桌面级微观 ≤{max(2, -(-n // 2))} 镜; 连续两镜特写后下一镜必须"
                          "大场景。大场景镜的 camera 字段用 俯瞰/空拍/平视全景。\n"
                          ) if n else "")(len(zone_slots) + len(scene_slots))
            + f"\n【输出】arcs 数组只含本场一项 (arc_id={arc['arc_id']}, narrative/motion "
              f"重新构思 — 每镜都是立意的一个面); "
              f"groups 只含本场组 (arc_id 全部={arc['arc_id']}); 格式与全片规划完全一致。"
            + zone_block + scene_block + refs_block)
    if fix_hints:
        # 0926 修: 校验违规回喂 (旧重试网盲掷两把同错 — 花字裸数字连败实锤)
        user += ("\n【上轮校验违规 (必须全部修正, 同错再犯=废稿)】\n"
                 + "\n".join(f"- {h}" for h in fix_hints[:12]) + "\n")
    logger.info("[replan] 场 %s 重规划中… (窗口 %.1f-%.1fs, %d 组退役, LLM 1-2 分钟静默属正常)",
                arc["arc_id"], t0, t1, len(s_groups))
    # 0917 批评注入 (闭环): 有旧产出 = 看着旧稿改, 不盲重掷 — assistant 携旧场分镜,
    # user 给自动批评 (禁区/克隆/隐喻贯彻) + 重出指令; 无旧产出 (两级化新场) 保持单轮
    _old_gids = {g["group_id"] for g in s_groups}
    _old_shots = [s for s in doc["shots"] if s.get("group_id") in _old_gids]
    if _old_shots:
        _old_board = "\n".join(
            f"- {s['shot_id']} [{s.get('camera')}]: {(s.get('keyframe_zh') or '')[:80]}"
            for s in sorted(_old_shots, key=lambda x: float(x["t_start"])))
        _crit: list[str] = []
        _sub = {"shots": _old_shots, "groups": doc.get("groups") or [], "arcs": doc.get("arcs") or []}
        _ban = visual_ban_scan(_sub)
        if _ban:
            _crit.append(f"政治敏感禁区命中 {'、'.join(_ban)} — 必须根除")
        if _clone_families(_old_shots):
            _crit.append("场内存在逐字克隆镜 (同图换噪点) — 新分镜每镜必换构图")
        if zone_slots:
            # 1008 铁令升级 (电影感): 换场景 → 微空间变奏 (同大空间换微位+换景别角度)
            _crit.append(f"特区铁律: 本场前 {len(zone_slots)} 槽同属一个大空间 "
                         "(location 2-6字), 每镜换场内微位 (scene_tag 2-6字, 组内禁重复, "
                         "系统硬校验) + 景别角度都变, 旧版场景串必须整体打散 — "
                         "换片场/只换机位/只换景别=废稿")
            _zc = [s for s in _old_shots if s.get("zone")]
            if _zc:
                for issue in _HZ.zone_composition_issues(_zc)[:6]:
                    _crit.append(f"构图同源 {issue} — 每镜换一个隐喻载体 "
                                 "(立意 visual_motifs 轮换, 一镜一母题)")
        if arc.get("visual_metaphor"):
            _crit.append(f"本场隐喻「{arc['visual_metaphor']}」— 新分镜每一镜都必须是它"
                         "的一个面/一个时刻, 不得跑题到通用画面")
        _crit.append("逐镜自查: 黑名单素材/字面配图/相邻构图重复 — 有则改之")
        # 1014 修: 大场输出预算按槽量缩放 — 旧定额 16000 下 15-16 槽必截断
        _n_slots = len(zone_slots) + len(scene_slots) or max(1, int((a1 - a0) / 5))
        raw = _llm().chat_turns(
            [{"role": "system", "content": _sys_prompt(mode)},
             {"role": "user", "content": user},
             {"role": "assistant",
              "content": "旧版分镜如下:\n" + _old_board[:6000]},
             {"role": "user", "content":
              "【批评-修正轮】系统审计对旧版的批评:\n- " + "\n- ".join(_crit) +
              "\n基于批评重新设计本场 (时间窗/口播铁律不变, 构图与旧版显著错开), " +
              ("每镜五件套齐 (输出契约照旧)。" if mode == "writer" else
               "输出格式与全片规划完全一致。")}],
            model="pro", temperature=0.5, max_tokens=_plan_tokens(mode, _n_slots), tag="plan_scene")
    else:
        _n_slots = len(zone_slots) + len(scene_slots) or max(1, int((a1 - a0) / 5))
        raw = _llm().chat(_sys_prompt(mode), user, model="pro", temperature=0.5,
                          max_tokens=_plan_tokens(mode, _n_slots), tag="plan_scene")
    data = _parse_json(raw) or {}
    groups = data.get("groups") or []
    if not groups:
        raise ValueError("导演产出不可解析 (无 groups)")
    for g in groups:
        if isinstance(g, dict):
            g["arc_id"] = arc["arc_id"]
    _ar0 = (data.get("arcs") or [{}])[0]
    new_arc = dict(_ar0) if isinstance(_ar0, dict) else {}
    new_arc["arc_id"] = arc["arc_id"]
    data["arcs"] = [new_arc]

    # 0919 60s 特区执法: slot 镜数对槽校验 + 槽位时间/口播回填; 特区镜重组为
    # 独立头组 (场结构不动, 组=容器); refs 段只在槽后余窗展开 (防 expand 误吃特区句)
    zone_group: dict[str, Any] | None = None
    z_tags: list[str] = []
    if zone_slots:
        groups = _HZ.enforce_zone_shots(groups, zone_slots)
        _zshots = sorted((r for g in groups for r in (g.get("shots") or [])
                          if r.get("zone") == "hook60"),
                         key=lambda r: float(r["t_start"]))
        # scene_tag 硬审计 (0919 用户铁令"连环镜头场景不能一致"的确定性执法):
        # 缺标/组内重复 → 拒稿走重试网, 提示词无牙教训 (s50/s52 逐字克隆实锤)
        # 0923 确定性兜底: LLM 忘带 scene_tag 时从 keyframe_zh 自动提取 (不再因缺标拒稿)
        # 1009 P1: STRUCT 宪法下镜级 keyframe 已退役 (只剩组定调占位, 无微位信息) —
        # 兜底改从 narration 前段取词, 句异则 tag 异, 不再全组同 tag 撞"组内重复"闸
        for r in _zshots:
            if not str(r.get("scene_tag") or "").strip():
                _kf = str(r.get("keyframe_zh") or "")
                if _kf and "，" in _kf and len(_kf) >= 16:
                    _tag = _kf.split("，")[1][:6].strip()
                else:
                    _tag = str(r.get("narration") or "").strip()[:6]
                r["scene_tag"] = _tag or "场景"
        _tag_issues = _HZ.zone_scene_issues([r.get("scene_tag") for r in _zshots])
        if _tag_issues:
            raise ValueError("特区场景审计不过: " + "; ".join(_tag_issues[:4]))
        z_tags = [str(r.get("scene_tag") or "") for r in _zshots]
        _rest = [{**g, "shots": [r for r in (g.get("shots") or [])
                                 if r.get("zone") != "hook60"]} for g in groups]
        _rest = [g for g in _rest if g.get("shots")]
        zone_group = {"arc_id": arc["arc_id"], "camera": "蒙太奇",
                      "keyframe_zh": str((groups[0].get("keyframe_zh") if groups else "")
                                         or "60s特区多场景蒙太奇")[:200],
                      # 1009 P1 回归修: 组级 motion 兜底 (STRUCT 下镜级已退役,
                      # 组缺运动描述会吃 collect_warnings 软警告 — 从场运动线取)
                      "motion_zh": str(new_arc.get("motion") or
                                       (groups[0].get("motion_zh") if groups else "")
                                       or "多场景蒙太奇切换")[:100],
                      "t_start": zone_slots[0]["t_start"], "t_end": zone_slots[-1]["t_end"],
                      "shots": _zshots}
        logger.info("[replan] 场 %s 特区 ✓ %d 槽成镜 (槽尾 %.1fs, 余 %.1fs refs)",
                    arc["arc_id"], len(zone_slots), zone_tail, a1 - zone_tail)
        groups = [zone_group] + _rest

    # 0919 全片装包执法: 普通槽镜回填 (slot 号 > 特区槽数); 失败走重试网
    if scene_slots:
        groups = _HZ.enforce_scene_shots(groups, scene_slots,
                                         slot_offset=len(zone_slots))
        logger.info("[replan] 场 %s 普通槽 ✓ %d 镜回填 (全片装包制)",
                    arc["arc_id"], len(scene_slots))

    # 0917 refs 协议展开: LLM 只引句号 → 系统查表回填 t/narration (确定性);
    # 解析失败/引用不可用 → 整场回退旧路径 (LLM 自报时间 + 贪心落位兜底)
    if refs_mode and zone_group:
        # 特区镜已回填 — refs 只展开余窗 (zone_group 无 refs, 剥离后重拼)
        tail_groups = [g for g in groups if g is not zone_group]
        if tail_groups:
            exp_groups, refs_notes = expand_refs_groups(
                tail_groups, sent_table, zone_tail, a1)
            if exp_groups is not None:
                groups = [zone_group] + exp_groups
                data["planner_refs"] = True
                logger.info("[replan] 场 %s refs 展开 ✓ 余窗镜 %s", arc["arc_id"],
                            ("; ".join(refs_notes)) if refs_notes else "零修复")
            else:
                logger.warning("[replan] 场 %s refs 回退旧路径: %s", arc["arc_id"],
                               "; ".join(refs_notes))
    elif refs_mode:
        exp_groups, refs_notes = expand_refs_groups(
            groups, sent_table, a0, a1)
        if exp_groups is not None:
            for g in exp_groups:
                g["arc_id"] = arc["arc_id"]
            groups = exp_groups
            data["planner_refs"] = True
            logger.info("[replan] 场 %s refs 展开 ✓ %d 镜 %s", arc["arc_id"],
                        len(exp_groups[0].get("shots") or []),
                        ("; ".join(refs_notes)) if refs_notes else "零修复")
        else:
            refs_mode = False
            logger.warning("[replan] 场 %s refs 回退旧路径: %s", arc["arc_id"],
                           "; ".join(refs_notes))
    data["groups"] = groups

    sid_off = max((int(s["shot_id"][1:]) for s in doc["shots"]
                   if s["shot_id"][1:].isdigit()), default=0)
    gid_off = max((int(g["group_id"][1:]) for g in g_all
                   if str(g.get("group_id", ""))[1:].isdigit()), default=0)
    scene_doc = _build_doc(book_id, book_title, ep, str(doc.get("ep_title") or ""), data, bible,
                           sid_offset=sid_off, gid_offset=gid_off)
    # 0919 60s 特区: _build_doc 不透传新字段 — 前 N 镜 (groups[0]=特区组, 构造保证)
    # 重新打标 + 携带槽位 (对齐阶段的圣时间)
    if zone_slots:
        for s, sl in zip(scene_doc["shots"], zone_slots):
            s["zone"] = "hook60"
            s["_zone_slot"] = {"t_start": float(sl["t_start"]), "t_end": float(sl["t_end"]),
                               "text": str(sl["text"])}
        for s, tag in zip(scene_doc["shots"], z_tags):
            s["scene_tag"] = tag

    # 拼回: 弃旧场组/镜, 插新. 场内对齐 = 窗口锁定顺序贪心 (0915 定稿, 三代方案):
    # ① 整集 realign 会微扰其余场; ② 锚式 (头/尾锚) 依赖邻场 narration 音频精确,
    #    邻场刚重规划后锚被毒化 (A6 实锤); ③ 现方案: 在 [窗口起,窗上止] 内按镜
    #    顺序匹配 narration 头, 找不到按 LLM 意图时间比例落位 — 物理出不 了窗口。
    old_gids = {g["group_id"] for g in s_groups}
    arcs[idx] = {**arc, "narrative": new_arc.get("narrative") or arc.get("narrative"),
                 "motion": new_arc.get("motion") or arc.get("motion"),
                 "visual_metaphor": new_arc.get("visual_metaphor") or arc.get("visual_metaphor")}
    remaining = [g for g in g_all if g["group_id"] not in old_gids]
    # 0916 文字分切根治 (用户实锤: 跨词腰斩"尼 克|松赢了"/孤儿尾巴/跨场渗漏):
    # 窗口贪心分配的切点落进巨型多句包中间 → 换句级 packs (句边=唯一合法切点,
    # 与归真/对齐同源); wav silencedetect 失败回退包级
    try:
        from app.services import anim_draft as _ad
        _files = _ad.fetch_episode_audio(book_id, ep)["files"]
        _sseg = _ad._sentence_segs(_files)
        if _sseg:
            segs = _sseg
            logger.info("[replan] 句级 packs %d 段 (切点=句边)", len(segs))
    except Exception as exc:  # noqa: BLE001
        # 0919 弃包级回退 (ep4 实测: 包级吸附剔 7 镜+组窗漂移 = 破坏模式):
        # 句级失败=硬错, 外层重试网接住, ×2 败则人工介入 — 不再静默降级
        raise SystemExit(f"句级 segs 失败 (包级回退已废 — 会剔镜漂组窗): {exc}") from exc
    packs, stream = _packs_stream(segs)
    # 真实集长 = 最后一个非空包的终点 (空文本包实测 226s 幻影 — 裸加 duration 会把
    # 末场钉出窗外, s48 234.8s 实锤)
    total_end = round(tc + _pos_to_time(packs, len(stream)), 2)
    has_next = any(float(g["t_start"]) >= t1 - 0.6 for g in remaining)
    pin_end = t1 if has_next else max(t1, total_end)  # 末场吃满真实集长 (旧doc欠账)

    def _pos_at(t_audio: float) -> int:
        for p in packs:
            if p["t0"] <= t_audio < p["t0"] + p["dur"]:
                frac = (t_audio - p["t0"]) / max(p["dur"], 1e-6)
                return p["lo"] + int(frac * (p["hi"] - p["lo"]))
        return packs[-1]["hi"] if packs else 0

    p_w0, p_w1 = _pos_at(t0 - tc), _pos_at(pin_end - tc)
    ss = sorted(scene_doc["shots"], key=lambda s: float(s["t_start"]))
    sg = sorted(scene_doc["groups"], key=lambda g: float(g["t_start"]))
    if slot_mode:
        # 0919 全片装包制: 镜时间/narration 已由槽执法回填 (槽=句包边), needle
        # 锚定/塌缩/碎镜合并整体退役 — 重切反而洗掉槽位 (s08 65.5 实锤同机理)
        logger.info("[replan] 装包制跳过锚定 (槽位即时间, %d 镜)", len(ss))
    else:
        # 两遍窗口对齐: ① 口播头严格匹配 (窗内单调); ② 未命中边界在两侧已知锚间
        # 按口播字数比例分配 — 单调由构造保证, 不再保留 LLM 原始时间 (重叠实锤根因)
        n = len(ss)
        b = [p_w0] + [None] * (n - 1) + [p_w1]  # b[k] = 镜 k-1|k 间的流边界
        cur = p_w0
        # 口播头 = 该镜的起点: needle(ss[i]) 落位在 b[i] (不是 b[i+1] — 差一实锤,
        # 每镜拿到前一镜的窗, s35 双跨度 26.5s 就是这么来的)
        for i in range(1, n):
            needle = _norm_with_map(ss[i].get("narration") or "")[0][:14]
            for ln in (14, 9, 5):
                if len(needle) >= ln:
                    hit = stream.find(needle[:ln], cur)
                    if cur <= hit < p_w1:
                        b[i] = hit
                        cur = hit
                        break
        narr_lens = [max(len(_norm_with_map(s.get("narration") or "")[0]), 2) for s in ss]
        i = 1
        while i < n:
            if b[i] is None:
                j = i
                while j < n and b[j] is None:
                    j += 1
                lo, hi = b[i - 1], b[j]  # j == n → b[n] = p_w1
                weights = narr_lens[i - 1:j]  # 覆盖这些边界的镜 (shot k ∈ [b[k], b[k+1]])
                total = sum(weights) or 1
                for k in range(i, j):
                    share = sum(weights[:k - i + 1]) / total
                    b[k] = min(lo + int((hi - lo) * share), hi)
                i = j
            else:
                i += 1
        for i2, s in enumerate(ss):
            a2, b2 = b[i2], b[i2 + 1]
            if b2 > a2:
                s["t_start"] = round(tc + _pos_to_time(packs, a2), 2)
                s["t_end"] = round(tc + _pos_to_time(packs, b2), 2)
                s["narration"] = _slice_orig(packs, a2, b2)[:300]
            else:
                # 窗口塌缩 (needle 撞重/比例塌缩) — 禁留 LLM 原始时间 (0323/0336 两连毙实锤:
                # 原始时间乱序+重叠混进对齐序列)。零宽化后剔除, 与归真空镜剔除同判。
                s["t_start"] = s["t_end"] = round(tc + _pos_to_time(packs, a2), 2)
                s["_collapsed"] = True
                logger.warning("[replan] 塌缩镜剔除 %s (窗口零宽, 口播并给邻镜)",
                               s.get("shot_id"))
        ss = [s for s in ss if not s.pop("_collapsed", False)]
        # 碎镜合并 (0917 两级化实锤: needle 挤压出 <1.5s 镜, 硬校验 1.5s 地板两连毙):
        # 并入前镜 (窗口+口播续接); 首镜碎 → 并入后镜。与密度宪法 2-6s 同向。
        merged: list[dict[str, Any]] = []
        for s in ss:
            short = round(float(s["t_end"]) - float(s["t_start"]), 2) < 1.5
            if short and merged and not merged[-1].get("zone"):  # 特区镜不吸收碎镜 (槽位神圣)
                prev = merged[-1]
                logger.warning("[replan] 碎镜并入前镜 %s ← %s (%.1fs)",
                               prev.get("shot_id"), s.get("shot_id"),
                               float(s["t_end"]) - float(s["t_start"]))
                prev["t_end"] = s["t_end"]
                prev["narration"] = (str(prev.get("narration") or "")
                                     + str(s.get("narration") or ""))[:300]
                continue
            merged.append(s)
        ss = merged
        if len(ss) > 1 and round(float(ss[0]["t_end"]) - float(ss[0]["t_start"]), 2) < 1.5:
            head, nxt = ss[0], ss[1]
            logger.warning("[replan] 首碎镜并入后镜 %s ← %s", nxt.get("shot_id"), head.get("shot_id"))
            nxt["t_start"] = head["t_start"]
            nxt["narration"] = (str(head.get("narration") or "") + str(nxt.get("narration") or ""))[:300]
            ss = ss[1:]
    if ss:
        ss[0]["t_start"] = t0
        ss[-1]["t_end"] = pin_end
    if sg:
        sg[0]["t_start"] = t0
        sg[-1]["t_end"] = pin_end
        sg[0]["narration"] = _slice_orig(packs, p_w0, p_w1)[:300]
    # 0919 60s 特区镜保护: 槽位时间神圣 — needle/比例/钉头尾全让位, 原样恢复;
    # 非特区邻镜防重叠 (让位特区)。首槽起=场窗 a0、末槽止=窗界 (装包器钉过), 恢复=幂等
    if zone_slots:
        for s in ss:
            z = s.pop("_zone_slot", None)
            if z:
                s["t_start"] = round(tc + float(z["t_start"]), 2)
                s["t_end"] = round(tc + float(z["t_end"]), 2)
                s["narration"] = str(z["text"])[:300]
        for i, s in enumerate(ss):
            if s.get("zone"):
                continue
            if i > 0 and ss[i - 1].get("zone"):
                s["t_start"] = max(float(s["t_start"]), float(ss[i - 1]["t_end"]))
            if i + 1 < len(ss) and ss[i + 1].get("zone"):
                s["t_end"] = min(float(s["t_end"]), float(ss[i + 1]["t_start"]))
    for s in ss:  # span 变了: 时窗/beats 归一 (H3 生成窗口用)
        span = round(float(s["t_end"]) - float(s["t_start"]), 2)
        gen = min(span, H3_MAX_SPAN)
        s["anim"]["duration_s"] = gen
        shots_mod.stretch_beats(s, gen)
        _clamp_motion_events(s)  # 窗口重排后旧绝对时间事件不可达 → 删
        if span > H3_MAX_SPAN + 0.05:
            s["overrun_s"] = round(span - H3_MAX_SPAN, 2)
        else:
            s.pop("overrun_s", None)
    if sg:
        arcs[idx]["t_start"] = float(sg[0]["t_start"])
        arcs[idx]["t_end"] = float(sg[-1]["t_end"])
    # module_id 透传 (0919 ep4 体检实锤丢失): expand_refs_groups 用 LLM 组重建,
    # 骨架组字段没带过来 → 盘上 groups.module_id 全 None, 模块总线断链
    _mod = next((g.get("module_id") for g in s_groups if g.get("module_id") is not None), None)
    if _mod is not None:
        for g in sg:
            g.setdefault("module_id", _mod)
    doc["groups"] = remaining + sg
    doc["shots"] = sorted([s for s in doc["shots"] if s["group_id"] not in old_gids] + ss,
                          key=lambda s: float(s["t_start"]))
    first, last = doc["shots"][0]["shot_id"], doc["shots"][-1]["shot_id"]
    for s in doc["shots"]:
        s["page_type"] = "C" if s["shot_id"] in (first, last) else "B"
    tagged = shots_mod.tag_brand_cards(doc)
    if tagged:
        logger.info("[replan] 品牌卡镜: %s (零 GPU 直通)", ",".join(tagged))
    return doc


def _build_doc(book_id: str, book_title: str, ep: int, ep_title: str,
               data: dict[str, Any], bible: dict[str, Any], *,
               sid_offset: int = 0, gid_offset: int = 0) -> dict[str, Any]:
    """导演输出 → 统一 doc: 每镜补产线字段 (状态机/seed/beats/text_layer).

    时间轴 = opening 预留 (剪辑层 5.5s) + manifest 时间 (音频先行原生, 无需事后归真).
    sid/gid_offset: 单场重规划拼接用 — 新镜/组用全局新号 (旧 id 退役防撞),
    且不做全集首/末 C 页标记 (拼接方全局重施).
    """
    tc = float(load().brand_card.opening_sec)
    arc_by_id = {a.get("arc_id"): a for a in data.get("arcs") or [] if isinstance(a, dict)}
    raw_groups = data.get("groups") or []
    if not raw_groups and data.get("shots"):
        # 兼容两层输出 (LLM 未切镜): 每条自成一镜一组 — 镜=生成单位, 组缺层由 label 兜
        raw_groups = [{**s, "group_id": s.get("shot_id") or f"S{i + 1:02d}", "shots": [dict(s)]}
                      for i, s in enumerate(data["shots"]) if isinstance(s, dict)]

    groups_meta: list[dict[str, Any]] = []
    shots_out: list[dict[str, Any]] = []
    n = 0
    gi = 0
    for g in raw_groups:
        # 1014 防御: LLM 偶发吐字符串组 (ep3 A1/A3/A5 四把 AttributeError 实锤,
        # 整场炸进重试网) — 畸形条目跳过, 缺口由出口闸+时间轴闸兜底
        if not isinstance(g, dict):
            logger.warning("[replan] 组 %d 非 JSON 对象 (LLM 输出畸形, 跳过)", gi + 1)
            gi += 1
            continue
        gi += 1
        gid = (f"S{gid_offset + gi:02d}" if gid_offset
               else str(g.get("group_id") or f"S{gi:02d}"))
        arc = arc_by_id.get(g.get("arc_id")) or {}
        gt0 = tc + float(g.get("t_start") or 0)
        gt1 = tc + float(g.get("t_end") or 0)
        g_kf = str(g.get("keyframe_zh") or "").strip()
        g_camera = g.get("camera") or "固定"
        inner = g.get("shots") or [g]  # 无镜数组: 组即镜
        g_shot_ids: list[str] = []
        for raw in inner:
            if not isinstance(raw, dict):
                continue  # 同款防御: 字符串镜跳过 (含 shots 被吐成整串的逐字符迭代)
            n += 1
            sid = f"s{sid_offset + n:02d}"
            t0 = tc + float(raw.get("t_start") or 0)
            t1 = tc + float(raw.get("t_end") or 0)
            span = max(t1 - t0, 0.5)
            kf_en = str(raw.get("keyframe_en") or "").strip()
            # 1013 修 (组模板克隆根因): 镜缺画面静默盖组模板 = 整场一构图乱套实锤 —
            # 兜底仍给 (doc 可组装) 但打标, 调用方见标抛错进 fix_hints 重试网
            _kf_missing = not str(raw.get("keyframe_zh") or "").strip()
            kf_zh = str(raw.get("keyframe_zh") or "").strip() or g_kf
            motion_en = str(raw.get("motion_en") or "").strip() or "the scene evolves with subtle ambient motion"
            gen_span = min(round(span, 2), H3_MAX_SPAN)  # H3 配方上限, 超出草稿定格吸收
            shot = {
                "shot_id": sid,
                "arc_id": g.get("arc_id"),
                "group_id": gid,
                "label": (arc.get("narrative") or "")[:60],
                "camera": raw.get("camera") or g_camera,
                "motion_zh": str(raw.get("motion_zh") or g.get("motion_zh") or "")[:300],
                "page_type": "B",
                "t_start": round(t0, 2), "t_end": round(t1, 2),
                "narration": str(raw.get("narration") or ""),
                # 纯画面描述 — 风格锚由 k2.build_prompt 按书级配方统一注入 (单一事实源,
                # 不在镜级拼 style_prefix, 防"圣经前缀×生成层锚"两套风格打架)
                "image_prompt_zh": kf_zh,
                "keyframe_zh": kf_zh,
                "keyframe_en": kf_en,
                "_kf_missing": bool(_kf_missing or not kf_en.strip()),
                # H3 直用: 单拍节拍壳 (h3.build_prompt 拼首帧对齐/物理锁/静音底)
                "anim": {
                    "duration_s": gen_span,
                    "opening_desc": kf_en or kf_zh,
                    "physical_lock": "the main structural elements and layout",
                    "screen_exception": "nothing",
                    "beats": [{"t_start": 0, "t_end": gen_span, "motion": motion_en}],
                    "ending": "the scene holds with gentle ambient light variation",
                },
                # 1014 修: LLM 偶发把 text_layer 吐成字符串列表 → 下游 t.get("text")
                # 全线 AttributeError (ep3 A1/A6 各烧一把实锤) — 合并口统一归一化
                "text_layer": [{"text": str(x)} if isinstance(x, str) else x
                               for x in (raw.get("text_layer") or [])
                               if isinstance(x, (str, dict))],
                # 1015 修 (用户问二段式断链): 合并口此前根本不带 in_image_text —
                # writer 按 §10b 申报了也被丢 → text_pass 永不触发 + 出口闸误拦申报
                # 路线 (唯一过闸剩"删文字物件")。归一化 ≤4 行 (契约同源)
                "in_image_text": [str(x).strip() for x in (raw.get("in_image_text") or [])
                                  if str(x).strip()][:4],
                # 1015 动画先行: H3 事件字结构化透传 (文字三路由之一)
                "h3_text_event": ({k: v for k, v in (raw.get("h3_text_event") or {}).items()
                                  if k in ("text", "at_s", "zone")}
                                 if isinstance(raw.get("h3_text_event"), dict) else {}),
                "qc_notes": "",
                "extra_loras": [],
                "seed": shots_mod.new_seed(),
                "h3_seed": shots_mod.new_seed(),
                "status": "planned",
                "brand_card": False,
                "image_file": None, "video_file": None,
                "attempts": {"k2": 0, "h3": 0},
                "error": None, "reject_note": "",
            }
            if span > H3_MAX_SPAN + 0.05:
                shot["overrun_s"] = round(span - H3_MAX_SPAN, 2)
            # 0919 装包制标透传 (packed=普通槽镜, zone=特区镜) — 对齐链路保护用
            for _k in ("zone", "packed", "scene_tag"):
                if raw.get(_k) is not None:
                    shot[_k] = raw[_k]
            # 0920 浮字军规: LLM 违规浮字当场下沉 overlay + text_layer 同步补登记
            _gate_motion_text(shot)
            _ensure_motion_text(shot)  # 0920 补字回填 (军规逆操作)
            shots_out.append(shot)
            g_shot_ids.append(sid)
        groups_meta.append({
            "group_id": gid, "arc_id": g.get("arc_id"),
            "t_start": round(gt0, 2), "t_end": round(gt1, 2),
            "narration": str(g.get("narration") or "")[:300],
            "camera": g_camera,
            "keyframe_zh": g_kf,
            "motion_zh": str(g.get("motion_zh") or "")[:300],
            "shot_ids": g_shot_ids,
        })
    if shots_out and not sid_offset:  # 单场拼接模式不做 C 标记 (拼接方全局重施)
        shots_out[0]["page_type"] = "C"
        shots_out[-1]["page_type"] = "C"
    doc = {
        "version": shots_mod.SCHEMA_VERSION,
        "book_id": book_id, "book_title": book_title, "ep": ep, "ep_title": ep_title,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "planner_flow": "director2 arcs v2 (场→组→镜)",
        "planner_model": "pro",
        "ep_summary": data.get("ep_summary", ""),
        "world_library": [str(w)[:80] for w in (data.get("world_library") or [])
                          if str(w).strip()][:12],
        "style_recipe": resolve_style(book_title).get("name"),
        "bible": bible,
        "arcs": [{**a,
                  "t_start": round(tc + float(a.get("t_start") or 0), 2),
                  "t_end": round(tc + float(a.get("t_end") or 0), 2)}
                 for a in data.get("arcs") or []],
        "groups": groups_meta,
        "shots": shots_out,
    }
    tagged = shots_mod.tag_brand_cards(doc)
    if tagged:
        logger.info("[plan] 品牌卡镜: %s (零 GPU 直通)", ",".join(tagged))
    return doc


# ── 确定性校验 (自 anim_director.validate_shots 平移) ─────────

def _clamp_motion_events(shot: dict[str, Any]) -> bool:
    """motion_zh 里超出镜长的"N秒时"事件子句删除 (0917 用户令修实 bug).

    拆镜后父镜/分身继承的 motion_zh 仍带拆前绝对时间 (s21 5s 镜里留 8s/12s 事件),
    LLM 新产也会越界 (s04 5.1s 镜写"12秒时")。beats 才是 H3 真值, motion_zh 是
    设计文本 — 不可达事件留着 = 说谎的设计文档。返回是否改写。
    """
    span = round(float(shot.get("t_end") or 0) - float(shot.get("t_start") or 0), 2)
    txt = str(shot.get("motion_zh") or "")
    if not txt or span <= 0:
        return False
    toks = re.split(r"([，。；,;])", txt)
    out: list[str] = []
    changed = False
    for tok in toks:
        if not tok:
            continue
        if re.fullmatch(r"[，。；,;]", tok):
            out.append(tok)
            continue
        times = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*秒", tok)]
        if times and max(times) > span + 0.05:
            changed = True  # 该子句的事件时间不可达 → 整子句删
            if out and re.fullmatch(r"[，。；,;]", out[-1] or ""):
                out.pop()  # 别留悬空分隔符
            continue
        out.append(tok)
    if changed:
        shot["motion_zh"] = "".join(out).strip("，。；,; ")
    return changed


# ── 浮字军规 (0920): H3 只画 ≤2 字超短标 — LLM 违规必纠, 复杂字下沉剪辑层 ──
# ep4 实锤 6/79 文字变形: '归零'→赐零/聰零, '辑'缺笔, '谁付得多'→烧付得多,
# '改规则''报价重调'错字 — 全是 LLM 违反自家 0915 铁律 (≤2字/一镜一词) 的产物.
# 同 _clamp_motion_events 教义: 提示词劝不住的, 落盘前确定性拦.

# 引号浮字 token ('贱'/'归零'/'5000+'/‘单价’ — 内容非空白 ≤12 字)
_FT_TOKEN_RE = re.compile(r"[''\"‘“]([^''\"’”\s]{1,12})[''\"’”]")
# 子句是否"浮字事件": 引号 token + 文字语境词 (双条件防误杀普通引号)
_FT_CTX_RE = re.compile(r"大字|艺术字|艺术数字|特大|金字|红字|浮出|浮现|砸出|砸落|滑入|升起|数字'|字'")
# 花体 (H3 变形放大器: 书法/手写/冲击/抖动)
_FT_FANCY_RE = re.compile(r"书法|手写|冲击|抖动")
_FT_RED_RE = re.compile(r"红色|信号红|红字|大红")
_FT_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|s(?![a-zA-Z]))")
# 沉字后紧跟的无 token 尾巴子句 ("字体是书法手写体"/"红色字带震动效果"/"数字带弹跳")
_FT_ORPHAN_RE = re.compile(r"^(字体|字带|字旁|字形|数字带|.{0,3}字带|该字|此字|同风格|同样|同款)")


def _ft_cjk_len(t: str) -> int:
    return sum("一" <= c <= "鿿" for c in t)


def _ft_clause_time(clause: str, span: float) -> tuple[float, float]:
    m = _FT_TIME_RE.search(clause)
    t0 = min(float(m.group(1)), max(span - 0.5, 0)) if m else 0.8
    return round(t0, 2), round(min(t0 + 2.5, max(span, 1.0)), 2)


def _gate_motion_text(shot: dict[str, Any]) -> bool:
    """浮字军规 + text_layer 同步 (0920 修 ep4 六镜变形实锤).

    ① 军规: H3 只画 [纯ASCII短标 | CJK≤2] 且每镜至多 1 个且无花体 (书法/手写/
    冲击/抖动) — 与 h3.py TEXT_POLICY 同口径. 违规浮字从 motion_zh 子句和
    beats 英文段里整段删除, 改写 text_layer overlay 条目 (留档/音效 cue/
    回填候选 — 0921 起剪辑层不再渲染大字轨, 用户实锤 ep6 下沉条目全是
    零星孤岛碎字; color gold/red 仍驱 音效分类).
    ② 同步: 合规保留的浮字若 text_layer 漏登记 (s21/s22 实锤) → 补
    hero_number/impact 条目 (字幕行内高亮锚 + 留档). 幂等: 同文本条目已存在
    不重写. 返回是否改写.
    """
    span = round(float(shot.get("t_end") or 0) - float(shot.get("t_start") or 0), 2)
    mz = str(shot.get("motion_zh") or "")
    if not mz or span <= 0:
        return False
    parts = re.split(r"([，。；,;])", mz)
    clauses = [(i, p) for i, p in enumerate(parts)
               if p and not re.fullmatch(r"[，。；,;]", p)]

    # 事件提取: (clause_idx, token, t0, t1, fancy, red)
    events: list[dict[str, Any]] = []
    for i, cl in clauses:
        if not _FT_CTX_RE.search(cl):
            continue
        for tok in _FT_TOKEN_RE.findall(cl):
            # 守卫放行: 字母数字/汉字, 或 ?！·≠ 等单字符短标 (H3 强项)
            if not re.search(r"[0-9A-Za-z一-鿿？?！!·≠＋+]", tok):
                continue
            t0, t1 = _ft_clause_time(cl, span)
            events.append({"ci": i, "tok": tok, "t0": t0, "t1": t1,
                           "fancy": bool(_FT_FANCY_RE.search(cl)),
                           "red": bool(_FT_RED_RE.search(cl))})
    if not events:
        return False

    # 同文本去重 (首现定时), 判 keep/sink
    seen: dict[str, dict[str, Any]] = {}
    for ev in events:
        seen.setdefault(ev["tok"], ev)
    uniq = sorted(seen.values(), key=lambda e: e["t0"])
    n_text_clauses = len({e["ci"] for e in events})
    keep: list[str] = []
    for ev in uniq:
        # 1017 W2: 口径对齐 ID-098 (花字≤4字) — 旧 ≤2 是 motion 内嵌时代约束;
        # keep 语义已改为迁移 h3_text_event (声明通道, TEXT_POLICY 同口径)
        sink = (ev["fancy"] or _ft_cjk_len(ev["tok"]) > 4
                or n_text_clauses >= 2 or len(keep) >= 1)
        if not sink:
            keep.append(ev["tok"])
        ev["sink"] = sink
    sunk = [e for e in uniq if e["sink"]]

    changed = False
    tl = list(shot.get("text_layer") or [])

    def _has(kind: str, text: str) -> bool:
        return any(str(t.get("kind")) == kind
                   and shots_mod.norm_for_match(str(t.get("text") or "")) == shots_mod.norm_for_match(text)
                   for t in tl)

    # ① sink → 删 motion_zh 子句 (含无 token 尾巴子句) + 删 beats 英文段 + 写 overlay
    if sunk:
        # 沉字所有出现子句全删 (同 token 可跨多句, 只删首现会留尾巴 — s55 实锤)
        # 1017 W2 收敛: keep token 一并出清 (合规字迁移 h3_text_event, 见②) —
        # motion_zh/beats 不再内嵌字卡 (与 H3 NO_TEXT_POLICY / r2v 'No text'
        # 同 prompt 冲突, s01 家族实锤)
        sunk_toks = {e["tok"] for e in uniq}
        drop_ci = {e["ci"] for e in events if e["tok"] in sunk_toks}
        out: list[str] = []
        prev_dropped = False
        for i, p in enumerate(parts):
            if not p:
                continue
            if re.fullmatch(r"[，。；,;]", p):
                out.append(p)  # 分隔符先留, 删句时回吞 (同 _clamp 做法)
                continue
            drop = i in drop_ci or (prev_dropped and _FT_ORPHAN_RE.match(p))
            if drop:
                if out and re.fullmatch(r"[，。；,;]", out[-1] or ""):
                    out.pop()  # 删子句连同其前分隔符, 活句不粘连
                prev_dropped = True
                continue
            prev_dropped = False
            out.append(p)
        new_mz = "".join(out).strip("，。；,; ")
        if new_mz != mz:
            shot["motion_zh"] = new_mz
            changed = True
        for e in sunk:
            # 0921 三 kind 全查重 (s37 实锤: 走不通已登记 impact 又追加 overlay
            # = 同字双条; ② 分支 0920 已修, ① 下沉分支同律)
            if not any(_has(k, e["tok"]) for k in ("hero_number", "impact", "overlay")):
                tl.append({"kind": "overlay", "text": e["tok"],
                           "t_start": e["t0"], "t_end": e["t1"],
                           "color": "red" if e["red"] else "gold"})
                changed = True
        # beats 英文段删除: 只删含沉字引号的段 (括号保护后按逗号/分号切, 段自足)
        pat = re.compile("[" + re.escape("''\"‘“") + "](" +
                         "|".join(re.escape(e["tok"]) for e in sunk) +
                         ")[" + re.escape("''\"’”") + "]")
        anim = shot.get("anim") or {}
        for b in anim.get("beats") or []:
            mot = str(b.get("motion") or "")
            if not mot or not pat.search(mot):
                continue
            prot: list[str] = []

            def _shield(m_):  # noqa: E306
                prot.append(m_.group(0))
                return f"\x00{len(prot) - 1}\x00"

            masked = re.sub(r"\([^()]*\)", _shield, mot)
            segs = re.split(r"(?<=[,;])\s+", masked)
            kept = [s_ for s_ in segs if not pat.search(s_)]
            if len(kept) != len(segs):
                new_mot = re.sub(r"\x00(\d+)\x00",
                                 lambda m_: prot[int(m_.group(1))],
                                 " ".join(kept))
                new_mot = re.sub(r"\s+", " ", new_mot).rstrip(" ;,.").strip()
                if new_mot != mot:
                    b["motion"] = new_mot
                    changed = True
    # ② keep → 1017 W2 迁移: 合规字出清内嵌后落结构化 h3_text_event (单通道)
    for tok in keep:
        ev = next(e for e in uniq if e["tok"] == tok)
        kind = "impact" if ev["red"] else "hero_number"
        # text_layer 登记 (字幕行内高亮锚; 三 kind 全查重 — 0920 同字双条实锤)
        if not any(_has(k, tok) for k in ("hero_number", "impact", "overlay")):
            tl.append({"kind": kind, "text": tok,
                       "t_start": ev["t0"], "t_end": ev["t1"]})
            changed = True
        # 结构化事件 (幂等不覆写; brand 镜无 H3 不迁)
        if not shot.get("brand_card") and not (
                isinstance(shot.get("h3_text_event"), dict)
                and str(shot["h3_text_event"].get("text") or "").strip()):
            shot["h3_text_event"] = {"text": tok, "at_s": ev["t0"],
                                     "zone": "避开主体脸与关键物的留白"}
            logger.info("[text-route] %s 浮字迁移→h3_text_event「%s」@%.1fs",
                        shot.get("shot_id"), tok, ev["t0"])
            changed = True

    if tl != list(shot.get("text_layer") or []):
        shot["text_layer"] = tl
    return changed



# ── H3 补字回填 (0920 修 ep5 全批零浮字实锤 — 文字军规的逆操作) ──────────────
# 军规只拦违规浮字, 但 0920 提示词收紧 ("违规整段删掉转 overlay — 白写") 后
# LLM 连合规短标也不写进 motion 了: ep4 16 镜带浮字 → ep2/ep5 全批 0 镜,
# H3 补字层整层蒸发, 视频全无文字 (用户实锤"一镜都没有"). 提示词劝不住 →
# 落盘前确定性补 (同军规教义, 方向相反).

def _ensure_motion_text(shot: dict[str, Any]) -> bool:
    """从 text_layer 挑最强合规短标注入 motion_zh 文字事件 + beats 英文段.

    候选优先级 hero_number > impact > overlay (同 kind 取 t_start 最早);
    合规口径同军规: ≤2 字中文或纯短 ASCII、单 token、非花体、时间可达.
    注入子句自身必过 _gate_motion_text (幂等共存). brand_card 直通镜 /
    motion_zh 已有浮字事件 / 无 anim.beats → 不动. 返回是否改写.
    """
    if shot.get("brand_card"):
        return False
    span = round(float(shot.get("t_end") or 0) - float(shot.get("t_start") or 0), 2)
    mz = str(shot.get("motion_zh") or "")
    if span <= 0 or not mz:
        return False
    # 1017 W2 收敛: 守卫改结构化事件 (旧内嵌浮字由 _gate_motion_text 迁移)
    _ev0 = shot.get("h3_text_event") or {}
    if isinstance(_ev0, dict) and str(_ev0.get("text") or "").strip():
        return False  # 已有事件 (arc-pass/迁移/前次回填)
    if any(_FT_CTX_RE.search(cl) and _FT_TOKEN_RE.findall(cl)
           for cl in re.split(r"[，。；,;]", mz)):
        return False  # 旧内嵌浮字待迁移 (本函数不双写)
    order = {"hero_number": 0, "impact": 1, "overlay": 2}
    cand: dict[tuple[int, float], dict[str, Any]] = {}
    for t in shot.get("text_layer") or []:
        kind = str(t.get("kind") or "")
        tok = str(t.get("text") or "").strip()
        if kind not in order or not tok:
            continue
        if not re.search(r"[0-9A-Za-z一-鿿？?！!·≠＋+]", tok):
            continue
        if _ft_cjk_len(tok) > 4 or (len(tok) > 8 and not _ft_cjk_len(tok)):
            continue  # 1017 W2: 候选口径对齐 ID-098 ≤4字
        key = (order[kind], round(float(t.get("t_start") or 0), 2))
        cand.setdefault(key, {"tok": tok, "t0": float(t.get("t_start") or 0),
                              "t1": float(t.get("t_end") or 0)})
    if not cand:
        return False
    best = cand[min(cand)]
    tok = best["tok"]
    t0 = min(best["t0"], max(span - 1.0, 0.0))
    t1 = min(max(best["t1"], t0 + 1.2), span)
    dur = round(t1 - t0, 1)
    # 1017 W2 收敛: 写结构化 h3_text_event (旧版写 motion_zh/beats 内嵌字卡 —
    # 与 H3 NO_TEXT_POLICY 及 r2v 'No text' 同 prompt 冲突)
    shot["h3_text_event"] = {"text": tok, "at_s": round(t0, 1),
                             "zone": "避开主体脸与关键物的留白"}
    logger.info("[text-route] %s 补字回填→h3_text_event「%s」@%.1fs",
                shot.get("shot_id"), tok, t0)
    # 0920 双源互斥 (同军规不变量: token 要么 H3 入画要么 overlay, 不双渲):
    # 0916 降维令后 overlay 是剪辑层唯一大字轨 — 入画接管即撤 overlay,
    # hero_number/impact 锚保留 (字幕行内高亮, R9 三件套 ①+③ 共存)
    tl = list(shot.get("text_layer") or [])
    red = any(str(e.get("color")) == "red" for e in tl
              if str(e.get("kind")) == "overlay" and str(e.get("text") or "").strip() == tok)
    tl = [e for e in tl if not (str(e.get("kind")) == "overlay"
                                and str(e.get("text") or "").strip() == tok)]
    if not any(str(e.get("kind")) in ("hero_number", "impact")
               and str(e.get("text") or "").strip() == tok for e in tl):
        tl.append({"kind": "impact" if red else "hero_number", "text": tok,
                   "t_start": t0, "t_end": t1})
    shot["text_layer"] = tl
    return True


def _clone_families(shots: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """克隆家系检测: 时间轴相邻、同组、keyframe_zh 逐字相同的镜群.

    家系首镜 = 设计本尊 (保留), 其后 = 拆镜复印残留 (违反"每镜必换构图").
    宪法明文"续镜(机位不变)=废稿", 无论克隆从哪条路进来 (整集规划/单场/归真
    拆镜) 这里都能确定性抓到.
    """
    fams: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for s in sorted(shots, key=lambda x: float(x.get("t_start") or 0)):
        same = (cur and not s.get("brand_card")  # 品牌镜 DNA 冻结, 只当边界不当分身
                and str(s.get("group_id")) == str(cur[-1].get("group_id"))
                and (s.get("keyframe_zh") or "").strip()
                and (s.get("keyframe_zh") or "") == (cur[-1].get("keyframe_zh") or ""))
        if not same:
            if len(cur) > 1:
                fams.append(cur)
            cur = []
        cur.append(s)
    if len(cur) > 1:
        fams.append(cur)
    return fams


def redesign_split_clones(doc: dict[str, Any], bible: dict[str, Any] | None = None) -> dict[str, int]:
    """拆镜分身重设计 (0917 用户实锤"太多镜都一样"): 克隆分身批量换构图.

    自动拆镜是时长物理上限的兜底, 不是视觉复印机 — 分身逐字继承父镜提示词
    +相邻 seed = 同一张图换噪点. 本函数把全部克隆家系一次 LLM 调用重设计:
    场景/年代/人物双约/风格与父镜一致, 景别/机位/画面时刻逐镜推进.
    槽位 (时间/口播/镜号/字幕层) 钉死不动; LLM 没接住或解析失败的分身保持
    克隆 (collect_warnings 亮灯), 调用方可重跑本函数 (幂等: 只认逐字克隆).
    返回 {"redesigned": n, "failed": m}.
    """
    fams = _clone_families(doc.get("shots") or [])
    kids = [s for f in fams for s in f[1:]]
    if not kids:
        return {"redesigned": 0, "failed": 0}
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    groups = {str(g.get("group_id")): g for g in doc.get("groups") or []}
    arcs = {str(a.get("arc_id")): a for a in doc.get("arcs") or []}
    bible = bible or doc.get("bible") or {}
    bible_block = ""
    if bible:
        bible_block = ("- 风格: " + str(bible.get("style_prefix") or "")[:100]
                       + " | 色彩: " + str(bible.get("color_palette") or "")[:80])

        _chars = bible.get("characters") or []
        if _chars:
            _ls = "; ".join(f"{c.get('name')}({c.get('look', '')})" for c in _chars if c.get("look"))
            if _ls:
                bible_block += chr(10) + "- 角色锁定卡 (出场角色 keyframe_zh 原样携带 look): " + _ls
    blocks = []
    for f in fams:
        head, fam_kids = f[0], f[1:]
        g = groups.get(str(head.get("group_id")) or "", {})
        a = arcs.get(str(head.get("arc_id")) or "", {})
        kids_line = chr(10).join(
            f"  - {k['shot_id']}: {round(float(k['t_end']) - float(k['t_start']), 1)}s"
            f" | 口播: {str(k.get('narration') or '')[:70]} | 旧机位: {k.get('camera') or '?'}"
            for k in fam_kids)
        blocks.append(
            f"家系 {head['shot_id']} (场 {str(a.get('narrative') or '')[:40]} | 场景定调: "
            f"{str(g.get('keyframe_zh') or '')[:70]}){chr(10)}"
            f"父镜设计 (画面基准 — 分身必须同场景同时刻同人物): "
            f"{str(head.get('keyframe_zh') or '')[:130]}{chr(10)}待重设计分身:{chr(10)}{kids_line}")
    user = ("【模式: 拆镜分身重设计】自动拆镜把超长镜切成了几段, 分身现在是父镜的复印件 — "
            "只重设计下列分身的画面, 时间槽/口播/镜号/字幕层全部钉死不动。" + chr(10)
            + "铁律: ① 同一家系 = 同一场同一时刻同一批人物 (造型/年龄段/表情基调跨镜一致, 双约一致), "
            "年代/场景/色调与父镜完全一致; ② 每个分身与父镜、与其他分身在景别/机位/画面时刻"
            "至少一项显著不同 (如 远景全景→中景→近景特写 的推进), 画面随口播推进一拍; "
            "③ 首帧全灭字 — 禁任何可读文字/数字/字母/标志; ④ 一镜一事, 运镜=固定或极轻微推拉, "
            "禁横移跟拍。" + chr(10) + "【圣经】" + bible_block + chr(10) + chr(10)
            + chr(10).join(blocks) + chr(10) + chr(10)
            + "【输出】严格 JSON: {\"redesigns\": [{\"shot_id\": \"原样带回\", "
              "\"keyframe_zh\": \"首帧(具象+视角锁死+信息完备)\", \"keyframe_en\": \"English, no text\", "
              "\"motion_zh\": \"动画层+至多1个文字事件\", \"motion_en\": \"English ambient motion\", "
              "\"camera\": \"...\"}]} — 上面每个分身一条, 一条不落")
    logger.info("[redesign-clones] %d 家系 %d 分身重设计中… (LLM 1-2 分钟)", len(fams), len(kids))
    raw = _llm().chat(DIRECTOR_SYS, user, model="pro", temperature=0.5, max_tokens=8000, tag="clone")
    data = _parse_json(raw) or {}
    got = {str(r.get("shot_id")): r for r in (data.get("redesigns") or [])
           if isinstance(r, dict) and r.get("shot_id")}
    n = 0
    for fam in fams:
        for k in fam[1:]:
            r = got.get(str(k["shot_id"]))
            kf_zh = str((r or {}).get("keyframe_zh") or "").strip()
            if not kf_zh:
                continue
            span = round(float(k["t_end"]) - float(k["t_start"]), 2)
            gen = min(span, H3_MAX_SPAN)
            motion_en = str((r or {}).get("motion_en") or "").strip() or \
                "Gentle ambient motion, very slight push-in."
            k.update({  # 重置语义与 plan_shot 同款 (口播未变, 媒体必须显式清)
                "keyframe_zh": kf_zh,
                "keyframe_en": str(r.get("keyframe_en") or "").strip(),
                "image_prompt_zh": kf_zh,
                "motion_zh": str(r.get("motion_zh") or "")[:300],
                "camera": r.get("camera") or k.get("camera") or "固定",
                "anim": {
                    "duration_s": gen,
                    "opening_desc": str(r.get("keyframe_en") or "").strip() or kf_zh,
                    "physical_lock": "the camera viewpoint, the main structural elements and layout",
                    "screen_exception": "nothing",
                    "beats": [{"t_start": 0, "t_end": gen, "motion": motion_en}],
                    "ending": "the scene holds with gentle ambient light variation",
                },
                "seed": shots_mod.new_seed(),
                "h3_seed": shots_mod.new_seed(),
                "status": "planned",
                "image_file": None, "video_file": None,
                "attempts": {"k2": 0, "h3": 0},
                "error": None, "reject_note": "",
                "split_parent": str(fam[0]["shot_id"]),
            })
            k.pop("overrun_s", None)
            _clamp_motion_events(k)  # LLM 新产的越界事件时间 → 删
            _gate_motion_text(k)  # 浮字军规: 新产 motion 同样过闸
            _ensure_motion_text(k)  # 0920 补字回填 (军规逆操作)
            n += 1
    failed = len(kids) - n
    if failed:
        logger.warning("[redesign-clones] %d 分身未接住, 保持克隆 (软警告亮灯)", failed)
    logger.info("[redesign-clones] ✓ %d 分身已换构图, %d 保持克隆", n, failed)
    return {"redesigned": n, "failed": failed}


def norm_text_layer(t) -> str:
    """花字归一 (剥书名号/括号/空格/连接符) — 相邻镜重复判定用。

    1010 提为模块级: 单镜重规划的门禁洗涤 (anim_service._sanitize_shot_text)
    必须与 validate_doc 用同一把尺, 闭包版两处漂移 = 洗了还会拦。"""
    return re.sub(r'[《》〈〉「」【】\[\]()（）\s·\-—]', '', str(t or ''))


# 1013 人物在场配额词表 (1013 P0 迁宪法 — validate_doc/终审/inject 共用一把尺;
# 模块级别名保留防既有引用断)
_PERSON_W = CST.person_words()
# 度量尾词: 人物词紧跟这些 = prop_lock 尺寸锚 ("直径约老谭身高1.5倍"), 人在尺上不在画里
_MEAS_TAILS = tuple(CST.measure_tails())


def _has_person_words(blob: str, words: list[str]) -> bool:
    """人物词命中且非度量引用 (卓有ep1 夜修: '高度约人物身高1/3' 不算人在画)."""
    for w in words:
        i = blob.find(w)
        while i >= 0:
            rest = blob[i + len(w): i + len(w) + 2]
            if not any(rest.startswith(t) for t in _MEAS_TAILS):
                return True
            i = blob.find(w, i + 1)
    return False


def person_quota_issues(doc: dict[str, Any], skip_placeholders: bool = True) -> list[str]:
    """人物在场配额 → 分区配额 (1014 一把尺: 唯一尺在 final_gate.zone_quota_issues)。

    场级闸 (validate_doc, skip_placeholders=True 占位豁免) 与装配后终审
    (skip_placeholders=False 一把尺量真句) 都按分区值 (hook/mid 0.6,
    case/cite 1.0)。旧版本函数按场 ⌈n/4⌉ 量, 与终产分区闸两把尺错位:
    器层注入补到 ⌈n/4⌉=4 即停, 终闸要分区 0.6×16=10 — 重试无穷
    (ep4 A6 死锁实锤), 现三消费者 (闸/终审/注入帽) 全部同尺。"""
    from .final_gate import zone_quota_issues
    return zone_quota_issues(doc, skip_placeholders=skip_placeholders)


def validate_doc(doc: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    shots = doc.get("shots") or []
    tc = float(load().brand_card.opening_sec)  # 镜轴含开场预留, 音频侧对齐要加回

    # 1004 修: 场窗连续性硬校验 (定位 ep5 实锤: 切场把 A2 窗砍短 46.7s, 骨架洞
    # 漏到分镜层才炸"时间轴断裂" — 单场重规划窗口来自骨架, 骨架洞永远修不动,
    # ×2 后整规划炸停)。切场时 shots 尚未生成, 本检查是骨架洞唯一的拦截面。
    _arcs_sorted = sorted(doc.get("arcs") or [], key=lambda a: float(a.get("t_start") or 0))
    for _i in range(1, len(_arcs_sorted)):
        _pe = float(_arcs_sorted[_i - 1].get("t_end") or 0)
        _cs = float(_arcs_sorted[_i].get("t_start") or 0)
        if abs(_pe - _cs) > 0.6:
            issues.append(f"场窗断裂: {_arcs_sorted[_i-1].get('arc_id')}({_pe:.1f}s) → "
                          f"{_arcs_sorted[_i].get('arc_id')}({_cs:.1f}s) — "
                          "场窗必须首尾相接铺满时间轴 (中段口播无主=分镜层死锁)")

    # 0926 景深配额审计 (ep3 用户判词"困在微观"): 每场 大场景≥⌈n/4⌉、微观≤⌈n/2⌉ —
    # 景别升级条款是散文没人执行 (同日 N 犯), 进确定性闸 + fix_hints 回喂自纠。
    # 大场景信号: camera 俯瞰/空拍/全景 或 keyframe 具体尺度词; 微观: 特写/桌面级。
    _BIG_W = tuple(CST.depth_big_words())  # 1013 P0: 景深词表迁宪法
    def _scale_cls(s) -> str:
        blob = str(s.get("keyframe_zh") or "") + str(s.get("camera") or "")
        if str(s.get("camera") or "") in ("俯瞰", "空拍", "平视全景") or any(w in blob for w in _BIG_W):
            return "大"
        if "特写" in blob:
            return "微"
        return "中"
    _by_arc: dict[str, list] = {}
    for _s in shots:
        _by_arc.setdefault(str(_s.get("arc_id") or ""), []).append(_s)
    for _a, _ss in sorted(_by_arc.items()):
        # 1013 夜修: 占位镜豁免 (同人物配额) — _kf_missing 组模板章的 keyframe 是
        # 场级定调克隆, 景别分布的权威在法层 size (器层头), 在占位符上量景深 =
        # A4 两连死实锤; 真句镜照常执法。
        _ss = [x for x in _ss if not x.get("_kf_missing")]
        if not _ss:
            continue
        _n = len(_ss)
        _nbig = sum(1 for x in _ss if _scale_cls(x) == "大")
        _nmic = sum(1 for x in _ss if _scale_cls(x) == "微")
        _need = max(1, -(-_n // 4))
        if _nbig < _need:
            _mic_ids = [x["shot_id"] for x in _ss if _scale_cls(x) == "微"][:4]
            issues.append(f"景深配额: 场{_a} {_n}镜仅{_nbig}镜大场景 (需≥{_need}) — "
                          f"把{'/'.join(_mic_ids) or '场内中景镜中挑'} 改为大场景构图 "
                          f"(全景/俯瞰/辽阔环境+主体占比小+参照物显纵深, 禁桌面级特写)")
        if _nmic > max(2, -(-_n // 2)):
            issues.append(f"景深配额: 场{_a} 微观/特写镜 {_nmic}/{_n} 超半 — 连排特写必拆, 插大场景或中景")

    # 1013 人物在场配额 (卓有ep1 A1 实锤: 15镜全档案柜空镜无人 — character_policy
    # "全部人物露脸且有表情"是散文没人执行, 同景深配额先例: 确定性闸 + fix_hints 回喂)。
    # 检测 = 圣经角色名 (最强信号) ∪ 通用人物主体/五官表情词; "手"绝不入表
    # (拉手/手表误报), 品牌卡镜豁免 (定妆 keyframe)。
    # 1013 夜修两处: ① _kf_missing 占位镜豁免 — STRUCT 宪法下 plan_scene 禁写逐镜
    # keyframe, 组模板盖章是预期中间态, 人物由术/器层按寄存器注入, 在占位符上
    # 执法 = 架构错层 (卓有ep1 A4 两连死实锤); ② 度量引用去假阳 — "直径约老谭
    # 身高1.5倍"是尺寸锚不是人在画 (A2/A3 假通过实锤), 人物词紧跟身高/手掌等
    # 度量尾词不计。装配后终审走 person_quota_issues(skip_placeholders=False)。
    issues += person_quota_issues(doc, skip_placeholders=True)

    # 0924 相邻镜花字禁重复 (用户实锤: 相邻镜同词连闪):
    # 归一化(剥书名号/括号/空格)后比 text 值, 相邻镜命中 = 违规
    _sorted = sorted(shots, key=lambda s: float(s.get("t_start") or 0))
    for i in range(1, len(_sorted)):
        prev_texts = {norm_text_layer(t.get("text")) for t in (_sorted[i-1].get("text_layer") or []) if t.get("text")}
        cur_texts = {norm_text_layer(t.get("text")) for t in (_sorted[i].get("text_layer") or []) if t.get("text")}
        dup = prev_texts & cur_texts
        if dup:
            issues.append(f"相邻镜花字重复: {_sorted[i-1]['shot_id']}→{_sorted[i]['shot_id']} 重复'{'/'.join(sorted(dup)[:3])}'")

    # 0924 花字格式铁律 (用户令): 禁裸数字(0-9无单位)和单字花字 — 必须是词(≥2字)或数字带单位
    for s in shots:
        for t in (s.get("text_layer") or []):
            txt = str(t.get("text") or "").strip()
            if not txt:
                continue
            if txt.isdigit() and len(txt) <= 1:
                issues.append(f"{s['shot_id']} 花字裸数字: '{txt}' (须带单位如'{txt}层')")
            elif len(txt) == 1 and not re.match(r'[？！?!.。…]', txt):
                issues.append(f"{s['shot_id']} 花字单字: '{txt}' (必须是词≥2字)")

    total_audio = sum(float(s["t_end"]) - float(s["t_start"]) for s in shots)
    if shots:
        shot_end = max(float(s["t_end"]) for s in shots)
        if abs(shot_end - total_audio - tc) > 2.0:
            issues.append(f"末组终点 {shot_end:.1f}s ≠ 开场预留+镜轴总长 {total_audio + tc:.1f}s")
    for i in range(1, len(shots)):
        prev_end = float(shots[i - 1]["t_end"])
        cur_start = float(shots[i]["t_start"])
        if abs(prev_end - cur_start) > 0.5:
            issues.append(f"时间轴断裂: {shots[i-1]['shot_id']}({prev_end:.1f}s) → "
                          f"{shots[i]['shot_id']}({cur_start:.1f}s)")
    for s in shots:
        dur = float(s["t_end"]) - float(s["t_start"])
        if dur < 1.5:
            issues.append(f"{s['shot_id']} 时长 {dur:.1f}s < 1.5s")
        # 超长镜不硬拒: 归真层自动拆镜兜底 (0916 句级细分, 整句包分块 ≤H3上限);
        # 硬拒会与自动拆镜重复执法 → 规划死循环 (LLM 难精确控时长实锤)
    # 1009 P1 回归修: 逐镜 motion_zh 已退役 (STRUCT 只出结构, 镜级运动归术层
    # action + 器层组装 — 规划期查不到, 旧检查把特区首镜必拒 — s01 两连炸实锤)。
    # 运动驱动原则的规划期执法改挂场级: 每场必须有运动线 (arcs.motion 必填);
    # 组级 motion_zh 缺失降软警告 (collect_warnings), 镜级由 _accept_slot 的
    # action 非空执法 (creative_slots action = 镜级运动/动作的存在证明)。
    for a in doc.get("arcs") or []:
        if not str(a.get("motion") or "").strip():
            issues.append(f"场 {a.get('arc_id')} 缺运动线 — 违反运动驱动原则")
    return issues  # 硬校验到此; 文字类问题走 collect_warnings (物理防护在 K2灭字尾/H3壳, 人检兜底)


def collect_warnings(doc: dict[str, Any]) -> list[str]:
    """软警告 (不挡规划, 记录留痕): keyframe 混英文风格词/可读文字, motion 急推词."""
    shots = doc.get("shots") or []
    warns: list[str] = []
    # 超长镜提示 (不挡 — 归真层自动拆镜兜底, 0916 硬拒死循环教训)
    warns += [f"{s['shot_id']} 时长 {float(s['t_end']) - float(s['t_start']):.1f}s 超 H3 上限 "
              f"(归真自动拆镜兜底)" for s in shots
              if float(s["t_end"]) - float(s["t_start"]) > H3_MAX_SPAN + 0.05]
    # 1009 P1: 组级运动描述降级到此 (旧硬校验退役 — STRUCT 下镜级 motion 归术层,
    # 场级硬执法在 validate_doc 的 arcs.motion)
    warns += [f"组 {g.get('group_id')} 缺场内运动描述 (人检)" for g in doc.get("groups") or []
              if not str(g.get("motion_zh") or "").strip()]
    # 白名单: 圣经 style_prefix / 配方风格词 (LLM 常把全局风格词带进画面描述, 非真违规)
    wl = set(re.findall(r"[A-Za-z]{3,}", (doc.get("bible") or {}).get("style_prefix") or ""))
    wl |= {"cel", "celanimation", "traditional"}
    _hex_strip = re.compile(r"#[0-9a-fA-F]{3,8}")
    # 1014 P1 扩容 (ep3 实锤: 中文汉字从不被检出 — s03/s07/s46 带字过闸):
    # ①引号短中文 (可画文本) ②文字载体动词 (写着/字样/字迹/写满)
    _text_pat = re.compile(r"[a-zA-Z]{3,}|\d{3,}|[「『\"“']([^」』\"”']{1,8})[」』\"”']|写着|字样|字迹|写满")
    _carrier_pat = re.compile(r"写着|字样|字迹|写满")
    for s in shots:
        kf = _hex_strip.sub("", s.get("keyframe_zh") or "")
        hits = [h for h in _text_pat.findall(kf) if h.lower() not in wl]
        if hits:
            _carrier = " (文字载体—应走二段式 in_image_text)" if _carrier_pat.search(kf) and not s.get("in_image_text") else ""
            warns.append(f"{s['shot_id']} keyframe 疑含可读文字/数字 {'/'.join(hits[:3])} — "
                         f"K2 灭字尾会物理防护, text_layer 需自查{_carrier}")
    _fast_pat = re.compile(r"猛然|急速|快速|飞速|瞬间|突然|闪电")
    for s in shots:
        if _fast_pat.search((s.get("motion_zh") or "") + (s.get("camera") or "")):
            warns.append(f"{s['shot_id']} motion 含急推词 — H3 壳有缓速措辞对冲, 建议人检")
    # 越界事件时间 (0917): "N秒时"事件超出镜长 = 不可达, 设计文档说谎
    for s in shots:
        span = float(s["t_end"]) - float(s["t_start"])
        bad = [x for x in re.findall(r"(\d+(?:\.\d+)?)\s*秒", s.get("motion_zh") or "")
               if float(x) > span + 0.05]
        if bad:
            warns.append(f"{s['shot_id']} motion 文字事件超镜长 ({'/'.join(bad)} > {span:.1f}s) — 事件不可达")
    # 克隆家系 (0917 用户实锤"太多镜都一样"): 分身逐字=父镜 → 同一张图换噪点
    for f in _clone_families(shots):
        kids = "/".join(s["shot_id"] for s in f[1:])
        warns.append(f"{f[0]['shot_id']} 拆镜分身克隆未重设计 (画面与父镜逐字相同): {kids} — "
                     f"重跑对齐自动重设计, 或单镜🔁")
    # 政治敏感画面禁区 (0917 用户令): 命中即亮灯 (生成闸门另硬拒)
    _ban_hits = visual_ban_scan(doc)
    if _ban_hits:
        warns.append(f"画面命中政治敏感禁区 (中国地图/国旗/国徽/领导人): {'、'.join(_ban_hits[:6])} — "
                     f"单镜🔁 重设计, 卡通/剪影/变形同禁")
    return warns


def consistency_review(doc: dict[str, Any], bible: dict[str, Any] | None = None) -> dict[str, Any]:
    """全片巡检轮 (0917 用户令: 调优优先) — 逐场渐进产出的代价=局部视角.

    多轮对话: assistant=全片分镜摘要 (模型看见自己画的所有镜) → user=巡检指令
    → 只出槽级修正清单 patches [{shot_id, slots:{action/detail/atmosphere}, camera}]。
    1009 P4 (设计 §6.2 写者收口): patch 不再直写 keyframe — 合并进创意槽后经
    器层重组装 (assemble_episode only), 寄存器/角色围栏自动执法; 无 shooting_script
    的旧 doc 退老协议 (LLM 直写 keyframe, 兼容存量)。
    检查面: 跨场人物双约一致 / 圣经色板贯彻 / 相邻镜构图重复 / 隐喻场内贯彻 /
    黑名单。确定性应用 (仅清单内已存在的镜, 媒体重置回 planned), 零 patch=不落刀。
    返回 {"patched": n, "issues": [...]}。
    """
    shots = sorted(doc.get("shots") or [], key=lambda s: float(s.get("t_start") or 0))
    if not shots:
        return {"patched": 0, "issues": []}
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    # 1009 P4: 法层在场判定 — 槽级协议仅在器层链路可用时启用
    from .shooting_script import load as _load_ss
    ss = _load_ss(str(doc.get("book_title") or ""), int(doc.get("ep") or 0))
    slot_mode = bool(ss)

    board = "\n".join(
        f"{s['shot_id']} [{s.get('arc_id')}] {round(float(s['t_end']) - float(s['t_start']), 1)}s "
        f"{(s.get('keyframe_zh') or '')[:90]}"
        + (f" | 槽: {(s.get('creative_slots') or {}).get('action') or '?'}"
           f"·{(s.get('creative_slots') or {}).get('scene_detail') or '?'}"
           f"·{(s.get('creative_slots') or {}).get('atmosphere') or '?'}"
           if slot_mode else "")
        for s in shots)
    user1 = ("【模式: 全片分镜总览 (只读)】以下是本集全部镜的首帧设计"
             + (" 与创意槽 (action·detail·atmosphere)" if slot_mode else "")
             + ", 场结构见 arcs。\n" + board)
    if slot_mode:
        _patch_schema = ("输出严格 JSON: {\"issues\": [\"一句话问题\", ...], \"patches\": "
                         "[{\"shot_id\": \"s03\", \"reason\": \"一句话为什么改\", "
                         "\"slots\": {\"action\": \"新动作(≤20字)\", "
                         "\"scene_detail\": \"新细节(≤30字)\", "
                         "\"atmosphere\": \"新氛围(≤10字)\"}, \"camera\": \"...\"}]} "
                         "— slots 三键都可只给要改的 (其余系统保留原值); "
                         "patches 最多 8 条, 无问题给空数组。"
                         "你只改槽 — 首帧画面由系统模板重组装, 不要自己写首帧句。")
        patches_filter = lambda p: isinstance(p, dict) and p.get("shot_id") \
            and isinstance(p.get("slots"), dict) and str(p["slots"]).strip("{}") \
            and any(str(v or "").strip() for v in p["slots"].values())
    else:
        _patch_schema = ("输出严格 JSON: {\"issues\": [\"一句话问题\", ...], \"patches\": "
                         "[{\"shot_id\": \"s03\", \"keyframe_zh\": \"新首帧\", \"keyframe_en\": \"English, no text\", "
                         "\"camera\": \"...\", \"motion_zh\": \"...\", \"motion_en\": \"English ambient motion\"}]} "
                         "— patches 最多 8 条, 无问题给空数组。")
        patches_filter = lambda p: isinstance(p, dict) and p.get("shot_id") \
            and str(p.get("keyframe_zh") or "").strip()
    ask = ("【全片巡检轮 — 只出修正清单, 不新增镜】逐镜检查跨场一致性: ①人物双约跨场一致 "
           "(同人物年龄/造型/表情基调) ②圣经色板贯彻 (金=钱/被选中, 红=危机) "
           "③相邻镜构图是否重复 (含跨场接缝) ④场隐喻是否贯彻到每镜 ⑤黑名单"
           "(商务素材/政治禁区)。只修真问题 — 宁可零修正不可为改而改。"
           + _patch_schema)
    logger.info("[consistency] 全片巡检轮中… (LLM 1-2 分钟, %d 镜, %s)",
                len(shots), "槽级patch" if slot_mode else "老协议")
    raw = _llm().chat_turns(
        [{"role": "system", "content": DIRECTOR_SYS},
         {"role": "user", "content": user1},
         {"role": "assistant", "content": "已通读全部分镜, 请下巡检指令。"},
         {"role": "user", "content": ask}],
        model="pro", temperature=0.3, max_tokens=3000, tag="review")
    data = _parse_json(raw) or {}
    patches = [p for p in (data.get("patches") or []) if patches_filter(p)]
    by_id = {s["shot_id"]: s for s in shots}
    n = 0
    _snapshots: dict[str, dict[str, Any]] = {}
    _patched: list[str] = []
    _slot_patched: set[str] = set()
    for p in patches[:8]:
        s = by_id.get(str(p["shot_id"]))
        if s is None or s.get("brand_card"):
            continue  # 未知镜/品牌镜不动
        _snapshots[s["shot_id"]] = {k: (dict(s[k]) if isinstance(s[k], dict) else s[k])
                                    for k in ("keyframe_zh", "keyframe_en", "image_prompt_zh",
                                              "camera", "motion_zh", "anim", "seed", "h3_seed",
                                              "status", "image_file", "video_file", "attempts",
                                              "error", "reject_note", "creative_slots")
                                    if k in s}
        _patched.append(s["shot_id"])
        if slot_mode:
            # 1009 P4: 槽级 patch — 合并 (只改给出的键) + 规层净化, 画面句交给器层
            from .creative_slots import _accept_slot
            cur = dict(s.get("creative_slots") or {})
            cur.update({k: str(v or "").strip()
                        for k, v in (p.get("slots") or {}).items()
                        if k in ("action", "scene_detail", "atmosphere")
                        and str(v or "").strip()})
            if not _accept_slot(s, cur):
                continue  # action 被改空 = 无效 patch, 跳过 (快照白拍无害)
            _slot_patched.add(s["shot_id"])
            s.update({
                "camera": p.get("camera") or s.get("camera") or "固定",
                "seed": shots_mod.new_seed(),
                "h3_seed": shots_mod.new_seed(),
                "status": "planned",
                "image_file": None, "video_file": None,
                "attempts": {"k2": 0, "h3": 0},
                "error": None, "reject_note": "",
            })
            n += 1
            continue
        kf = str(p["keyframe_zh"]).strip()
        kf_en = str(p.get("keyframe_en") or "").strip()
        s.update({
            "keyframe_zh": kf,
            "keyframe_en": kf_en,
            "image_prompt_zh": kf,
            "camera": p.get("camera") or s.get("camera") or "固定",
            "motion_zh": str(p.get("motion_zh") or s.get("motion_zh") or "")[:300],
            "seed": shots_mod.new_seed(),
            "h3_seed": shots_mod.new_seed(),
            "status": "planned",
            "image_file": None, "video_file": None,
            "attempts": {"k2": 0, "h3": 0},
            "error": None, "reject_note": "",
        })
        # anim 必须同步重建 (0917 深查: h3 吃 opening_desc — 只改 keyframe 不重建
        # = 新图配旧视频锁, 图视失配; 与 plan_shot/redesign 同款语义)
        span = round(float(s["t_end"]) - float(s["t_start"]), 2)
        gen = min(span, H3_MAX_SPAN)
        motion_en = str(p.get("motion_en") or "").strip() or \
            "Gentle ambient motion, very slight push-in."
        s["anim"] = {
            "duration_s": gen,
            "opening_desc": kf_en or kf,
            "physical_lock": "the camera viewpoint, the main structural elements and layout",
            "screen_exception": "nothing",
            "beats": [{"t_start": 0, "t_end": gen, "motion": motion_en}],
            "ending": "the scene holds with gentle ambient light variation",
        }
        _clamp_motion_events(s)
        _gate_motion_text(s)  # 巡检 patch 也是新 LLM 产出 — 浮字军规同闸
        _ensure_motion_text(s)  # 0920 补字回填 (军规逆操作)
        n += 1
    # 1009 P4: 槽级 patch 的镜 → 器层重组装 (keyframe/anim/法层钉标一次到位;
    # 组装器跳过有媒体镜 — 这些镜刚被重置为 planned, 必然可组)
    if _slot_patched:
        try:
            from .assembler import assemble_episode
            assemble_episode(doc, ss, bible or doc.get("bible") or {},
                             only={sid for sid in _slot_patched})
        except Exception as exc:  # noqa: BLE001 — 重组装失败回滚槽 patch
            logger.warning("[consistency] 器层重组装失败 → 回滚 %d 镜槽 patch: %s",
                           len(_slot_patched), exc)
            for sid in _slot_patched:
                s = by_id.get(sid)
                if s is not None:
                    s.update(_snapshots.get(sid, {}))
                    n -= 1
            _slot_patched.clear()
    # patch 后重扫 (0917 闭环): 巡检产物本身是新 LLM 产出 — 禁区命中即回滚该 patch
    from app.services import compliance as _cmp2
    for sid in _patched:
        s = by_id.get(sid)
        blob = " ".join(filter(None, [str(s.get("keyframe_zh") or ""), str(s.get("keyframe_en") or ""),
                                      str(s.get("motion_zh") or "")]))
        if _cmp2.check_visual(blob):
            logger.warning("[consistency] patch %s 命中禁区 → 回滚", sid)
            s.update(_snapshots[sid])
            if sid in _slot_patched:
                _slot_patched.discard(sid)
            n -= 1
    issues = [str(x)[:120] for x in (data.get("issues") or [])][:10]
    if n or issues:
        logger.info("[consistency] ✓ 修正 %d 镜, 问题留痕 %d 条", n, len(issues))
    return {"patched": n, "issues": issues}




# ── 提示词增强两级 (0926 用户令: 拆 pass1 名词具象化伺候 K2 + pass2 动起来伺候 H3) ──
# 旧单级 (0923 摄影套话版) 实锤只涨字数不涨可画密度 — 试渲 vs 产线差距主因 (0925 案卷)。

_ENHANCE1_SYS = (
    "你是电影美术置景师。给你导演写的基础画面句，你只做一件事：裸名词具象化 — "
    "把每个泛指名词展开成看得见的具体样子，让画面越看越有细节、越看越有小心思。"
    "铁律（违反即废）："
    "一，原句的实体名词（人物/物体/场景部件）必须逐字保留，只允许在其前后叠加定语、"
    "同位语或该名词的可见细节；禁改写、删除、合并原有内容 — 你不是编剧。"
    "二，每个裸名词都要展开：什么样的工作台（材质/形状/磨损/上面摆了什么）、"
    "怎样的大脑（色泽/透明度/表面细节）、如何的书架（高低/疏密/落灰）。"
    "三，细节必须服务本场视觉隐喻与场景：小心思=服务隐喻的细节（如'堆积如山的失败产品'"
    "自己讲失败故事），禁与隐喻无关的随机堆砌。"
    "四，可画实体预算8-15个：全镜主要可画实体不足则补、够了则停，禁凑数硬塞。"
    "五，新增元素一律静止在位（桌上摆着的/墙上挂着的/角落堆着的/已经落灰的）；"
    "禁任何'将要倒下的''正在掉落的'等将建立新接触关系的元素；禁新增人物互动。"
    "六，光影写成看得见的现象（光斑拉过纸面/影子向右渐次晕开/崖壁镶着碎台阶），"
    "禁技术术语（色温/开尔文/K值/f档/lux）。"
    "七，全灭字：画面里禁任何文字/数字/字母/招牌字样，牌匾一律空白无字。"
    "八，禁把'手工捏制/黏土质感'当装饰词重复（名词性的'黏土盒子'不受限）；"
    "禁指纹/微缩/模型感；禁英文；禁色值编码。"
    "九，Krea2 渲染容量红线 (1006 卓有ep1 实锤, 违反=出图1分): "
    "一镜一焦点(禁多主体多动作争抢); 一主一辅光源(禁3+光源); "
    "禁镜面反射/镜中倒映(Krea2不理解); 人物禁'渺小如豆'(至少占画面1/8高度); "
    "禁'无数/大量/成百上千'(改为'几摞/三四份'等可数表达); "
    "最终200-280字(超300字渲染过载)。"
    "十，人物造型一致: 同一人物服饰全集统一; Q版/正常比例不混用; "
    "群演服饰统一基调(职场=衬衫西裤系)。"
    "输出：只输出增强后的中文画面句（200至280字），不输出任何解释。"
)

_ENHANCE2_SYS = (
    "你是动画编排师（H3图生视频提示词师）。输入=增强后的画面句（首帧已按它生成）、"
    "导演写的动画层句、口播片段。你只做一件事：让首帧里已有的元素安全地动起来。"
    "铁律（H3实测军规，违反即废片）："
    "一，开头一句显式主体与任务：这一镜主体是谁/什么、任务是什么（推进口播的那个动作）。"
    "二，给画面句里静止在位的细节元素分配1-3个安全原地微动：尘埃在光柱里缓慢漂浮/"
    "阴影缓缓移动/蒸汽袅袅上升/光晕轻微呼吸/布帘轻轻摆动 — 只选画面里已有的元素。"
    "三，导演原动画层的主体动作与文字出场事件一条不丢，只在其基础上追加环境微动；"
    "禁改主体动作、禁新增画面里没有的元素、禁大面积重绘。"
    "四，运镜：固定 或 极轻微推拉，仅此两样；禁横移/跟拍/摇镜/平移。"
    "五，主体位移短距离、镜内前段完成、到位静止；特效只在静止后触发。"
    "六，文字出场：只有英文/数字/不超过2字的中文可进动画层；3字及以上中文词交给后期字幕。"
    "七，人物一致性 (1006 卓有ep1 混穿实锤): 人物比例与服饰必须与首帧描述一致; "
    "禁在动画中改变服饰颜色/比例(Q版变正常/正常变Q版); 群演服饰不突变。"
    "八，中英双语同步 (1006 实锤: keyframe改白衬衫但opening_desc仍写blue skin orange shirt → H3灌回旧色): "
    "opening_desc 与 motion_en 的人物皮肤色/服饰必须与 keyframe_zh 当前版一致; "
    "keyframe 说白皙皮肤+白衬衫领带 → 英文必须 fair skin + white dress shirt + dark tie; "
    "禁凭旧记忆写已废弃的颜色。"
    '输出严格 JSON：{"motion_zh": "中文动画层", '
    '"motion_en": "English: subject task first, then original events, then ambient micro-motions", '
    '"opening_desc": "English 1-2 sentences describing the first frame contents '
    '(match the enriched scene sentence)"}'
)


def _survival_ratio(base: str, enhanced: str) -> float:
    """只加不删·存活率 (确定性, 0926): base 内容在 enhanced 中的匹配块占比.

    0922 调味器实锤 LLM'丰富'任务会偷偷缩句 (密度 0.5x) — SequenceMatcher
    匹配块总长/len(base) 做逐字存活闸; 非名词修饰语改写 (色温→光斑) 不伤分。
    """
    from difflib import SequenceMatcher
    if not base.strip():
        return 1.0
    return sum(b.size for b in SequenceMatcher(None, base, enhanced).get_matching_blocks()) / len(base)


def _anchor_lost(base: str, enhanced: str) -> list[str]:
    """器层锚保留闸 (1009 P1 拍板 D1·甲, 设计 §3.4): 抛光不得丢结构锚.

    器层组装句的结构锚: 头两段「景别，角度」+ 全部 ≥8 字的括段 (角色 look /
    道具 look — 器层以 名（look） 形式注入)。增强只许在锚上加词, 丢锚=围栏
    (寄存器/角色锁定卡) 被抛光洗掉。返回丢失锚清单 (空=过闸)。
    """
    lost: list[str] = []
    segs = base.split("，")
    head = "，".join(segs[:2]) if len(segs) >= 2 else base[:12]
    # 1013 修 (组模板克隆根因): 1009 P1 退役镜级 keyframe 后 base 多为组场景句,
    # 开头是场景内容不是景别头 — 逐字要求前两段存活 = 扩写 100% 误杀 (47镜全灭实锤)。
    # 头两段真含景别词才执法结构头; look 括段照检不受影响。
    _cam_words = ("全景", "俯瞰", "特写", "空拍", "平视", "低角度", "仰拍",
                  "中景", "近景", "远景", "微距", "大全", "广角", "定格", "大场景")
    if head and any(w in head for w in _cam_words) and head not in enhanced:
        lost.append("结构头")
    # 1013 夜修 (人物锚): base 有人物词而 enhanced 全丢 = 配额被抛光洗掉 —
    # 装配注入的替身/角色句是配额刚性的成果, 增强只许换说法不许赶人出画
    if _has_person_words(base, _PERSON_W) and not _has_person_words(enhanced, _PERSON_W):
        lost.append("person")
    for m in re.finditer(r"（([^）]{8,})）", base):
        frag = m.group(1)[:16]
        if frag and frag not in enhanced:
            lost.append(f"look:{frag[:10]}")
    return lost


def _enhance_pass1(doc, book_title: str, only: set[str] | None,
                   touched: set[str]) -> int:
    """增强1 · 画面丰富度 (伺候 K2): 只改 keyframe_zh/image_prompt_zh.

    裸名词具象化; 细节取材本场 visual_metaphor + 场景定调 (group 句);
    存活率≥0.70 且不缩句才收; 幂等 _enhanced1 (旧 _enhanced=0923 套话版, 不算数)。
    """
    from app.services.llm_service import get_llm_service as _llm
    bible = load_or_derive_bible("", book_title)
    style_ctx = ""
    if bible:
        style_ctx = ("风格: " + str(bible.get("style_prefix", ""))[:80]
                     + " | 色调: " + str(bible.get("color_palette", ""))[:60])
    arcs = {str(a.get("arc_id") or ""): a for a in doc.get("arcs") or []}
    groups = {str(g.get("group_id") or ""): g for g in doc.get("groups") or []}
    n = 0
    for s in doc.get("shots") or []:
        if s.get("brand_card") or s.get("_enhanced1"):
            continue
        if only is not None and s["shot_id"] not in only:
            continue
        base = str(s.get("keyframe_zh") or "").strip()
        if len(base) < 20:
            continue
        arc = arcs.get(str(s.get("arc_id") or "")) or {}
        grp = groups.get(str(s.get("group_id") or "")) or {}
        user = ("风格参考：" + style_ctx
                + "\n本场视觉隐喻：" + str(arc.get("visual_metaphor") or "")[:120]
                + "\n本场场景定调：" + str(grp.get("keyframe_zh") or "")[:120]
                + "\n本镜口播：" + str(s.get("narration") or "")[:120]
                + "\n导演基础画面：" + base + "\n增强后输出：")
        try:
            out = _llm().chat(_ENHANCE1_SYS, user, model="pro", temperature=0.3, max_tokens=1500, tag="enhance1")
            enhanced = str(out or "").strip()
            # 1013 夜修 (模板句换闸): 器层组装句 (补光比签名) 是结构化底稿不是设计
            # 散文 — 散文化必然重排句式, 存活率闸对模板底 46/47 团灭实锤 (四跑
            # 全链走通仍模板单调的根因)。模板底只走 锚+compliance+容量 闸
            # (结构头/look括段/人物锚照护); 散文底保持原双闸不动。
            _is_tmpl = "补光比" in base
            ratio = _survival_ratio(base, enhanced)
            _len_ok = (40 <= len(enhanced) <= 700) if _is_tmpl else \
                (len(enhanced) >= len(base) * 0.98 and len(enhanced) <= 700)
            _ratio_ok = True if _is_tmpl else ratio >= 0.70
            if _len_ok and _ratio_ok:
                # 1009 P1 甲方案两闸 (拍板 D1·甲, 设计 §3.4): ① 器层锚保留 — 丢
                # 景别角度头/角色道具 look 括段 = 围栏被抛光洗掉; ② compliance —
                # 增强是新 LLM 产出, 巡检 patch 有回滚闸这里补齐。任一命中 → 用原句。
                _lost = _anchor_lost(base, enhanced)
                _ban = False
                if not _lost:
                    from app.services import compliance as _cmp1
                    _ban = bool(_cmp1.check_visual(enhanced))
                if _lost or _ban:
                    logger.warning("[enhance1] %s %s → 用原句 (锚/compliance 闸)",
                                   s.get("shot_id"),
                                   "/".join(_lost) or "compliance命中")
                else:
                    s["keyframe_zh"] = enhanced
                    s["image_prompt_zh"] = enhanced
                    s["_enhanced1"] = True
                    # 画面句变了 → 上轮 pass2 产物已过期 (opening_desc/微动名单须跟新画面句),
                    # 清旗标让同轮/下轮 pass2 重跑 (耦合纪律执行层, 补跑乱序也自愈)
                    s.pop("_enhanced2", None)
                    touched.add(s["shot_id"])
                    n += 1
            else:
                logger.warning("[enhance1] %s 存活率/长度闸未过, 用原句 (存活率%.2f, %d→%d字)",
                               s.get("shot_id"), ratio, len(base), len(enhanced))
        except Exception as exc:  # noqa: BLE001 — 单镜失败不断批
            logger.warning("[enhance1] %s 失败用原句: %s", s.get("shot_id"), exc)
    return n


def _enhance_pass2(doc, only: set[str] | None, touched: set[str]) -> int:
    """增强2 · 动起来 (伺候 H3): 改 motion_zh + 重建 anim (beats/opening_desc).

    给增强1新增的静止元素分配安全原地微动; 主体动作/文字事件/军规全保留 (只编排不改戏)。
    确定性闸: 禁横移/跟拍/摇镜/平移 + 不缩句, 违者 motion 留原句 (anim 仍重建 —
    opening_desc 必须跟新画面句, 0917 图视失配教训)。幂等 _enhanced2。
    """
    from app.services.llm_service import get_llm_service as _llm, _parse_json
    n = 0
    for s in doc.get("shots") or []:
        if s.get("brand_card") or s.get("_enhanced2"):
            continue
        if only is not None and s["shot_id"] not in only:
            continue
        scene = str(s.get("keyframe_zh") or "").strip()
        base_motion = str(s.get("motion_zh") or "").strip()
        if len(scene) < 20 or len(base_motion) < 8:
            continue
        beats_motion = ""
        try:
            beats_motion = str((s.get("anim") or {}).get("beats") or [{}])[0].get("motion") or ""
        except Exception:  # noqa: BLE001
            beats_motion = ""
        user = ("增强后画面句（首帧）：" + scene
                + "\n导演动画层：" + base_motion
                + "\n动画层英文（节拍）：" + beats_motion[:300]
                + "\n口播片段：" + str(s.get("narration") or "")[:120]
                + "\n输出 JSON：")
        try:
            raw = _llm().chat(_ENHANCE2_SYS, user, model="pro", temperature=0.3, max_tokens=800, tag="enhance2")
            data = _parse_json(raw)
            mzh = str((data or {}).get("motion_zh") or "").strip()
            men = str((data or {}).get("motion_en") or "").strip()
            odesc = str((data or {}).get("opening_desc") or "").strip()
            # 1009 P1 甲方案闸②: motion 也是新 LLM 产出 — compliance 同闸
            from app.services import compliance as _cmp2p2
            _mzh_ban = bool(mzh) and bool(_cmp2p2.check_visual(mzh))
            if (len(mzh) >= len(base_motion) * 0.8
                    and not any(w in mzh for w in ("横移", "跟拍", "摇镜", "平移"))
                    and not re.search(r"(?i)\b(pan|panning|tracking|truck)\b", men)
                    and not _mzh_ban):
                s["motion_zh"] = mzh[:400]
                men_eff = men or beats_motion or "Gentle ambient motion, very slight push-in."
            else:
                logger.warning("[enhance2] %s 军规/长度闸未过, motion 留原句", s.get("shot_id"))
                men_eff = beats_motion or str(s.get("motion_en") or "") \
                    or "Gentle ambient motion, very slight push-in."
            # 1017 W2 锚定链归正: 画面句优先 (组装器不发明场景); LLM odesc 仅降级
            _kf_en = str(s.get("keyframe_en") or "").strip()
            if not _kf_en:
                logger.warning("[enhance2][anchor] %s keyframe_en 空, odesc 降级",
                               s.get("shot_id"))
            s["anim"] = {
                "duration_s": min(round(float(s["t_end"]) - float(s["t_start"]), 2), H3_MAX_SPAN),
                "opening_desc": _kf_en or odesc or scene,
                "physical_lock": "the camera viewpoint, the main structural elements and layout",
                "screen_exception": "nothing",
                "beats": [{"t_start": 0,
                           "t_end": min(round(float(s["t_end"]) - float(s["t_start"]), 2), H3_MAX_SPAN),
                           "motion": men_eff}],
                "ending": "the scene holds with gentle ambient light variation",
            }
            _clamp_motion_events(s)
            _gate_motion_text(s)
            _ensure_motion_text(s)
            s["_enhanced2"] = True
            touched.add(s["shot_id"])
            n += 1
        except Exception as exc:  # noqa: BLE001 — 单镜失败不断批
            logger.warning("[enhance2] %s 失败, motion/anim 留原: %s", s.get("shot_id"), exc)
    return n


def enhance_keyframes(doc, book_title, only: set[str] | None = None):
    """提示词增强两级编排器 (0923 用户令立, 0926 用户令拆两级).

    增强1 名词具象化伺候 K2 (只改画面句, 新元素静止入画) → 增强2 动起来伺候 H3
    (只改动画层+重建 anim, 给新元素分配安全微动)。纯 LLM 零 GPU, K2 之前跑;
    only=单镜集 (A/B/补跑用), 默认全量; 幂等 _enhanced1/_enhanced2。
    改稿必重生成: 本次被改写的镜若已有媒体 → 状态回 planned + 媒体清空
    (新提示词配旧图/旧视频 = 图视失配, 巡检 patch 同款语义)。
    """
    from app.config import load_config, set_config
    set_config(load_config())
    touched: set[str] = set()
    n1 = _enhance_pass1(doc, book_title, only, touched)
    n2 = _enhance_pass2(doc, only, touched)
    n_reset = 0
    for s in doc.get("shots") or []:
        if s["shot_id"] not in touched:
            continue
        if str(s.get("status") or "") != "planned":
            s["status"] = "planned"
            s["image_file"] = None
            s["video_file"] = None
            s["attempts"] = {"k2": 0, "h3": 0}
            s["error"] = None
            s["reject_note"] = ""
            n_reset += 1
    if touched:
        shots_mod.save(doc)
    logger.info("[enhance] %s ep%s: 增强1×%d 增强2×%d, 媒体失效重置×%d (共%d镜)",
                book_title, doc.get("ep"), n1, n2, n_reset, len(doc.get("shots") or []))
    return n1 + n2


def text_layer_lint(doc: dict[str, Any]) -> list[str]:
    """text_layer 机械去污 (1006 卓有ep1 审阅实锤: s01 "1966年"+"1966"同窗重复,
    s13 四 overlay 数字同屏堆叠, s11 首字幕 0.37s, s11c 复读上一镜旁白)。

    确定性修: ①同窗文本完全重复 → 删后者; ②时长 <0.5s → 拉到 0.5 (仍在镜内);
    ③同窗 overlay 条目 >3 → 删多余的 (保 hero_number 优先); ④相邻镜 caption 与
    上一镜 narration 完全相同 (拆镜复读) → 删该条。返回修复清单。"""
    import re as _re2

    fixes: list[str] = []
    ordered = sorted(doc.get("shots") or [], key=lambda s: float(s.get("t_start") or 0))
    prev_narr = ""
    for s in ordered:
        tls = s.get("text_layer")
        if not isinstance(tls, list) or not tls:
            prev_narr = str(s.get("narration") or "")
            continue
        span = float(s.get("t_end") or 0) - float(s.get("t_start") or 0)
        seen: dict[str, dict] = {}
        out: list[dict] = []
        for t in tls:
            if not isinstance(t, dict):
                out.append(t)
                continue
            txt = _re2.sub(r"\s+", "", str(t.get("text") or ""))
            t0, t1 = float(t.get("t_start") or 0), float(t.get("t_end") or 0)
            if txt and txt in seen:  # ① 同窗重复
                fixes.append(f"{s['shot_id']} 去重复[{txt[:8]}]")
                continue
            if t1 - t0 < 0.5:  # ② 时长地板
                t1 = min(t0 + 0.5, max(span, t0 + 0.5))
                t["t_end"] = round(t1, 2)
                fixes.append(f"{s['shot_id']} 短字幕拉0.5s[{txt[:8]}]")
            if txt and txt == _re2.sub(r"\s+", "", prev_narr[:len(txt) + 2]) and t.get("kind") == "caption":
                fixes.append(f"{s['shot_id']} 去复读[{txt[:8]}]")  # ④
                continue
            if txt:
                seen[txt] = t
            out.append(t)
        # ③ 同刻堆叠 (>3 条同时在场)
        events = sorted([(float(t.get("t_start") or 0), float(t.get("t_end") or 0), i)
                         for i, t in enumerate(out) if isinstance(t, dict)], key=lambda x: x[0])
        for (a0, a1, _i), (b0, _b1, _j) in zip(events, events[1:]):
            pass  # 堆叠检查走粗粒度: 任一时刻 >3 — 循环清到 ≤3 (s13 实锤单发只剔一条)
        while True:
            alive = [x for x in out if isinstance(x, dict)]
            _stacked = None
            for x in alive:
                mid = (float(x.get("t_start") or 0) + float(x.get("t_end") or 1)) / 2
                n_at = sum(1 for y in alive
                           if float(y.get("t_start") or 0) <= mid <= float(y.get("t_end") or 0))
                if n_at > 3 and x.get("kind") == "overlay":
                    _stacked = x
                    break
            if _stacked is None:
                break
            out.remove(_stacked)
            fixes.append(f"{s['shot_id']} 去堆叠[{str(_stacked.get('text'))[:6]}]")
        s["text_layer"] = out
        prev_narr = str(s.get("narration") or "")
    return fixes


def prop_lock_audit(doc: dict[str, Any], apply_colors: bool = True) -> list[str]:
    """prop_lock 确定性审计+改写 (1006 卓有ep1 审阅实锤: s29b 五圣物颜色近乎全反,
    s25/s28 圣殿 6米→百米 16 倍漂 — prop_lock 只是提示词层注入, LLM 会自行改色改尺)。

    机制: 从 arc.prop_lock 抽 (物件, 锁定色词) 与 (物件, 锁定尺度) 二元组;
    扫该场每镜 keyframe_zh/image_prompt_zh — 物件近旁出现**别的锁定色词**或
    **冲突尺度词** (百米/巨型/N米≠锁定) → 确定性改写回锁定值 (双写 kf+prompt),
    返回修复清单。判不出 (无色词/无物件) 不动 — 只修抓得实的。"""
    import re as _re

    _COLORS = ("电光蓝", "荧光橙", "紫红", "金蓝", "金色", "暗金", "深蓝", "橙", "蓝", "紫", "金", "银灰", "红")
    fixes: list[str] = []
    for arc in doc.get("arcs") or []:
        lock = str(arc.get("prop_lock") or "")
        if not lock:
            continue
        aid = arc.get("arc_id")
        # 锁定色: "X色Y" / "XY" 中 Y=圣物/道具名
        color_of: dict[str, str] = {}
        _MOD = r"(?:电光|荧光|紫红|金蓝|暗金|深蓝|纯金|银灰|深灰|深|纯|暗|金|蓝|橙|紫|银|灰|红)"
        for m in _re.finditer(rf"({_MOD}(?:蓝|橙|紫|金|灰|银|红)色?)((?:沙漏|灯塔|棱镜|筛网|熔炉|光柱|长桌|天平|棋盘|钥匙|罗盘))", lock):
            color_of.setdefault(m.group(2), m.group(1))
        # 锁定尺度: "高N米/直径N米" 贴物件
        scale_of: dict[str, str] = {}
        for m in _re.finditer(r"(沙漏|灯塔|棱镜|筛网|熔炉|圣殿)(?:[^,，。]{0,12}?)?(?:高|直径)(\d+(?:\.\d+)?米)", lock):
            scale_of.setdefault(m.group(1), m.group(2))
        if not color_of and not scale_of:
            continue
        for s in doc.get("shots") or []:
            if s.get("arc_id") != aid:
                continue
            # 1006 复核补: motion_zh 同扫 — H3 吃 motion, 只修 kf 不修 motion = 错位传导
            for field in ("keyframe_zh", "image_prompt_zh", "motion_zh"):
                txt = str(s.get(field) or "")
                if not txt:
                    continue
                orig = txt
                for obj, want_c in color_of.items():
                    if obj not in txt:
                        continue
                    # 双句式: "X色…物件" (色前) 与 "物件…X色" (色后, s29b "沙漏由荧光橙光粒凝成")
                    for pat in (rf"((?:{'|'.join(_COLORS)})色?)[^，。;；]{{0,12}}{obj}",
                                rf"{obj}[^，。;；]{{0,14}}?((?:{'|'.join(_COLORS)})色?)"):
                        for m in _re.finditer(pat, txt):
                            got = m.group(1)
                            if got and got not in want_c and want_c not in got:
                                # 1006 复核补 (ep4 光柱实锤): 枚举/多彩语境 (匹配段±14 字内
                                # 还有第二个色词, 如 "荧光橙、紫红、金色等多种") 一律只标不改 —
                                # 盲改会把色列表改成 "荧光橙、荧光橙" 文本毁损; 交人裁
                                sent = _re.split(r"[。，;；]", txt[max(0, m.start() - 60):m.end() + 60])
                                clause = next((c for c in sent if obj in c or got in c), "")
                                n_colors = len({c for c in _COLORS if c in clause})
                                if n_colors > 1 or "等" in clause:
                                    fixes.append(f"{s['shot_id']} {obj} {got}≠{want_c}[枚举,人裁]")
                                    continue
                                if not apply_colors:  # 旧集扫: 色冲突=锁定卡vs镜头的创作裁量, 只标
                                    fixes.append(f"{s['shot_id']} {obj} {got}≠{want_c}[人裁]")
                                    continue
                                txt = txt.replace(m.group(0), m.group(0).replace(got, want_c, 1), 1)
                                fixes.append(f"{s['shot_id']} {obj} {got}→{want_c}")
                    # 无色注入: 物件在场但近旁一个锁定色词都没有 (s29b "棱镜是透明多面体")
                    if not _re.search(rf"(?:{'|'.join(_COLORS)})色?[^，。;；]{{0,12}}{obj}"
                                      rf"|{obj}[^，。;；]{{0,14}}?(?:{'|'.join(_COLORS)})色?", txt):
                        txt = txt.replace(obj, f"{want_c}{obj}", 1)
                        fixes.append(f"{s['shot_id']} {obj} 无色→注入{want_c}")
                for obj, want_s in scale_of.items():
                    if obj not in txt:
                        continue
                    for m in _re.finditer(rf"{obj}[^，。;；]{{0,10}}?(高约?百米|高达百米|巨型|高约?(\d+(?:\.\d+)?)米)", txt):
                        got = m.group(2) or m.group(1)
                        if got != want_s:
                            txt = txt.replace(m.group(0), m.group(0).replace(got, want_s, 1), 1)
                            fixes.append(f"{s['shot_id']} {obj} {got}→{want_s}")
                if txt != orig:
                    s[field] = txt
    return fixes


def auto_fix_banned(doc: dict[str, Any]) -> int:
    """自动修复禁区词 (0924 用户令: 已知问题自动修, 修完再生成 — 不死循环等人).

    替换表 = 已知安全的同义替换; 修不了的才留标记给人检.
    0926 修: 撤 political_flag 前置门 — gpu 相位"扫描→修→复扫"链里新命中
    的镜还没打标, 旧版直接跳过 → 复扫仍中 → 误报"修不了"死循环 (s42 横幅
    实锤: 表里有 横幅→装饰带 却永远轮不到执行)。现在凡命中可映射词即修。
    """
    _FIX = {
        "招牌": "灯牌", "红旗": "旗座", "red flag": "marker flag",
        "Red flag": "Marker flag", "国旗": "", "军装": "制服",
        "警服": "制服",
        # 1004 修 (ep5 s16 死循环实锤): 地图→路线图 是视觉孪生 (VLM 照杀) —
        # 换棋盘族载体 (格面/竞争隐喻/政治无感), 世界地图先于地图匹配
        "世界地图": "世界棋盘", "中国地图": "立体沙盘",  # 1015 口径: 裸「地图」除名 (compliance 同尺)
        "领导人": "主讲人", "政治人物": "人物",
        "标语牌": "信息板", "横幅": "装饰带", "告示牌": "指示板",
        # 1010 烟草酒精族 (ep1 s81b/s82 死循环实锤: 题材本身是《纽约客》抽烟
        # 漫画, LLM 重掷必回声 烟圈/雪茄 — 替换表无此族 = 命中即 political_flag
        # +K2 永久跳过, redo/replan 赌不中就永远卡 planned)。同义安全替换:
        "香烟": "钢笔", "雪茄": "钢笔", "烟斗": "钢笔", "烟灰缸": "小瓷碟",
        "吸烟": "托腮沉思", "抽烟": "若有所思", "烟圈": "袅袅热气",
        "喝酒": "举杯畅饮", "饮酒": "举杯畅饮", "醉酒": "酣然入睡",
        "酒精": "灯油", "酒瓶": "玻璃瓶", "啤酒": "麦芽饮料", "白酒": "清茶",
    }
    n = 0
    for s in doc.get("shots") or []:
        fixed = False
        for field in ("keyframe_zh", "keyframe_en", "image_prompt_zh", "motion_zh"):
            old = str(s.get(field) or "")
            new = old
            for bad, good in _FIX.items():
                if bad in new:
                    new = new.replace(bad, good)
            if new != old:
                s[field] = new
                fixed = True
        # 1013 修: 花字补齐 — scan 扫 text_layer 但 fix 不修 → 禁区词藏花字
        # (s26"吐烟圈"/s35"· 地图"实锤) 复扫仍中 → "修不了" → political_flag
        # 永久跳过。字段面必须与 visual_ban_scan 一把尺。
        for t in (s.get("text_layer") or []):
            old_t = str(t.get("text") or "")
            new_t = old_t
            for bad, good in _FIX.items():
                if bad in new_t:
                    new_t = new_t.replace(bad, good)
            if new_t != old_t:
                t["text"] = new_t
                fixed = True
        if fixed:
            s.pop("political_flag", None)
            n += 1
            logger.info("[auto-fix] %s 禁区词已替换 (继续生图)", s.get("shot_id"))
    if n:
        logger.info("[auto-fix] %d 镜禁区词自动修复 ✓ (调用方负责落盘)", n)
    return n


def visual_ban_scan(doc: dict[str, Any]) -> list[str]:
    """政治敏感画面禁区扫描 (0917 用户令): 返回 ["s01(国旗)", ...] 命中镜清单.

    词库单一事实源 = config/compliance_visual.json (compliance.check_visual);
    扫描面 = keyframe_zh/motion_zh/text_layer 全部画面文本。
    """
    from app.services import compliance as _cmp
    out: list[str] = []
    for s in doc.get("shots") or []:
        blob = " ".join(filter(None, [
            str(s.get("keyframe_zh") or ""), str(s.get("keyframe_en") or ""),
            str(s.get("motion_zh") or ""),
            " ".join(str(t.get("text") or "") for t in (s.get("text_layer") or [])),
        ]))
        hits = _cmp.check_visual(blob)
        if hits:
            out.append(f"{s['shot_id']}({'/'.join(hits[:2])})")
    return out


def duration_audit(doc: dict[str, Any], segs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """时长核验 (0917 用户令): 音频时长 ↔ 规划时长 ↔ H3 生成窗 三方对账.

    场级: 场窗时长 (doc 轴, 音频锚定) vs 场内镜槽合计; 给 segs (音频包/句级,
    音频轴 0 起) 时再独立对账 场窗与音频段重叠合计。
    镜级: 音频槽 (t_end-t_start) vs H3 生成窗 (anim.duration_s) vs beats 铺满。
    返回 {"arcs": rows, "shots": rows, "problems": [...]}; problems 供 plan_warnings/报告。
    """
    tc = float(load().brand_card.opening_sec)
    shots = sorted(doc.get("shots") or [], key=lambda s: float(s.get("t_start") or 0))
    by_arc: dict[str, list[dict[str, Any]]] = {}
    for s in shots:
        by_arc.setdefault(str(s.get("arc_id")), []).append(s)
    problems: list[str] = []
    arc_rows: list[dict[str, Any]] = []
    for a in doc.get("arcs") or []:
        aid = str(a.get("arc_id"))
        ss = by_arc.get(aid) or []
        a0, a1 = float(a.get("t_start") or 0), float(a.get("t_end") or 0)
        audio = round(a1 - a0, 2)
        planned = round(sum(float(s["t_end"]) - float(s["t_start"]) for s in ss), 2)
        gen = round(sum(float((s.get("anim") or {}).get("duration_s") or 0) for s in ss), 2)
        row: dict[str, Any] = {"arc_id": aid,
                               "module": (a.get("module_id") + 1) if a.get("module_id") is not None else "",
                               "audio_s": audio, "planned_s": planned,
                               "h3_window_s": gen, "shots": len(ss)}
        if not ss:
            problems.append(f"[时长核验] {aid} 场无镜 (骨架未分镜?)")
        elif abs(planned - audio) > 0.5:
            problems.append(f"[时长核验] {aid} 音频槽 {audio:.1f}s ≠ 镜槽合计 {planned:.1f}s "
                            f"(差 {planned - audio:+.1f}s)")
        if segs:
            ov = 0.0
            for g in segs:
                s0, s1 = float(g.get("t_start") or 0), float(g.get("t_end") or 0)
                lo, hi = max(s0, a0 - tc), min(s1, a1 - tc)
                if hi > lo:
                    ov += hi - lo
            row["audio_seg_overlap_s"] = round(ov, 2)
            if abs(ov - audio) > 0.6:
                problems.append(f"[时长核验] {aid} 场窗 {audio:.1f}s 与音频段重叠 {ov:.1f}s 不符 (时间轴脱锚)")
        arc_rows.append(row)
    shot_rows: list[dict[str, Any]] = []
    import subprocess as _sp
    _ep_dir = shots_mod.ep_dir(str(doc.get("book_title")), int(doc.get("ep") or 1))
    for s in shots:
        slot = round(float(s["t_end"]) - float(s["t_start"]), 2)
        anim = s.get("anim") or {}
        dur = round(float(anim.get("duration_s") or 0), 2)
        # 0918 核验盲区补: 实测视频文件时长 (声明值对/文件旧 = 漏网, 5镜实锤)
        _vf = s.get("video_file")
        if _vf and "anim/" in str(_vf) and (_ep_dir / str(_vf)).exists():
            try:
                _o = _sp.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(_ep_dir / str(_vf))],
                             capture_output=True, text=True, timeout=10).stdout.strip()
                _real = round(float(_o), 2) if _o else -1.0
            except Exception:  # noqa: BLE001
                _real = -1.0
            if _real > 0 and abs(_real - slot) > 1.0:
                problems.append(f"[时长核验] {s['shot_id']} 视频实测 {_real:.1f}s ≠ 槽 {slot:.1f}s "
                                f"(旧文件/错版, 需按槽重roll)")
                shot_rows.append({"shot_id": s["shot_id"], "slot_s": slot, "h3_s": dur,
                                  "beats_end_s": 0.0, "video_real_s": _real})
                continue
        bend = round(max((float(b.get("t_end") or 0) for b in (anim.get("beats") or [])), default=0.0), 2)
        shot_rows.append({"shot_id": s["shot_id"], "slot_s": slot, "h3_s": dur, "beats_end_s": bend})
        if abs(dur - slot) > 0.5:
            kind = "H3窗短→草稿定格补差" if dur < slot else "H3窗长→截断"
            problems.append(f"[时长核验] {s['shot_id']} 音频槽 {slot:.1f}s ≠ H3窗 {dur:.1f}s ({kind})")
        if dur > 0 and bend < dur - 0.5:
            problems.append(f"[时长核验] {s['shot_id']} beats 只铺到 {bend:.1f}s < H3窗 {dur:.1f}s")
    return {"arcs": arc_rows, "shots": shot_rows, "problems": problems}


def write_duration_sheet(doc: dict[str, Any], audit: dict[str, Any]) -> Path:
    """时长核验表落盘 (ep 目录 时长核验.md) — 人读对账表, 任何时刻可复查."""
    ep_dir = shots_mod.ep_dir(str(doc.get("book_title")), int(doc.get("ep") or 1))
    lines = ["# 时长核验 — 音频时长 ↔ 规划镜槽 ↔ H3 生成窗", "",
             "## 场级", "| 场 | 模块 | 音频槽s | 镜槽合计s | H3窗合计s | 镜数 |",
             "|---|---|---|---|---|---|"]
    for r in audit.get("arcs") or []:
        lines.append(f"| {r['arc_id']} | {r.get('module', '')} | {r['audio_s']} | "
                     f"{r['planned_s']} | {r['h3_window_s']} | {r['shots']} |")
    mismatch = [r for r in audit.get("shots") or [] if abs(r["h3_s"] - r["slot_s"]) > 0.5]
    lines += ["", "## 镜级 (仅列 音频槽≠H3窗 >0.5s 的镜)", "",
              "| 镜 | 音频槽s | H3窗s |", "|---|---|---|"]
    for r in mismatch:
        lines.append(f"| {r['shot_id']} | {r['slot_s']} | {r['h3_s']} |")
    if not mismatch:
        lines.append("(全镜 H3窗=音频槽 ✓)")
    lines += ["", "## 问题清单", ""]
    lines += [f"- {p}" for p in (audit.get("problems") or [])] or ["- 无 ✓"]
    path = ep_dir / "时长核验.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
