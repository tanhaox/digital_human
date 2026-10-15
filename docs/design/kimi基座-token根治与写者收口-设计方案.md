# kimi 基座优化 — 死 token 根治与写者收口（设计方案）

> 2026-10-09。前置：外部 kimi 基座审计报告（五实锤点）+ 本仓逐条亲证（全部属实）+ 亲证新发现两条
> （⑥ enhance 覆写器层、单镜路径旁路）。用户令"全圈"——本文覆盖 A/①③/B/C/D/E/F/④/⑤ + 新发现 ⑥⑦。
> 状态：**设计定稿待评审，未动码**。实施走标准流程（逐阶段独立 commit + 回归一集）。

---

## 〇、审计结论（全部亲证，file:line 可点）

### 0.1 kimi 基座事实

| 事实 | 位置 | 备注 |
|---|---|---|
| 三级链 kimi→qwen→deepseek，单请求内自动兜底 | `llm_service.py:488-508` | qwen=token-plan 套餐优先耗，deepseek 末位 |
| pro→k3 / flash→kimi-for-coding-highspeed | `config.py:241` / `anim_pipeline/config.py:37` | |
| **temperature 只收 1**，kimi **和 deepseek** 发送前统一摘除 | `llm_service.py:104-115` | 所有调用点的 0.3/0.5/0.6 对这两家全是死参数；**仅 qwen 兜底时生效**。温度杠杆不存在，只能靠 prompt |
| thinking 已全线关闭 | `llm_service.py:116-120` | kimi/deepseek `thinking:disabled`，qwen `enable_thinking:false` |
| `chat()` 已支持 `max_tokens`/`response_format` | `llm_service.py:218-221` | **导演线 0 个调用点在传**（director2.py 全文无 max_tokens） |
| usage 字段（prompt/completion/cached tokens）被直接丢弃 | `llm_service.py:243-249` | chat 只返回 content——A 项缺口实锤 |

### 0.2 死 token 清单（按量级重排，含亲证新发现）

**⑥（新发现，头号）enhance 两级在 v2 流程里后跑，器层产物整体覆写。**
执行顺序（v2 焦土规划，`anim_service.py:263 _phase_plan` → `planner.py:497 _scenes_flow`）：

```
1. plan_scene 逐场 (pro, DIRECTOR_SYS 11,882字)     → 每镜 keyframe_zh/en + motion_zh/en   ← 死（被3覆写）
2. 术层 fill_creative_slots (47次串行 pro)           → creative_slots                        ← 活
3. 器层 assemble_episode (零LLM确定性)               → 覆写 keyframe_zh/image_prompt/anim    ← 死（被5覆写!）
4. redesign_split_clones + consistency_review (pro)  → 直写 keyframe（④旁路）               ← 半死（被5覆写）
5. anim_service:317 enhance_keyframes                → 47×2=94 次串行 pro                   ← 最终写者，K2/H3 实际吃的
   (enhance1 覆写 keyframe_zh, enhance2 覆写 anim/motion)
```

- 实锤链：`anim_service.py:278/317/343` 三处 enhance 挂点全在术/器（`planner.py:550-554`、`719-720`）**之后**；
  `_enhance_pass1` 跳过条件只有 `brand_card/_enhanced1`（`director2.py:3143-3145`），**不认器层**；
  器层覆写时反而清掉 `_enhanced1/_enhanced2` 旗标（`assembler.py:450-451`）= 给 enhance 让路。
- 量级（ep2=47镜估）：94 次 pro 调用，输入 ~50-70k 字符 + 输出 ~30k 字符/集——**比 ①③ 加起来还大**，
  且是全链最慢 LLM 段（术层 47 次的双倍）。
- 教义级后果：**道法术器的确定性组装在 v2 流程里根本活不到 K2**；④ 的"作者 look 串"风险在 enhance 同样存在
  （enhance1 无 compliance 扫描——巡检 patch 有回滚闸 `director2.py:3042-3052`，enhance 没有）。

