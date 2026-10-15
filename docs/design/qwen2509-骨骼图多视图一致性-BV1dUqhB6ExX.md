# Qwen骨骼图驱动多视图一致性工作流 — BV1dUqhB6ExX 拆解

> 来源: B站 BV1dUqhB6ExX《ComfyUI 12月最新! Lora多角度人物一致性生成》(UP: comfyui大本营, 8:51, 2025-12)
> 学习管线: video-learn skill 1009 第二单 (下载/ASR 86段/24 STRONG操作句/12定点帧VLM)
> 工作流 JSON: 粉丝私信门槛, 无直链 → 本文档 = 字幕+ASR交叉逆向 (VLM节点名仅参考)
> 工作区: `F:\AI-Agent-Local\.tmp\video_learn\BV1dUqhB6ExX\`

## 一句话机制

**1张人物参考图 + 15张姿态骨骼图 + 15行提示词 → for循环15次 Qwen-Edit 2509 → 15张同身份多视图角色表**。

## 三层钉死结构 (与"全部可控"总纲完全同构)

| 层 | 钉法 | 自由度敞口 |
|---|---|---|
| 姿态 | **骨骼图钉死** (每循环1张, 编号切换选) | 模型不自由摆姿势 |
| 身份 | 参考图钉死 (TextEncodeQwenImageEditPlus 槽位) | 脸/服装随参考 |
| 画面 | 提示词行管背景/视角/着装描述 | 仅剩文案层自由 |

→ UP 自评: 15张角色一致性"眼目都是一样的"。姿态可控 = 我们三视图线 (Multiple-Angles LoRA) 之外的**第二条可控多视图路线**, 且粒度更细 (15姿态 vs LoRA固定视角模板)。

## 节点链 (ASR锚, VLM名仅参考)

1. **模型**: Qwen-Image-Edit **2509** (fp8); 本机用 **2511_fp8_fix** 替代 (更新版本)
2. **加速**: **Qwen-Image-Lightning-8steps-V2.0 LoRA** st=1.0 — ⚠️ **本机已有** (`loras/Qwen/`)!
3. **采样**: 采样器必须选**八步版**配套 (Lightning-8steps 配套 8 步 sampler, "不要搞混")
4. **CLIP**: qwen_2.5_vl_7b_fp8_scaled (UP); 本机 4b_fp8 / 3_8b_fp8mixed (2511 专属, 不必换)
5. **文本编码**: TextEncodeQwenImageEditPlus (Plus 变体 = 多图槽位)
6. **提示词源**: 多行文本节点 → **按行加载**, 每循环取第 N 行 (行数=循环数=姿态图数)
7. **行号换算**: Math Expression `a*1` (循环计数 a, 文本无第0行故乘1取行号)
8. **姿态图选择**: 编号切换 (Easy-Use, 15槽, 索引0-14) ← 每循环喂1张骨骼图
9. **for循环**: Easy-Use 包 (initial_value 起始/索引, value1 累积, Batch Any) — 15次
10. **出批拆分**: FromBatchGetImage ×15 (index 0-14)
11. **拼大图**: ImageConcat 4+4+4+3 (单节点最多4入) → 15格 + 原图 = 对比图

## 与我们两条线的映射

| 对照项 | 本工作流 | qwen2511-msr (BV1ZkNe6gErF) | 差异要点 |
|---|---|---|---|
| 批量机制 | for循环+行-图配对 | easy promptLine 拆batch | **UP能行配图, 我们不能** — 多视图刚需行配图 |
| 参考驱动 | 姿态骨骼图 (每行不同) | 3参考图 (全批相同) | 骨骼图=细粒度姿态钉死 |
| 模型 | 2509+Lightning8 | 2511 fp8_fix steps24 | 本机2511可直接套姿态图思路 |
| 一致性来源 | 参考图+骨骼图双锚 | 固定种子+参考潜变量 | 同族机制 |

## 本机缺口表

| 需要 | 本机状态 | 处置 |
|---|---|---|
| Lightning-8steps-V2.0 LoRA | ✅ 已在 loras/Qwen/ | **msr线backlog"Lightning回归"半就位** (8步版, 4步版仍无) |
| 2509 Edit | ⚠️ 有2511 | 用2511, 不降级 |
| easy-use / was-node-suite | ✅ 双包已装 | 无缺口 |
| 姿态骨骼图 | ✅ **大全套在手** (1009用户确认): `G:\下载\comfyui工作流\pose` — 52类/3220张骨骼图+822张男女渲染预览+52份Prompt范例txt; 512×768 | 直接选用; 用前按试验田规矩挑拣入库(出生证明) |
| qwen_2.5_vl_7b CLIP | ✅ `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` 在 | (初判误报, 已修正) |


## 本地已有验证工作流 (1009 用户提示找回)

**`G:\下载\comfyui工作流\jasonbai_Qwen2511_人物一致性.json`** (142节点/10链) — **骨骼图机制早已本地验证**, 且比 B站视频更全:

| 项 | jasonbai (本地) | BV1dUqhB6ExX (B站) |
|---|---|---|
| 骨骼图来源 | **DWPose 现场提取** (yolox_l + dw-ll_ucoco_384, 从源照片抽骨骼) | 姿态库挑图 (3220张) |
| 图槽用法 | image1=人物 / image2=姿势或服装 / image3=场景, **提示词内"图01/图02/图3"引用** | image槽喂骨骼图 |
| 批量 | 10链平铺 (每链一个KSampler) | for循环15次 |
| LoRA栈 | **六连**: Lightning-8steps-V2.0 + boreal 0.85 + studio-realism 0.7 + InSubject 0.7 + MICKMUMPITZ_360 0.9 + next-scene 0.9 | 单Lightning-8 |
| 参数 | 2511_fp8_fix + qwen_2.5_vl_7b CLIP + shift3 + euler/simple + 1920×1080 固定种子 | 2509 + 8步 |
| 产物 | 换景系列 (同人物10场景, 姿势由骨骼/图2钉) | 换姿系列 (同人物15姿态) |

- **另: 自家 Klein 版同机制已验证** — `E:\AI\comfyui_workflows\姿态批量生成_FLUX2-Klein_完整版.json` (236节点): 8视图角色表(走路姿势×8机位), FLUX2-Klein9B+一致性LoRA0.35+换脸增强0.45, **图二=姿势参考照片** (非骨骼图), easy forLoop+imageIndexSwitch 行-图配对+imageConcat 拼格 — 与B站编排同构, 姿态源三态(骨骼图/DWPose抽骨/姿势照片)消融待做
- 本机 loras/Qwen/ 下那批 LoRA (boreal/realism/InSubject/MICKMUMPITZ/next-scene) 就是这套工作流的弹药 — **全部在库**
- **B站视频增量只剩两条**: ①姿态库 (3220张任选, 免DWPose源图) ②for循环行-图配对编排
- prompt 范式可采: "下一个场景：中景镜头，图01的女人穿着图02的X在图3里，[动作]" = 图槽引用+导演层景别前缀, 与 msr 线 promptLine 同构

## 采行结论 (1009)

- **采**: ①姿态骨骼图驱动 = 可控多视图第二路线, 与三视图backlog合流 ②行-图配对 for循环模式 = promptLine 之外的批量编排备选 ③Lightning-8steps 已在手, msr线加速回归可用它先试
- **不采**: 2509 模型 (2511 已覆盖); 从零复刻 for循环编排 (等真需要行配图时再上)
- **缺口全消+机制已验 (1009)**: 姿态库在手 + jasonbai本地工作流早已验证骨骼图机制 → 无复刻门槛, 只有组合题
- 下一步候选: jasonbai骨架 + 姿态库图进image2槽 (替代DWPose单姿态) + msr线导演层行文 = **组合验证单**; 六LoRA栈先消融再上产线
