# 蒸馏产物 Schema 设计（两级蒸馏 · 面向产线）

> 2026-08-22 讨论定稿。核心转向：**不在下游提示词上打补丁，在蒸馏时就产出对后面有用的内容**。
> 关联：`docs/design/拆书项目-实施方案.md`、`app/services/book_service/distiller.py`（现蒸馏 v2）。

---

## 0. 一句话

蒸馏分两级：**第一层 L0 章节蒸馏**（无枷锁把书变薄，全量保真，一次做好全下游共用）→ **第二层面向蒸馏**（多 agent，各拿 L0 章节跑一个维度，产出结构化产物供补全/总纲/逐集/合规直接消费）。

### 为什么是两级（而非一次压到底）
- 现有蒸馏 = 通用压缩 → 下游每个环节（补全/总纲/逐集）**各自再从摘要里解读/编造**，判断不可靠
- 两级 = 第一层只做"变薄+保真"（原料仓），**所有判断下沉到第二层**（此时有 L0 章节的全局上下文，判断更准）
- 新维度只需新增一个面向 agent，**不重跑全书**（输入是 L0 章节，非原文）

---

## 1. 两级蒸馏框架

```
① L0 章节蒸馏（第一层，通用，无面向枷锁）
   输入: 全书（按章/节分块） → 输出: 章节级结构化 L0（原料，全量保留，判断不做）

② 面向蒸馏（第二层，多 agent，各取所需）
   输入: 同一份 L0 章节（不重跑全书） → 输出: 各维度结构化产物
   每个面向 = 一个独立 LLM 调用（专注一个维度，prompt 更准）
```

---

## 2. 第一层 · L0 章节 schema

目标：把书变薄，**全量保留信息、不筛不评**（金句类型/敏感级别等判断留给第二层）。

```json
{
  "schema": "l0-chapter-v1",
  "book": "遇见未知的自己",
  "author": "张德芬",
  "meta": {
    "publisher": "华夏出版社",
    "isbn": "978-7-5080-0000-0",
    "pub_date": "2007-06",
    "book_intro": "华语世界第一部销量破百万的身心灵成长小说…",
    "author_bio": "张德芬，台湾作家、心理咨询师，毕业于台湾大学…",
    "toc": ["1 一场奇怪的对话", "2 老人的读心术", "…", "22 开始，就是未来"],
    "source": {
      "publisher/isbn/pub_date": "epub OPF metadata（书内，非爬虫）",
      "toc": "epub nav/toc.ncx 目录结构 或 txt 书内目录段",
      "book_intro": "书内前言/导言/封底简介",
      "author_bio": "书内作者页/前言/封底作者简介"
    }
  },
  "total_chapters": 22,
  "frontmatter": [
    {"type": "作者序", "author": "张德芬",
     "core_claim_self_stated": "作者自述本书想解决…",
     "version_delta": "再版新增『老人的信』一章，修订胜肽表述",
     "context": "创作背景/灵感来源",
     "value_note": "作者自述主张 = facing-kernel 高置信来源"}
  ],
  "chapters": [
    {
      "idx": 3,
      "title": "马车模型：谁在替你做决定",
      "text": "§B§我们以为自己在做决定，其实只是执行车夫的命令§/B§。…长尾效应【2】在电商中体现为…",
      "summary": "用马车比喻三层意识：马=表层意识、车夫=潜意识、乘客=真我，揭示惯性与旧地图。",
      "concepts": [
        {"name": "马车模型", "mech": "马/车夫/乘客对应表层意识/潜意识/真我，惯性来自车夫按童年旧地图导航"},
        {"name": "胜肽理论", "mech": "细胞受体因长期情绪惯形成特定胜肽需求，重复吸引同类情境"},
        {"name": "长尾效应", "mech": "网络时代商品存储/流通成本趋零，销量小但种类多的尾部商品总利润可超热门头部", "note_ref": "n2"}
      ],
      "notes": [
        {"id": "n2", "ref_marker": "【2】", "term": "长尾效应",
         "explanation": "长尾效应：网络时代商品存储/流通成本趋零，销量小但种类多的"尾部"商品总利润可超热门"头部"…",
         "location": "章尾注释"}
      ],
      "cases": [
        {"desc": "若菱总被同一类人吸引的困惑", "scene": "开篇咨询场景"}
      ],
      "quotes": [
        {"text": "我们以为自己在做决定，其实只是执行车夫的命令", "emphasis": true, "source": "粗体"}
      ],
      "data": [],
      "entities": ["若菱", "老人", "胜肽"],
      "references": [
        {"type": "book", "name": "思考，快与慢", "author": "丹尼尔·卡尼曼", "context": "锚定效应部分引用", "chapter": 11},
        {"type": "theory", "name": "斯多葛学派", "context": "情绪管理部分提及", "chapter": 5},
        {"type": "person", "name": "弗洛伊德", "context": "潜意识概念溯源", "chapter": 3}
      ],
      "note": "含轻度唯心表述（胜肽、能量），神/宗教无"
    }
  ],
  "backmatter": [
    {"type": "术语表", "title": "…", "extracted": {"terms": [{"term": "…", "def": "…"}]}},
    {"type": "参考文献", "title": "…", "extracted": {"refs": ["《思考快与慢》…"]}},
    {"type": "数据/方法论附录", "title": "…", "extracted": {"data": ["…"], "methods": ["…"]}}
  ]
}
```

