# 进行中 · 宫格 skill 八组合验证 (20261012 NextScene Master v1.0 冻结, 待八组合)

> **现状 (1012 深夜)**: v2.9 2511五格线被弃 (右脸不可生成+拼板缝合怪) → 用户定向改道**工作流大全原图 NextScene 机制** (FluxToQwenEdit-NextSceneLora_6Scenes) 改指本地 (2509 GGUF Q5_0 + K2 Scene1 + next-scene LoRA 295MB) → **星型拓扑** (军师定调, 全视角只吃Scene1) → LoRA@0.4/cfg1空负向 → 男板六格全净过审 (Baseline_Success_20261012.png)
> **Master v1.0 已冻结**: workflows/headboard/gen_ns_master.py + Qwen-Edit_2509_Master_v1.0.json; 三容错闸(病格换种/拼板assert/组间free); 命名 八组合_{style}_{gender}_{view}_{seed}.png
> **流程契约 (1012 用户定)**: ①Scene1人审定稿→②6宫推演→③服装→④道具。master 已固化 scene1/views/all 三模式
> **今日达成**: 6宫头图确定 (男板 Baseline_Success)。下一步 = 服装图设计令 或 八组合批跑
> 全档: comfyuitext/records/headboard/ 20261012_r5 (链式→星型全弧+NOTE) + 20261012_r6 (Master冻结+NOTE); CHANGELOG 已记 v3.x 条
> r5 硬教训: 链式=叙事用法多角度必须星型 / 坏格先换种子再怪量化 / Bash heredoc 吃反斜杠(
变真换行) / 拼板assert不留白 / Scene1不裸用须链内反推出板格

# 旧档 (v2.9 era, 2511五格线, 已被 NextScene 线取代)

> **现状**: r1 判死 → r2 图指引弧 (v2.0→v2.9) → **real_f 样板五格全过人眼 ✅**
> **v2.9 冻结配方**: K2裸肩基底 → 双通道角度格(<sks>词+素模指引assets/guides/head_guide_v1, left@1.3其余@1.0) → **right=手性往返**(镜像基底→左脸生成→翻回; 镜像世界右上柔光) → i2i0.6 → 五问闸(耳环可见性=非判据, 用户裁定)
> **下一步**: 八组合复测 (4风格×2性别, 冻结配方+机械重掷, 量良率) — 待用户发令
> 全档: comfyuitext/records/headboard/ (r1 判死档 + 20261012_r2v2.0_图指引AB 收官档); 配方史=workflows/headboard/CHANGELOG.md
> 关键教训: 素模图2须与词双通道/LoRA@1.3右脸吸引盆/生成右脸全灭→手性往返/i2i强调什么就重画什么/VLM方向读数全天不可信唯人眼

# 旧档 (20261012 r1 收档)