**① plan_scene 每镜 keyframe/motion 白写。** 规格书还在催（`director2.py:69` "200-250 字最佳"），
schema 强制四字段（`:319-323`），输出后必然被器层/enhance 覆写。~23-27k 字符/集全死，
且是 plan_scene 1-2min 延迟大头。**注意**：它是术层失败镜的降级兜底（`planner.py:556`
"术层失败不阻断 (keyframe已可用)"，单镜填空失败不断批 `creative_slots.py:183`）——**砍它必须配降级链**（§三）。

**② 术层 47 次串行 k3 调用，参数进 system 位。** `creative_slots.py:113` 逐镜 for 循环，
`:158` 每镜一次 chat；每镜参数（景别/微位/道具/前镜动作）format 进 system（`:139-154`）→
system 每镜都变 → **前缀缓存必不命中**。每次只为 ~60 字 JSON。

**③ DIRECTOR_SYS 11,882 字符**（实测 `wc -m`；审计报 11,185，量级一致），9 个调用点全背：
plan_arcs(`:1410/:1417`) / plan(`:1617`) / plan_shot(`:1708`) / plan_scene(`:2011/:2021`) /
reroll_concept(`:1280`) / redesign_split_clones(`:2771`) / consistency_review(`:2986`)。
其中"画面句丰富度规格/范本×3/容量红线"半章在道法链下是死指令还与新架构矛盾。

**④ 组装后巡逻回写绕过器层（+ 亲证扩大：是四条支路同病）。** 绕过器层模板直写 keyframe 的不止巡检
（`planner.py:573`→`director2.py:2954`，patch 直写 keyframe_zh/en/image_prompt_zh）和克隆重设计
（`planner.py:568`→`director2.py:2713`），还有：

- **⑦（新发现）单镜重生路径完全不过器层**：打回重 roll 单镜走 `anim_service.py:750 replan_shot`
  → `plan_shot`（`director2.py:1708`）LLM 直写 keyframe，后无任何组装/覆写。巡检 patch 后
  "回 planned 待重生"（`planner.py:575`）恰好落回这条路——**patch 完又用老路重生，器层白做**。
- enhance（⑥）同理。

**⑤ episode_gen fix 链声明"只动病灶句"却要求全稿重写。** 压缩 pass（`episode_gen.py:~1224`
"只删不改…直接输出压缩后全稿"）、注入/追加 pass（三处"直接输出…完整稿"）。
行级 patch 协议 `correct_article` 现成（`llm_service.py:350-429`，`{"changes":[[行号,"新行"]]}`，
未动行由代码保证原样）。

---

## 一、设计原则

1. **单一写者原则（本方案的核心新约束）**：`keyframe_zh` 的最终写者全链只有一个。
   目标态：**LLM 只写创意槽（action/scene_detail/atmosphere），器层是唯一组装者，
   enhance（若保留）是唯一抛光者且必须过锚保留闸**。任何新增"直写 keyframe"的路径都是违规。
2. **确定性优先**（0927 用户令）：能用查表/模板/确定性闸解决的，不进 LLM。
3. **降级兜底不裸砍**：每砍一个 LLM 产物，必须同时设计它的降级链（谁顶上、怎么标记、人检在哪看）。
4. **观测先行**：P0 usage 日志不到一天实弹数据，后续所有"收益估算"不转正。

---

## 二、P0：usage 日志（A 项，先行，~0.5 天）

### 改动
`llm_service.py`：
1. `chat()` / `chat_turns()` 加可选参数 `tag: str | None = None`（调用点标签）。
2. `_post_providers()` 返回值附带实际服务的 provider（改返回 `(resp, cfg)` 或等价；三个调用方同步）。
3. 成功解析后打一行日志（现有 logger 即可，无落库）：

```
[llm:usage] tag=creative provider=kimi model=k3 ptok=1234 ctok=56 cached=789
```

4. cached 字段三形状防御式提取（OpenAI 新式 `usage.prompt_tokens_details.cached_tokens` /
   deepseek `prompt_cache_hit_tokens` / qwen 兼容模式字段），取不到记 0。