> **`meta` 块（2026-08-22 补）**：作者简介/章节目录等基础元信息**从书内提取**（epub OPF 元数据 + nav/toc.ncx 目录 + 前言/封底），**不依赖爬虫**——书中自然都有。
> 爬虫（`fetch_zhihailib_meta`，L1）仅作书内缺失字段的可选补全（如 epub 无作者简介时），非必需路径。
> **`quotes`** 仍是 L0 全量原句（含情境型，**不筛类型**——类型判断在第二层 facing-quotes），但**带 `emphasis` 标记**（排版强调是高置信金句信号）。
> `note` 只做中性标记，不做判断（判断交合规 agent）。
>
> **排版强调保留（2026-08-22 补）**：作者/出版商的排版标记 = 划重点信号（金句/公式/重点的高置信标注），**L0 必须保留，不丢给下游猜**。
> 章节 `text` 用**内联标记**保留强调（reader 提取时转）：
> - `§B§…§/B§` 粗体（`<b>/<strong>`/class 含 `bold`）
> - `§I§…§/I§` 斜体（`<i>/<em>`）
> - `§BOX§…§/BOX§` 强调框/示例（`<blockquote>`/class 含 `box/tip/note/example`）
>
> L0 提取时，被强调的内容自动标 `emphasis: true`（quotes 的 `source` 记来源标记）；quotes 输出剥 `§` 标记。
> txt 无排版标记 → 无强调信号，走"无强调"路径（不强行造）。
> 公式（MathML/图片）当前不提取，`note` 标注"本章含公式"。
>
> **注释/角标知识库（2026-08-22 补）**：正文角标（`长尾效应【2】`）+ 章尾/书尾注释 = 作者的**术语官方定义**，是 L0 的"知识库体系"。
> - `notes` 由 **py 解析**（epub 锚点 `<a href="#noteN">`+`<sup>`+注释块 / txt 角标 `【N】`+`[N]`+`①②` 匹配"注释"段），注释文本是原文，**不编造**
> - concept 可带 `note_ref` → L0 提取时，有注释的概念**用注释原文作 mech**（比 LLM 自己总结准）
> - 逐集讲解概念时拿注释原文讲（带"书中注释提到"限定），不编
> - `_strip_html` 改造时**不得丢 `<sup>`/锚点/注释块**（当前丢的就是这些）
>
> **引用源头 references（2026-08-22 补）**：书内提到的理论源头/书/作家 = 作者引用的"知识出处"，是**隐藏选题池**（讲完一本可顺带讲源头书，自带分量与读者信任，频道增长素材）。
> - 类型：`book`（书名/作者）/ `theory`（理论流派）/ `person`（学者+观点溯源）
> - 识别：**LLM 干精**（判断"这是书/理论/人 + 溯源关系"）；py 辅助书名号 `《》`/引号先行标记
> - 与 `entities` 区分：entities 通用实体（讲概念用），references 可追溯的"书/理论/人"（选题池 + 内容关联用）——单独字段不混
> - 下游：全库 references 聚合 → 源头书池（下一批拆书候选）；逐集结尾带出"作者还在《XX》里讲过…"；源头作者作素材包检索锚点
>
> **序言/附录独立处理（2026-08-22 补）**：frontmatter（序言）与 backmatter（附录）**非正文但信息量极大**，单独提取不混正文：
> - **frontmatter**（作者序/再版序/他人序）：作者自述核心主张（= facing-kernel 高置信来源）、再版版本差异/观点演化、创作背景、他人序理解框架
> - **backmatter**（术语表/参考文献/数据附录/方法论/后记）：术语表→concepts 补充、参考文献→**references 补充（源头书单）**、数据/方法论→data/cases 补充
> - 定位：**py 靠内容/标题特征**（序/前言/自序/再版序/附录/术语表/参考文献/后记/致谢），不靠文件名（epub 文件名是序号不可靠）；reader 章节头正则已含这些词 → 扩展 `role: frontmatter/backmatter`
> - 提取：LLM 分别处理（frontmatter→自述主张/版本差异；backmatter→术语/引用/数据）
> - 为何单独：序言/附录信息密度与正文不同，混进正文章节会被稀释或丢关键信号

