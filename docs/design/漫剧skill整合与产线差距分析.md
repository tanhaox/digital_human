# 漫剧 Skill 整合与产线差距分析

> 2026-09-27 · 起因：外部 4 个分镜 skill（漫剧老李/Toonflow storyboard-pipeline/ai-storyboard-director/script-to-shootable-storyboard）整合反哺。
> 原则：能提高的提高，强的不强行改，取其精华去其糟粕。
> 方法：外部 skill 实物全量阅读（非搜索描述）+ 我方产线逐环节代码审计（file:line 亲证）。

---

## 一、外部四件套画像（实物结论）

| | 定位 | 王牌 | 糟粕（对我们） |
|---|---|---|---|
| short-drama-director 漫剧老李 V6.9.8 | 全流程总控+模型投喂契约 | **C1~C20 提示词机检**（实拍废镜教训库：禁令失效三改法/单主体锚定/语气位/时长守恒）；Asset-First 资产先行 | Seedance 2.5/2.0 投喂契约（我们走本地 ComfyUI）；FACS/打戏/玄幻（赛璐璐知识类用不上） |
| storyboard-pipeline (Toonflow) | 纯编排路由层 | 输入识别→路线→交接契约的思想 | 本体零能力；山音子技能被另两家覆盖；无 LICENSE |
| ai-storyboard-director V5.6.4 | 导演美学引擎 | 两份设计引擎（镜头+摄影语法）、语义反查（只看终稿能否还原导演意图）、设计记忆协议 | 融合六段母稿格式（我们 doc schema 更结构化，不换） |
| script-to-shootable-storyboard | 可拍性验证工程 | 原子镜头规则、40+ Schema 机器验证、修改权限四级（faithful/visual/pacing/story）、失败降级不偷改剧情 | Node 验证器不引入（validate_doc 已覆盖结构闸）；blocking/轴线全体系（我们是插画式硬切架构） |

## 二、我方产线审计结论（8 环节）

| 环节 | 评级 | 证据锚点 |
|---|---|---|
| 分镜规划 | **强** | director2.py:2717 validate_doc 硬校验（景深配额/花字/时间轴断裂/终点对账）+ :2797 软警告 + hook_zone 特区审计 + 隐喻撞车 Jaccard + 世界轮换审计 |
| 时间纪律 | **强（超外部四家）** | duration_audit 三方对账+ffprobe 实测（director2.py:3203）；timeline_violations GPU 前置门（anim_draft.py:523）；beats=槽拉齐（h3.py:204）——外部四家全是估时无实测闭环 |
| 政治合规 | **强（外部四家全无）** | 五层：词库+宪法+软警告+生成闸 auto_fix_banned+K2 PROTECT_TAIL+VLM 产物守门（k2.py:295） |
| 剪映装配 | **强（外部四家全无）** | pyJYD 直挂+补偿审计+孤儿回收+骨架场硬错 |
| 宪法系统 | **强** | DIRECTOR_SYS ~270行三层执法（注入+确定性闸+批评修正轮），"提示词劝不住的落盘前确定性拦" |
| 提示词机检 | **弱** | 拼好的终串只过禁区词替换（anim_service.py:463）；PROTECT_TAIL 全否定式（k2.py:30 "没有地图/没有旗帜"——老李 V6.9.5 实锤：**否定式禁令的词根被图像模型当画面词渲染**）；无主体锚定/长度预算检查 |
| 生成期回喂 | **弱** | K2 违禁只换 seed 重掷 1 次（k2.py:329），不基于失败原因改提示词；H3 失败降级静态图 |
| 巡检覆盖 | **弱** | consistency_review 只在规划期巡文本一轮（planner.py:531）；产物（图/视频）无周期抽检 |
| H3 视频产物机检 | **无** | run_shot 出片后只记 gen_narr_sha 转状态（h3.py:198-243）——全产线最贵环节（~102s+/镜）零画面级验证：不查首帧漂移/文字变形残留/黑屏抖废 |
| 镜间连续性 | **无（半欠缺）** | 无末态→首态衔接、无站位/姿态/视线字段。但架构本质=老李 D 锁方案②"换机位硬切"（每镜换构图是铁律+剪映硬切），真正缺的是 H3 首尾帧模式的"上镜尾帧=下镜末帧"选项和姿态延续注入 |
| 像素级资产复用 | **无（差最后一步）** | 定妆照已生成（character_sheets.py）但注释自认"未来 i2i 一致性底图"未接线；跨镜一致性=文本 look 串+_inject_char_looks 前置（k2.py:171），同角色跨镜仍独立抽卡 |
| 情绪→节奏接线 | **无** | TTS 线有 P5 情绪标注 1-7 档（boost_service/emotions.py:44）**零消费进动画线**；动画线节奏只有槽位 DP 和散文密度规则 |