### 调用点打标（P0 批量打完，~15 处）
`plan_arcs` / `plan_scene` / `plan_shot` / `reroll_concept` / `creative`（术层）/
`clone`（克隆重设计）/ `review`（巡检）/ `enhance1` / `enhance2` / `fa`（法层 shooting_script）/
`epgen:*`（episode_gen 首稿/重试/压缩/注入/hygiene）。

**标签带路径维度**：同一 tag 在整集/单场/单镜不同入口时加后缀（如 `plan_scene:flow` vs `plan_scene:single`）——
一天数据即可验证 ⑦ 单镜旁路流量，给 P4 供弹药。

### 配套
`.tmp/llm_usage_agg.py`：grep 日志 → 按 tag 汇总（次数/ptok/ctok/cached/缓存命中率）→ 分布表。

### 验收
跑一集实弹（规划全流程），聚合表能回答：哪个 tag 吃 token 最多、enhance 两级占比、kimi 命中率、
兜底到 qwen/deepseek 的次数。

---

## 三、P1：plan_scene 瘦身 + DIRECTOR_SYS 拆双版本 + enhance 定位（①③E⑥，~1.5 天）

### 3.1 输出契约瘦身（砍死 token 最大单笔）

`plan_scene`/`plan` 的输出 schema（`director2.py:319-323`）**删四个逐镜字段**：
`keyframe_zh` / `keyframe_en` / `motion_zh` / `motion_en`。保留：
- `arcs.*`：`visual_metaphor` / `visual_world`（registers/world_map 消费，`registers.py:71-99`）
- `groups.keyframe_zh` / `motion_zh`：**场级**场景定调（6 场×~50字，便宜；enhance1/克隆重设计取材用）
- 逐镜：`t_start/t_end/narration/camera/text_layer`

同步删 DIRECTOR_SYS 里催写它们的半章（见 3.2）。**plan_shot 在 P4 之前仍需写 keyframe**（单镜路径是活的），
故其输出契约暂不动（P4 收口后一并退役）。

### 3.2 DIRECTOR_SYS 拆双版本（E）

| 版本 | 体量目标 | 使用者 | 内容 |
|---|---|---|---|
| `DIRECTOR_SYS_STRUCT` | ~4k 字 | plan / plan_scene / plan_arcs / reroll_concept | 结构六步法、景别升级、清单段铁律、黑名单、政治禁区+国家指代块、text_layer 规格、瘦身版输出格式 |
| `DIRECTOR_SYS_SHOT` | ~1.5k 字 | plan_shot / redesign_split_clones / consistency_review（P4 前过渡态） | 单镜三层设计、灭字铁律、运镜军规、单镜输出格式 |

两版共同**删除**：画面句丰富度规格（`:183-197`）、范本×3（`:271-289`）、K2 容量红线章节——
这些职责已归器层/enhance。替换块（`_substitution_block`，`:353`）两版都保留。

### 3.3 降级兜底链（砍 keyframe 的安全网，必须与 3.1 同 commit）

术层填空失败/未对位的镜，从"用 plan_scene 的死 keyframe 顶"改为三级降级：

1. **单镜补填重试 ×2**（flash，便宜）：`fill_creative_slots(only={sid})` 已支持 only 参数。
2. **器层默认槽直装**：`assemble_episode` 放宽 `:423` 的 `if not creative_slots: continue`——
   无槽镜按寄存器默认动作直装（`_REG_TASKS` 的默认语义，`registers.py:55-75`），打标
   `shot["slots_source"]="default"` 供人检/审计。
3. 页面/日志亮灯：默认槽镜数 > 0 时 WARN 汇总（沿 collect_warnings 惯例）。

### 3.4 enhance 定位拍板（⑥，**用户决策点 D1**）