---

## 3. 第二层 · 面向 schema（多 agent）

每个面向一个 JSON，输入都是 `l0-chapter-v1`。8 个面向（kernel/units/quotes/cases/compliance/readers/hooks/relations）：

### 3.1 内核蒸馏（facing-kernel）— 供补全 / 总纲（#4）
```json
{
  "schema": "facing-kernel-v1",
  "one_liner": "本书要扭转的默认认知：人以为痛苦来自外界遭遇，实际来自内在旧程序（潜意识车夫）",
  "framework": "同心圆（真我→身体→情绪→思想→身份）逐层剥离的自我认知路径",
  "unique_angle": "用心灵成长包装的自我认知方法论（非传统心理学，含灵性成分）",
  "reader_shift": ["从'都是别人的错'→'我内在的程序在重演'", "从向外归因→向内觉察"],
  "eligible": true
}
```

### 3.2 主题单元蒸馏（facing-units）— 供总纲集数 / 逐集（#1 #4 关键）
每个单元 = 一个"能独立讲 5-8 分钟"的主题；**`units.length` = 该拆几集**（自适应集数基础）。
**三段结构（2026-08-22 升级）**：单元带 `role`（导读/拆解/落地）——第 1 单元=全书导读（为什么读/买，统揽全局）、中间=内容拆解、最后=落地应用（行动清单）；`selling_point` = 每集一句话卖点（挂车钩子）。
```json
{
  "schema": "facing-units-v1",
  "units": [
    {
      "id": "u01", "role": "导读",
      "title": "为什么每个人都需要一次蛤蟆式心理咨询？",
      "core_claim": "这本书通过寓言故事，教你用交互分析看清自己",
      "concepts": ["交互分析", "自我觉察"],
      "reader_resonance": ["面对低谷不知从哪开始求助"],
      "analogy_hooks": ["就像黑暗中有人为你拿灯"],
      "selling_point": "看完这集你将明白为什么这本书能成为心理学入门神作",
      "sensitive": {"level": "green", "hits": [], "safe_angle": ""}
    },
    {
      "id": "u03", "role": "拆解",
      "title": "破解人生剧本：你是在演戏，还是在生活？",
      "core_claim": "人生并非随机，而是潜意识策划的剧本在重演",
      "concepts": ["人生剧本", "人生坐标"],
      "reader_resonance": ["明知该放手却反复回到同一段关系"],
      "analogy_hooks": ["就像手机默认设置，没人改就一直按出厂状态跑"],
      "selling_point": "教你识破潜意识里的自毁程序，从我不行走向我好你也好",
      "sensitive": {"level": "yellow", "hits": ["宿命"], "safe_angle": "用习惯回路/认知偏差的心理学语言重述"}
    },
    {
      "id": "u05", "role": "落地",
      "title": "从蛤蟆到独立个体：一套可操作的自我疗愈清单",
      "core_claim": "成长的终点是意识到自己拥有选择权并自我负责",
      "concepts": ["自我负责", "心理独立"],
      "reader_resonance": ["情绪内耗想结束却不知怎么开始"],
      "analogy_hooks": ["把人生方向盘从过去的阴影手中夺回来"],
      "selling_point": "提供一套自我心理扫描清单，面对冲突和低谷有具体步骤",
      "sensitive": {"level": "green", "hits": [], "safe_angle": ""}
    }
  ]
}
```
> 总纲从 units 编排：E1=导读（含**系列预告**：共 N 集 + 各集主题，引导追更/关注）、中间=拆解、末集=落地。
> 逐集 E1 prompt 注入系列预告（开头 20-30 秒预告整体系列，禁止空洞求关）。