> 状态: **r1 已收档判死** — 6/8 板生成后用户叫停("一套是错的, 同程序跑100套都是错的"), 0/6 人检合格。
> 全档: `f:\AI-Agent-Local\comfyuitext\records\headboard\20261011_r1v1.0_八组合风格性别泛化\` (NOTE=终判, report.json, state.json, 6板)
> 四病: 耳环病(anchor强制可见)/文本钉朝向弱(2.5D崩)/背面格最弱/闸门失明(不查五官耳环数)
> **下一步定向 (用户提案 1012): v2.0 图指引路线** — image1=身份基底 + image2=角度指引图(自制头模或白底网图, 跨角色复用资产) + 图槽点名; v1.1 锚词补丁降备胎。

> 下一窗口从这里恢复。状态: 两 skill 已落盘待验, **标准名已定 (1011 用户裁定)**:
> - **「12宫」= char-sheet-12grid** — 全身定妆表, 有衣, 12格
> - **「头像板」= headboard-pipeline** — 纯头像五角度, 无衣裸肩, 5格
> ⚠️ 口令必须点名标准名, 模糊的"宫格"默认问清再跑 (1011 已因撞名跑错一次)。

## 1011 跑错回滚记录

- 误跑: gen_8combo.py 按 12宫路线 (有衣), real_f 基底出来是穿衣服全身像, 用户当场打回
- 已停: 批产/监控全杀, ComfyUI 队列已空; 半成品 state 在 .tmp/wf_audit/8combo_state.json (可续可弃)
- 用户已裁定: 八组合跑**头像板**管线 (1011, 跑前另有聊)
- **阵地已迁 comfyuitext 试验田** (1011 用户令, 不再在 .tmp 跑):
  - 线: `comfyuitext/workflows/headboard/` (CHANGELOG + runner gen_8hb.py)
  - 轮: `comfyuitext/records/headboard/20261011_r1v1.0_八组合风格性别泛化/` (板+report+state+run.log+NOTE)
  - real_f 基底已验干净, 断点续跑中

## 测试目标与协议 (1011 用户定调, 本文件北极星)

**两条产线的定位**:
- 「12宫」= 一次性整体定妆 → 产线落点 = 管理类书籍的**作者/主人翁** (拆书知识线)
- 「头像板」(测中) + 「服饰配件版」(未测) → 成功后开**小说方向短剧线** (身份/服装分层, 视频可换装)

**测试方法论 (用户原话级要求)**: 不要"哪不对修哪里, 拼一个对的出来"(改代码式);
要**稳定工作流+稳定出图方法**(程序式: 入口可控, 出口可控)。因此:
1. **配方冻结** — 八组合共用一份配置, 不做逐组合调词; 重掷只有机械规则(固定次数/换种子), 无人工挑图
2. **判据先行** — 先定合格线再跑, 不看结果画线
3. **失败即数据** — 不合格记录在案喂回 skill 坑清单, 不现场打补丁
4. **出口量化** — 一次成型率 / 衣物残留率(经重掷后) / 朝向存疑格数, 逐板报告

## 已完成 (1011 马拉松)

- **skill `headboard-pipeline`**: `.claude/skill/headboard-pipeline/`（头部五角度板一键管线, 脚本+12坑）

- **skill `char-sheet-12grid`**: `.claude/skill/char-sheet-12grid/`（12宫全身定妆, c11 服装格 1-2/2-2 配方待修标注）
- memory: `headboard-pipeline-validated` / `h3-omniref-workflow-deepdive` / `wf-collection-triage-1011`
- 样板: `F:\AI-Agent-Local\.tmp\wf_audit\h3_test_out\PIPE_headboard_v2.png`（头部板, 待终验）
- 12宫 v1-v7 迭代产物同目录（12grid_*.png, 9grid_*.png）

## 下一任务: 八组合宫格测试 (用户令)

**用 skill 生成宫格板, 八个组合**:

| # | 风格 | 性别 |
|---|---|---|
| 1 | 真人写实 | 男 / 女 |
| 2 | 任意平面风格(2D) | 男 / 女 |
| 3 | 2.5D | 男 / 女 |
| 4 | 3D | 男 / 女 |

- 用户人检这 4 风格 × 2 性别的宫格图, 反馈 skill 是否好用
- "9宫格"指宫格板泛称; 版式用 12 宫定稿版（skill 里有）

## 恢复要点

- 管线入口: `headboard_pipeline.py`（头部板）/ 12宫逐格法见 skill
- 换角色/风格 = 改 BASE 词一处; 风格差异在基底词与 style_prompt_tail
- cfg 必须 2.0; angles LoRA 1.3; 基底无衣铁律(头部板); i2i 0.6 除衣; 残留格重掷
- VLM 验侧脸不可信, 人眼终审
- ComfyUI: E:\AI\ComfyUI_windows_portable (8188, run_nvidia_gpu.bat 锚 4090 UUID)
- 2511 栈: qwen_image_edit_2511_fp8_fix + angles LoRA + Lightning-8, 编码 qwen_2.5_vl_7b, VAE 000_qwen_image_vae