| 方案 | 内容 | 代价/风险 |
|---|---|---|
| **甲（推荐先行）** | enhance 保留为"器层抛光层"：器层是唯一结构写者；enhance1 只在器层句上做名词具象化（现有存活率闸 ≥0.70 保留，`director2.py:3161`），**新增两闸**：① 器层锚保留闸——景别/角度/角色 look/道具片段在抛光后必须逐字存活（扩展现有 `_survival_ratio` 机制到锚词表）；② compliance 扫描——enhance1 产物过 `check_visual`，命中即回滚原句（对齐巡检 patch 的回滚闸 `:3042-3052`） | 94 次 pro 调用的 token 照付；但 ①③ 砍掉后总账仍大降 |
| 乙（后手） | enhance 退役：术层 `scene_detail` 预算从 ≤30 字扩到 ~80 字承担丰富度，器层模板加光影/氛围展开 | 省 94 调用；但 0925 案卷定过"试渲 vs 产线差距主因=增强不足"，两级增强是修好的版本——退役有 K2 丰富度回退风险，**必须 A/B 一集人检后再决** |

推荐路径：**先甲后乙**。甲把"单一写者"语义收口且不动质量机制；乙等 P0 数据
（enhance 的 completion 占比）+ A/B 实证再启动。

### 3.5 验收
- 同集重规划对比：plan_scene 输出字符 ↓60%+，wall time 显著降（预期 1-2min/场 → 明显缩短，以 P0 实测为准）；
- 成片质量不降：K2 score / video_qc 乱码检查 / 人检首帧；
- 术层失败镜走降级链后仍产出合法画面句（构造 1 集故意断术层验证）。

---

## 四、P2：术层批量化 + flash 降档（BC，~1 天）

### 4.1 按场批量（B）

一场（arc）一调用，47 次 → 6-8 次。**缓存友好位**（根治 ②）：

- **system = 固定指令**（`_CREATIVE_SYS` 重构：模板指令与寄存器任务表进 system，逐镜参数全部移出）
- **user = 场数据块**：场空间/光照 + 逐镜行（镜号/微位/景别/角度/道具/口播/前镜动作承接）
- 输出：JSON 数组按序对应逐镜三槽

**动作承接**：批内指令"按序填，每镜 action 承接你刚写的前一镜"（LLM 单次生成内自见前文）；
跨场边界由代码传入上场末镜 action（确定性，取自 doc）。

**失败降级**：批解析失败/缺项 → 该场退回现行逐镜串行（老路径保留为降级模式）；
单槽违规 → 单镜重试一次 → 仍坏走 P1 默认槽。`only` 参数与幂等跳过（`creative_slots.py:117`）原样保留。

### 4.2 flash 降档（C）

批量调用 `model="flash"`（kimi-for-coding-highspeed）。60 字创意填空不需要 k3，
下游已有确定性闸兜底（sanitize_visual_text `:164-172` + 器层容量红线裁剪 `assembler.py:283-289`）。
**A/B 一集**（pro vs flash），人检槽质量 + 违规率对比，用户验收后定档。

### 4.3 验收
- 术层 wall time：47 RTT → 6-8 RTT（预期为全链提速最大单笔之一）；
- A/B 报表：两档的槽质量人检分 + sanitizer 命中率；
- P0 日志确认 qwen 兜底路径的前缀缓存命中（cached > 0）。

---

## 五、P3：max_tokens + response_format（DF，~0.5 天，随 P1/P2 顺带）

### D：max_tokens 注入
`chat()` 已支持（`llm_service.py:220`），逐调用点补：plan_arcs 8000 / plan_scene（瘦身后）6000 /
术层批量 3000 / 巡检 2000 / 克隆重设计 4000 / enhance1 1500 / enhance2 800。
初值拍脑袋，P0 数据校准。长 JSON 截断 = 解析报废 = job 废，这是废稿率问题不是省钱问题。

### F：response_format 试挂
- 挂点：术层批量、巡检、克隆重设计、enhance2（输出 JSON 的调用点）。
- 兼容性证据（审计没注意到）：`_correct_patch` 已在生产用 `{"type":"json_object"}` 走同一条 kimi 链
  （`llm_service.py:405`），风险低于审计估计。
- **注意一个坑**：400 不在 `_FALLBACK_STATUS`（`llm_service.py:89`，"自家 payload 问题不兜底"）——
  若 kimi 拒收 response_format 会直接 raise 而非换厂。挂法照 `correct_article` 的 guard 模式：
  调用侧 try 带参 → 异常降级重试一次不带参。