### 3.3 金句蒸馏（facing-quotes）— 供补全核心金句 / 逐集引用（#3）
```json
{
  "schema": "facing-quotes-v1",
  "quotes": [
    {"text": "你怎样看待这个世界，这个世界就怎样回应你", "type": "格言型", "chapter": 5},
    {"text": "悟空，你走吧", "type": "情境型", "chapter": 27,
     "context": "三打白骨精后唐僧逐徒", "usage": "只能借该情节引用，不单独上屏"}
  ]
}
```
> `type` 判断在第二层做（输入含章节上下文，可判承启）；`情境型` 强制带 `context`，无 context 不得入 `核心金句`。

### 3.4 案例蒸馏（facing-cases）— 供逐集"书内案例"
```json
{
  "schema": "facing-cases-v1",
  "cases": [
    {"id": "c04", "title": "若菱的婚姻困境", "chapter": 3,
     "desc": "…", "quote": "…", "scene": "…",
     "usable": true,
     "era_sensitive": true,
     "contemporary_analogy": "今天类似的是：社交媒体上反复和前任纠缠的处境",
     "usage": "书内案例引原文 + 当代类比桥接（标注'放到今天就像…'）"}
  ]
}
```
> **案例时效桥接（2026-08-22 补，落点第二层非 L0）**：老书的技术/通讯/社会形态类例子易让现代观众脱节。
> - 原则：**不替换书内案例**（作者原意 + 历史价值），**并置当代等效类比**（"书里当年是电报，今天就是微信"）
> - `era_sensitive`（LLM 判断）：技术/通讯/社会形态/消费习惯类易脱节；人性/情感/历史/文学价值类不太脱节；**历史/神话类不桥接**（保留历史语境）
> - `contemporary_analogy`（LLM 生成）：当代等效类比，**非书中内容**，逐集须标注（对应"书外类比明显打比方"）
> - `meta.pub_date`（py 传）作时间锚：出版 >20 年的书默认多考虑桥接
> - **为什么不在 L0**：L0 只有书内信息无当代知识；桥接靠 LLM 知识/外部检索，属第二层/逐集

### 3.5 合规蒸馏（facing-compliance）— 供 Gate0 / GateB（#2）
```json
{
  "schema": "facing-compliance-v1",
  "book_tier": "yellow",
  "hard_blacklist_hits": [],
  "sensitive_units": [
    {"unit_id": "u03", "hits": ["胜肽", "能量"], "risk": "唯心/伪科学表述", "action": "换角度"},
    {"unit_id": "u07", "hits": ["21天"], "risk": "确定性断言", "action": "弱化/锚定书中建议"}
  ],
  "safe_angles": ["用心理学/习惯科学框架重述", "书中观点限定（书中提出/作者认为）", "避免'改变命运'类绝对化大字"]
}
```

### 3.6 读者共鸣蒸馏（facing-readers）— 供评论层 / 逐集戳痛点
```json
{
  "schema": "facing-readers-v1",
  "readers": [
    {"pain": "明知该放手却反复回到同一段关系", "unit_id": "u03"},
    {"pain": "被生活推着走，觉得没有选择权", "unit_id": "u03"}
  ]
}
```

### 3.7 开篇钩子蒸馏（facing-hooks）— 供逐集开篇/结尾互动（2026-08-22）
关联 facing-units（unit_id/title/selling_point → 开篇钩子/结尾互动/下集预告），每单元一组钩子。

### 3.8 全书理解蒸馏（facing-relations）— 供逐集人物身份 / 概念定位 / 全书把握（2026-08-23）

**定位**：L0 是提取层（entities 只有名字、概念只有定义、各章割裂），理解加工在面向层 — relations 把散落各章的信息聚合成本书理解层，解决"獾是谁下游只能猜"。输入含【作者序/导读】正文 +【全书人物/实体】跨章聚合（build_facing_input 2026-08-23 补，此前作者序只给名称）。
```json
{
  "schema": "facing-relations-v1",
  "characters": [
    {"name": "老獾", "identity": "强势且具有掌控欲的朋友",
     "relation_to_hero": "对立者/压力源",
     "role_in_book": "代表典型的父母状态和权威压制，触发蛤蟆受害者模式"}
  ],
  "concept_roles": [
    {"name": "人生坐标", "why_important": "定义个体对自我与他人价值的根本认知",
     "position_in_argument": "核心框架：认知升级的终点，决定人生剧本走向"}
  ],
  "author_intent": {
    "core_question": "如何打破童年负面心理剧本、从抑郁依赖中解脱",
    "framework": "症状→自我状态分析→童年根源→行为模式→人生坐标重构",
    "reader_journey": "从共情抑郁症状到认知升级'我好你也好'"}
}
```
**消费**：逐集生成 `_relations_block` 注入 — 人物首次出现直接用图谱身份介绍（不再靠模型猜）；概念标注核心框架 vs 支撑。