## 三、差距矩阵 → 反哺清单（按优先级）

### P1-A 提示词终串机检 `prompt_gate.py`（打最痛病根，吸收老李 C19/C20/C18a）
- 病根 1：PROTECT_TAIL 否定式禁令——政治词根（地图/旗帜/国徽/军装/领导人）留在提示词里诱导生成，靠出图后 VLM 兜底=浪费抽卡。**换词法**：高关联可画概念改正向描述+词根零出现；低关联瑕疵（水印/变形）保留否定式（老李同款分级）
- 病根 2：无主体锚定检查——宪法要求可画实体 8-15 个只有提示词约束无代码数；首句无主体→空场景风险
- 病根 3：无长度/token 预算（enhance1 有 ≤700 字软帽，主链无）
- 执法模式：先观察模式（log+字段标记不阻断），存量提示词回测确证命中后升硬闸

### P1-B H3 视频产物机检（我们自己的 VLM 基建平移到视频层）
- 抽 3 帧（首/中/尾）→ glm-4v-flash 查：首帧对比 K2 原图（漂移检测）、文字变形残留、黑屏/花屏
- 产物：video_suspect 字段交人检（复用 text_suspect/qc 接触表模式——产线现成的人检关口）
- 不阻断不自动重掷（视频太贵），只亮灯交人

### P2-A 定妆照接线 i2i（欠缺流程补最后一步）
- character_sheets 定妆照 → K2 出图时作垫图/图生图参考（ComfyUI 已有 LoadImage 通路，同 H3 首帧模式）
- 角色出场镜才挂；look 串文本注入保留双保险

### P2-B TTS 情绪标注接入动画线（已有数据零成本接线）
- P5 情绪强度 1-7 → 每镜炫度/密度微调参数（如强度≥6 的包→H3 运动幅度档+首拍变化提前）
- 先做映射表+离线对照，确证有效再进 director2

### P3 镜间末态衔接（架构增强，非必需）
- H3 build_workflow 支持尾帧输入时：上镜尾帧→下镜 last_frame 选项（衔接锁）
- anim.opening_desc 增加"上镜末态"注入字段
- 仅在用户报告镜间跳切问题时启用

## 四、不做的（明确去其糟粕）

- 不换 doc schema、不引入七段式投喂格式、不装 Seedance 契约（走本地 ComfyUI）
- 不引入 Node 验证器依赖、不做 blocking/轴线全体系（插画式硬切架构用不上）
- 不装 Toonflow（无 LICENSE 且能力被覆盖）
- FACS/打戏 PREVIS/玄幻法术/巨日禄——题材不符

## 五、整合包落地记录（已完成）

- `~/.claude/skills/short-drama-director`（MIT，788K，自带 check_package 全绿）
- `~/.claude/skills/ai-storyboard-director`（Apache-2.0，145K）
- `~/.claude/skills/script-to-shootable-storyboard`（MIT，1.7M，结构检查 passed）
- `~/.claude/skills/storyboard-hub`（新写薄编排层：路由+前置锁+两道验证闸+交接契约）
- `~/.zcode/skills/` 符号链接四条已建
- 参考实物存档 `F:\AI-Agent-Local\TEMP\skill_merge_ref\`（Toonflow 102M 仅备查）