---

## 六、P4：写者收口——四条旁路统一（④⑥⑦，~2 天，独立立项独立验收）

### 6.1 收口目标态

```
LLM 写槽（术层批量/单镜补填） → 器层组装（唯一结构写者） → enhance 抛光（带锚保留闸，若保留）
     ↑ 巡检 patch 改槽级回流 ←——————————┘
```

非品牌镜的 `keyframe_zh` 必须可重放验证：== `assemble_k2_prompt` 重放结果（或 enhance 抛光后含全部器层锚）。
做成确定性断言（video_qc 前置检查项）。

### 6.2 四条支路改法

| 支路 | 现状 | 改法 |
|---|---|---|
| **巡检 patch** | 直写 keyframe 五字段（`director2.py:3011-3013`） | patches schema 改槽级：`{shot_id, slots:{action?, scene_detail?, atmosphere?}, camera?, reason}`；应用 = 合并槽 → sanitize → `assemble_episode(only={sid})` → 媒体重置回 planned。**修正经器层模板 = 寄存器围栏自动执法（作者 look 串根治）** |
| **克隆重设计** | 一次 LLM 直写全部分身 keyframe（`:2713`） | 分身槽走术层批量（家系/场为批，指令"同场同时刻，每分身推进一拍"）→ `assemble(only=分身集)`。景别推进由 `_expand_fa_shots` 尺寸插值天然保证（`assembler.py:65-137`） |
| **单镜重生（⑦）** | `replan_shot→plan_shot` LLM 直写 keyframe，无器层（`anim_service.py:783`） | **用户决策点 D2（推荐改）**：打回重 roll = 重掷创意槽（`fill_creative_slots(only={sid})` 强制重填）+ `assemble(only={sid})`。重 roll 语义保留（内容必然变化），且围栏全生效。UI 手改 keyframe 的逃生口保留 |
| **enhance** | 后跑覆写器层（⑥） | 见 §3.4 方案甲 |

### 6.3 过渡态
P4 落地前 `DIRECTOR_SYS_SHOT` 服务三条支路；P4 后 plan_shot/克隆/巡检全部槽级化，
`DIRECTOR_SYS_SHOT` 仅剩（若乙方案上马则彻底退役）。

### 6.4 验收
- 审计断言过：全集非品牌镜 keyframe 可重放（或含全锚）；
- 打回重 roll 一镜 → 页面/产物验证走的是"槽重掷+组装"；
- compliance：作者 look 串扫描零命中（对比改造前同集）。

---

## 七、P5：episode_gen fix 链 patch 协议化（⑤，~1 天，条件触发收益）

### 7.1 协议复用 + 扩展
抽 `correct_article` 的行级 patch 协议为公共函数（编号行 + `{"changes":[[行号,"新行"]]}`
+ 代码拼装，`llm_service.py:378-429`），扩展**删行语义**：`新行=""` = 删该行（压缩 pass 需要）。

- 压缩 pass：只输出被删/被改行；
- 注入/追加 pass：输出目标行替换（原行+嵌入句）；
- hygiene（05d）：同。

### 7.2 结构性收益
未动行**由代码保证逐字保留**——段落标签、coverage JSON、⟦⟧原话零漂移从"prompt 自律"变"构造保证"。
现有"总字数变化≤60"从 prompt 约束改为**代码侧验收**（patch 结果超带 → 回退现行全稿路径）。
patch 解析失败 → 现行全稿路径兜底（保功能不断链）。

### 7.3 验收
fix 链触发集的 completion_tokens ↓70%+；稿件 diff 仅病灶行（代码 diff 验证）。

---

## 八、排期总表

| 阶段 | 内容 | 工作日 | 依赖 |
|---|---|---|---|
| P0 | usage 日志 + 聚合脚本 + 打标 | 0.5 | 无（**先行**） |
| P1 | schema 瘦身 + 双版本 + 降级链 + enhance 甲方案闸 | 1.5 | P0 数据校准收益 |
| P2 | 术层批量 + flash A/B | 1 | P1（schema 先瘦） |
| P3 | max_tokens + response_format | 0.5 | 随 P1/P2 顺带 |
| P4 | 写者收口四支路 + 审计断言 | 2 | P1P2（槽级生态先就位） |
| P5 | fix 链 patch 化 | 1 | 独立，可与 P4 并行 |