---

## 4. 关键设计决策

1. **L0 全量保留 vs 面向裁剪**：L0 是"原料仓"（全量、中性），面向产物是"加工品"（筛选、判断）。L0 一次做好，所有面向共用同一份 L0 章节，加新面向只新增 agent，不重跑全书。
2. **面向的输入 = L0 章节，不是全书**：多 agent 的"不重跑整本书"正是这个——每章已蒸馏成几千字结构，128k 上下文能装下整本书的 L0。
3. **判断尽量下沉到第二层**：金句类型、敏感级别、单元划分都在第二层做（此时有 L0 章节的全局上下文，判断更准）；L0 保持"不筛"避免第一层丢失信息。
4. **`usage` 字段（金句/案例）**：第二层直接标"这条能怎么用"（独立上屏 / 借情节引用 / 换角度讲），下游不再需要判断。

---

## 5. 落地路径（与现有 5 步管线关系）

| 面向产物 | 消费方 |
|---|---|
| l0-chapter-v1 | 所有面向的输入（原料仓） |
| facing-kernel | 补全（核心主张/读者画像） |
| facing-units | 总纲（集数自适应 + 逐集主题）、逐集（单元素材） |
| facing-quotes | 补全核心金句、逐集引用 |
| facing-cases | 逐集书内案例 |
| facing-compliance | Gate0 预评估、GateB 逐集审查 |
| facing-readers | 评论层、逐集戳痛点 |

**建议两步走**：
1. 先动**第一层 L0 章节蒸馏** + **第二层金句/案例面向**，验证"源头结构化让下游干净"（即"蒸馏时蒸馏出对后面有用的内容"）
2. 单元池（facing-units）跑稳后，把总纲/逐集切到按单元消费（此时才碰自适应集数 #1）

---

## 6. 与现有实现的关系

- 现 `_EXTRACT_SYS`（提取池：观点/概念/案例/数据/原句）→ 演进为 `l0-chapter-v1`（按章/节分块，每章结构化）
- 现 `_COMPOSE_SYS`（编纂成"规模.txt 式精华稿"）→ 演进为各 facing-* 面向（不再压成单篇摘要，产出结构化 JSON）
- 现 `distiller.distill_book` 的"分块穷尽提取 + 编纂轮"骨架可复用，面向 agent 复用 `GemmaClient`（128k 上下文）
- 精华稿"可引用原句" → 拆成 facing-quotes（分级 + 带 context）

---

## 7. 第一层 L0 实施顺序 + py/LLM 分工

> **✅ 已落地（2026-08-22）**：两级蒸馏 + 拆书 5 步融合已全部实现并验证。
> - **reader.py v3**：epub OPF 元数据/toc 目录/spine 顺序/frontmatter-backmatter/排版强调(§B§/§I§/§BOX§)/注释锚点/txt 增强；**文件内按"第X章"正文标题分节**（含 §B§ 粗体标记 + 目录节过滤）→ L0 章节粒度 = 真实章
> - **l0.py**：章节块 + 自适应打包（小章合并/大章拆 part/≤12K）+ `l0-chapter-v1.json` 落盘 `data/l0/{书名}/`
> - **facing.py**：kernel/units(三段结构)/quotes(格言/情境分级)/cases(era_sensitive+当代桥接)/compliance/readers 六面向
> - **UI 融合**：books_content 🧠 L0 面板 + books.html 书库 L0 标 + books_story 总纲(卖点/系列预告) + 分析类按钮去掉(蒸馏自动填充)
> - 核心流程：创建书 → 蒸馏(L0+facing, 命令行) → auto-fill 自动填充(补全/合规/评论层/总纲) → 讲书逐集创作 → 进产线

**原则：py 干"书怎么变成章节"（确定性结构），LLM 干"章节里有什么"（语义提取）。每一环都落在 l0 层，不扩散到补全/总纲去猜。**