合计 ~6.5 工作日。每阶段独立 commit、独立可回滚（git revert 单阶段，doc 存储兼容：
砍的只是 LLM 输出契约字段，旧 doc 的 keyframe 字段照读不误）。

**执行纪律**（沿项目惯例）：.py 改动必须重启后端；每阶段收工跑一集回归；
回归用 syscheck 入口不走旁路；不主动 commit，等用户令。

---

## 九、风险与回滚

| 风险 | 缓解 |
|---|---|
| P1 砍 keyframe 后术层大面积失败 → 无首帧 | 三级降级链（§3.3）同 commit；构造断术层集回归 |
| P2 批量后动作承接质量降（批内自承接 vs 逐镜真值传递） | A/B 人检；老串行路径保留为降级模式一 key 切换 |
| flash 槽质量不够 | C 项 A/B 用户验收后定档，不达标留 pro |
| F 的 400 硬炸 | guard 模式（§5），照 correct_article 现行生产用法 |
| P4 单镜器层化后"打回不满意" | 重掷槽=内容必变；UI 手改 keyframe 逃生口保留 |
| enhance 甲方案锚保留闸误杀正常抛光 | 锚词表只取器层注入的结构段（景别/角度/look/道具），存活率阈值先紧后松，A/B 校准 |

---

## 十、拍板记录（2026-10-09 用户令：四点授权自行决策，人检部分出 A/B 后用户选）

| # | 决策 | **裁定** | 依据 |
|---|---|---|---|
| D1 | enhance 定位 | **甲先行**：器层抛光层 + 锚保留闸 + compliance 扫描；乙（退役、术层吸收）挂起，等 P0 实弹 usage 数据 + 甲方案稳定后 A/B 再决 | 0925 案卷定过"试渲 vs 产线差距主因=增强不足"，两级增强是修好的质量机制，不动质量先收口写者语义 |
| D2 | 单镜重生（打回重 roll）是否器层化 | **是**：重掷创意槽 + 器层组装，重 roll 语义保留（内容必变），围栏全生效；UI 手改 keyframe 逃生口保留 | 单一写者原则无例外，否则 ④ 修一漏一 |
| D3 | 术层 flash 定档 | **已定档 flash（1010 用户令"定 flash"）**：A/B 双臂 47/47 质量持平（82s/80s，action 均 14.0/15.9 字），flash 唯一系统短板（照抄英文代号 21 镜 vs pro 0）已被出口闸确定性兜住；默认 model 已改 flash | 用户令"出 a、b 后我选" |
| D4 | P4 后 `DIRECTOR_SYS_SHOT` 退役 | **退役**：P4 落地后 plan_shot/克隆/巡检全部槽级化，SHOT 版无消费者；巡检换 ~600 字专用检查 prompt（从 DIRECTOR_SYS 抽黑名单/政治禁区/双约三段） | D1 走甲后 enhance 用自己的 _ENHANCE*_SYS，SHOT 版零消费 |

---

## 十一、实施记录（2026-10-09 当日，P0-P4 落地，P5 留下轮）

| 阶段 | 状态 | 实测 |
|---|---|---|
| P0 usage 日志 | ✅ | llm_service 加 tag 参数 + provider 回传 + `[llm:usage]` 行（cached 三形状防御）；24 个调用点打标（导演线 11 + 术层 2 + 法层 1 + 拆书线 10）；聚合脚本 `.tmp/llm_usage_agg.py` |
| P1 宪法双版 | ✅ | `DIRECTOR_SYS_STRUCT` 实测 **5,926 字符**（设计估 ~4k，质量块原文复用放宽到 5.9k，仍砍 49%）；plan_scene/plan_arcs/reroll_concept 切换，plan（legacy 单窗无术器下游）保守留全典；特区 scene_tag 兜底提取改 narration |
| P1 schema 瘦身 | ✅ | STRUCT 输出契约删逐镜 keyframe/motion 四字段（明示"输出会被丢弃"）；`_build_doc` 组定调兜底天然兼容 |
| P1 三级降级链 | ✅ | registers.default_slots（七寄存器默认动作）→ assembler 无槽镜直装 + `slots_source=default` 打标 + WARN（有真槽清旧标）→ planner 两流程补填重试 ×2 |
| P1 enhance 两闸 | ✅ | `_anchor_lost`（结构头 + ≥8字 look 括段逐字存活）+ compliance 扫描（pass1 回滚原句 / pass2 motion 拒收） |
| P2 术层批量 | ✅ | 一场一调用，system 全集恒定（`_CREATIVE_BATCH_SYS`）+ 逐镜参数全在 user；对象包数组协议兼容 json_object；批内承接 + 跨场上场末镜传入；失败镜退 `_fill_serial`（老 47 次串行实现保留为降级）；`model` 参数化（默认 pro）+ `dry` 参数（A/B 用） |
| P3 max_tokens | ✅ | 10 处配额（plan_arcs 8000 / plan_scene 6000 / clone 8000 / reroll 4000 / review 3000 / plan_shot 2000 / enhance1 1500 / enhance2 800 / creative 串行 400）；response_format guard 落在批量调用（照 correct_article 生产用法） |
| P4a 巡检槽级化 | ✅ | 有法层 → patches 槽级（slots 三键部分合并 + reason）→ `_accept_slot` 净化 → `assemble_episode(only)` 重组装（围栏自动执法）→ 重组装失败/compliance 命中双回滚（快照含 creative_slots）；board 带 `槽: action·detail·atmo` 行；无法层旧 doc 退老协议 |
| P4b 克隆重设计 | ✅（结构性 no-op） | 器层组装句各镜异（尺寸插值+微位方位）→ `_clone_families` 逐字克隆检测不再命中 → redesign 自然空转（冒烟 8 验证）；老 doc（无槽）仍可触发老路径 |
| P4c 单镜器层化 | ✅ | replan_shot：有法层 → 清旧槽 → `fill(only={sid})` 重掷 + `assemble(only={sid})`（打回语义=重掷创意槽，围栏全生效）；无法层退 plan_shot 老路 |
| P4 可重放断言 | ✅ 1010 落地 | video_qc.writer_audit 三态(纯器层过/抛光锚全过/旁路报); assemble_episode 后自动执法并写 validation.writer_audit |
| P5 fix 链 patch 化 | ✅ 1010 落地 | llm_service.edit_patch 通用行级协议(删行语义+json_object guard); 五 pass(压缩/注入/卫生/CTA/去重) patch 优先+全稿兜底+验收链不动 |

**验证**：`.tmp/smoke_p0p3.py` 11 项进程内冒烟全绿（宪法双版尺寸/默认槽/组装打标/批量块/锚闸双例/usage 三形状/克隆 no-op/槽级巡检合并重组装/compliance 回滚）。

**D3 A/B 实弹（卓有 ep2, 47 镜剥槽克隆双臂）**：A pro(k3) 47/47 · 82s · action 均 14.0 字；B flash(highspeed) 47/47 · 80s · 15.9 字；两臂零空槽、场空间/寄存器语义/动作承接全守住。**逐镜对照表 `.tmp/creative_ab_report.md` 等用户人检定档**——定 flash 后改 `fill_creative_slots` 默认 `model="flash"` 一处即生效。

**生效条件**：全部为 .py 改动——后端重启后生效（重启前按纪律查 running 批）。

## 附：亲证记录（2026-10-09）

审计报告五实锤 + 基座事实逐条对照代码验证通过；新增 ⑥（enhance 覆写器层：
`anim_service.py:278/317/343` + `director2.py:3143` 跳过条件 + `assembler.py:450` 清旗标）
与 ⑦（单镜旁路：`anim_service.py:783` → `plan_shot` 无器层）为本方案新发现，
已并入 P1/P4 范围。测量口径：DIRECTOR_SYS 11,882 字符（`wc -m`，sed 34-352）。