### py/LLM 分工表

| 任务 | 方式 | L0 环节 | 说明 |
|---|---|---|---|
| 文件解析 + 分章 | **py** | 结构 | reader: txt/epub → 章节原文块（已有，增强） |
| epub OPF 元数据 | **py** | 结构 | container.xml → OPF `<metadata>`：title/creator/publisher/isbn/date（xml 确定性） |
| epub 目录 toc | **py** | 结构 | toc.ncx / nav.xhtml → 章节标题层级（比文件名排序准） |
| 排版强调转内联标记 | **py** | 结构 | `_strip_html` 保留 `<b>/<i>/<blockquote>`/class bold → `§B§/§I§/§BOX§` 内联标记 |
| 注释/角标解析 | **py** | 结构 | epub 锚点 `<a href="#noteN">`+`<sup>`+注释块 / txt 角标 `【N】`+`[N]`+`①②` 匹配"注释"段 → `notes`（原文，不编造） |
| 前言/作者简介定位 | **py** | 结构 | 规则识别 frontmatter 章节（"前言/序/关于作者"） |
| 序言/附录定位 | **py** | 结构 | 内容/标题特征识别 frontmatter/backmatter（作者序/再版序/附录/术语表/参考文献/后记），扩展 `role` |
| txt 目录/作者简介扫描 | **py** | 结构 | 开头段规则（"目录"/"关于作者"） |
| 章节块自适应打包 | **py** | 结构 | 小章合并 / 大章拆 part / 目标 ≤12K 字符，尊重语义边界，防 LLM 崩 |
| 章节 summary | **LLM** | 提取 | 每章主旨（1-2 句） |
| concepts + mech | **LLM** | 提取 | 语义概念 + 机制解释；**有 `note_ref` 的概念用注释原文作 mech** |
| entities | **LLM** | 提取 | 人物/概念/事件实体 |
| references 引用源头 | **LLM** | 提取 | 书内提到的书/理论/人（`book`/`theory`/`person` + 溯源 context）；py 书名号 `《》` 先行标记辅助 |
| cases / quotes 原句 | **LLM** | 提取 | 穷尽提取，原句逐字（防幻觉资产）；**排版强调区优先，标 `emphasis`**，输出剥 `§` |
| note 中性标记 | **LLM** | 提取 | 这章涉及什么维度（唯心/宗教/历史…）——中性，不做判断 |
| 硬黑名单过滤 | **py** | 校验 | hard_blacklist_quotes 确定性删（不依赖 LLM） |
| schema 校验 + 落盘 | **py** | 校验 | 字段完整性 + `l0-chapter-v1` JSON 落盘 + 章节块缓存 |

### 实施顺序（依赖驱动）

**P0 · reader.py 增强（py）**
- `_read_epub`：读 OPF metadata（publisher/isbn/pub_date/creator）+ toc.ncx/nav 目录 + 前言/作者简介 → 产出 `meta`，章节带 `role`（frontmatter/body）
- **排版强调保留**：`_strip_html` 从"全剥标签"改为"保留强调转内联标记"——`<b>/<strong>`/class 含 `bold` → `§B§…§/B§`；`<i>/<em>` → `§I§…§/I§`；`<blockquote>`/class 含 `box/tip/note/example` → `§BOX§…§/BOX§`；不再压空白（保句读）。calibre 系排版类（`calibre`/`calibreN`）不算强调
- **注释/角标解析**：epub 识别 `<a href="#noteN">`+`<sup>`+注释块 / 书尾注释 section → `notes`；txt 识别角标 `【N】`/`[N]`/`①②` + 章尾/书尾"注释"段按编号匹配 → `notes`。注释文本原文保留不编造；`_strip_html` 不丢 `<sup>`/锚点/注释块
- `_read_txt`：扫开头目录 + 作者简介段 → 产出 `meta`（txt 无排版强调，走无强调路径）
- **序言/附录定位**：靠内容/标题特征（"作者序/自序/再版序/前言"→frontmatter；"附录/术语表/参考文献/数据附录/后记/致谢"→backmatter）识别并标 `role`，不靠文件名（epub 序号名不可靠）
- `read_book` 返回 `{book_title, meta, frontmatter[], chapters[role, text 带内联标记+notes], backmatter[], total_chars}`
- 验证：对书库真实 epub/txt 抽查 meta 提取质量（出版社/目录/作者简介）+ 强调标记保留（粗体/斜体/框是否转对）+ 注释关联（若有角标样本）

**P1 · 章节块落盘 + 自适应打包（py）**
- 把 reader 产出组装成"章节原文块"（每章一个独立 JSON/文件：{idx, title, role, text 带 § 标记} + 顶层 meta）
- **L0 产物独立目录（2026-08-22）**：落**项目内 `data/l0/{书名或book_id}/`**（`data/` 是 config `data_dir`，随项目迁移、可 git 盘查），不落外部 `E:/数字人计划/`、不污染书源目录（`G:/Desktop/畅销书/`）
  ```
  data/l0/{book}/
  ├── meta.json              # 元信息 + pub_date
  ├── chapters/              # 章节块（chapter_01.json …）
  ├── l0-chapter-v1.json     # 合并的 L0（全书）
  └── facing/                # 第二层面向产物
      ├── facing-kernel.json / facing-units.json / facing-quotes.json
      ├── facing-cases.json / facing-compliance.json / facing-readers.json
  ```
  配置：`config/app.yaml` 加 `l0_output_root`（默认 `data/l0`，相对 data_dir）
- **自适应打包**（2026-08-22，原则：尊重语义边界，别让本地 LLM 崩）：
  - 目标：每个提取单元 ≤ **~12K 字符**（≈6-8K token 输入，留足输出 + 128k 余量，稳定不 OOM）
  - **小章**（<3K）：多个小章合并进同一提取单元（一组 ≤3-4 章）
  - **中章**（3-12K）：单独一个单元
  - **大章**（>12K）：按子标题/段落切成 ≤12K 子块，每子块一单元，标 `part` 序号（保留章归属）
  - 产出"提取单元"：`{chapters:[起始章..结束章], parts, text}`（非固定字符数，按语义边界）
- 目的：LLM 提取与第二层面向都只读章节块，不重读全书；打包后按单元喂 LLM，跨单元上下文靠"章归属 + 全书 L0 总览"衔接

**P2 · L0 LLM 按打包单元提取（LLM，distiller）**
- 每个提取单元 → Gemma 提取：summary/concepts/cases/quotes/entities/references/note（复用 `GemmaClient` + 128k）
- 大章的多个 part 提取后**归并到该章**（同一章 sub-chunk 结果合并为该章 L0）
- 小章合并在同一单元提取 → 按起始章归并到各章
- **prompt 说明 § 标记语义**：`§B§/§I§/§BOX§` 为排版强调（金句/公式/重点高置信信号），提取 quotes/concepts/cases 时优先参考；quotes 输出去 `§` 并标 `emphasis:true` + `source`
- 逐单元独立调用（并行可行），产出结构化
- **frontmatter/backmatter 单独提取**（LLM）：序言→作者自述主张/再版版本差异/他人序框架；附录→术语表(terms)/参考文献(refs→references 补充)/数据方法论(data/methods)
- 提取后跑硬黑名单（py）确定性删

**P3 · L0 校验 + 落盘（py）**
- schema 校验（字段完整、quotes 逐字、§ 标记已剥）
- 合并 meta + 各章 → `l0-chapter-v1.json` 落 `data/l0/{book}/`
- 生成"全书 L0 总览"（各章 summary 拼接，供第二层面向的全局上下文）
- **过渡兼容**：现 `.蒸馏.txt`（书源目录，下游 `source_context` 读）保留生成，L0 JSON 进独立目录；L0 稳定后精华稿改由 L0 生成

**P4 · 接现有产线**
- `complete_input`/`build_roadmap` 改从 `l0-chapter-v1` 读（替代 `source_context` 直接读全文）
- 第二层面向（facing-kernel/quotes/cases/compliance/units/readers）在 L0 之上各自跑

### 验收标准（P3 后）
- 一本书跑完 L0：meta 完整（出版社/ISBN/目录/作者简介，书内来源非爬虫）+ 每章结构化齐全
- 抽查 1-2 本：quotes 逐字、concepts 带机制、note 中性
- **references**：抽查书内引用的源头书/理论（如锚定效应→《思考快与慢》），L0 references 命中且带 context
- **排版强调**：抽查书中作者框出/加粗段落，L0 对应 quotes 带 `emphasis:true` 且 `§` 已剥
- 补全从 L0 读后，`核心金句` 不再出现"悟空你走吧"式裸句（接 facing-quotes 前先看 L0 原句质量）

