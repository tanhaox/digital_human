# H3 运镜写法规范（定稿 v1 · 2026-10-24）

> 依据：MiniMax-H3 官方 prompt 指南（`skills/h3-prompt-writing/references/base-en.txt`）+ HF 官方讨论 #47 + fuser.studio API 实测 + Wan2GP issue #2279（24 帧抖）+ 本地两轮网格（ab_cam_steps / ab_cam_lang，卓 ep6 s19/s27）。
> 状态：规范条目除标"待验证"外均为外部实证；本轮 ab_camv3 网格验证结果见文末。

## 一、已证伪（勿再试）

| 死路 | 证据 |
|---|---|
| prompt-only 离画布大幅摇 | 两轮网格：官方裸句 / 画外揭示锚 / 视点反向描述，全部被执行成**内容运动**（船开向灯塔式） |
| 堆步数求稳 | 8 步已够（官方 turbo 卡 6–8 档顶格）；10/12 步无感 |
| 链式运镜（pan→tilt→zoom 一镜连招） | 官方 expander 直接拆成多镜硬切 |
| 堆否定句禁抖（"no wobble no shake..."） | 模型把具象词当正向用（同 1012 negation 教训） |

## 二、画布铁律（24 帧周期抖）

- **任一轴 mod 64 ≠ 0**，双轴皆被 64 整除 → 横摇每 24 帧（1s）规律顿跳。
- 16:9 底图禁用 1024×576（双轴 %64=0，踩雷），**用 1024×608**（Wan2GP #2279，okkazoo 24 renders 实测表）。
- 产线节点 136 `scale_to_side longest 1024` + `round_to_multiple 32` 会把 16:9 落到 1024×576 —— 运镜镜需预裁或改参。

## 三、prompt 结构（官方两段式）

```
Part 1 instruction 行:
  I2VA: "For the target video, at 0.00 seconds, Picture 1 is fully referenced."
  FL2VA: "How the reference pictures align with the target video — Picture 1 aligns
          with the 0.00-second mark; Picture 2 aligns with the S.SS-second mark."
Part 2: 主体 → 场景 → 事件 → 镜头 → 声音 (镜头句嵌在事件流里, 非孤立行)
```

### 镜头句四要素（缺一即翻车）
1. **三维句法**：类型 + 幅度 + 速度（medium/normal 可省）：`The camera pans left with large amplitude at slow speed`
2. **嵌动作流**：作从句跟主体动作走（官方 Case 2：`...trucks right with small amplitude at slow speed as she lifts her gaze`）
3. **锚定揭示物**：`...revealing the lighthouse beam into full view`（给模型画外几何的具体目标）
4. **构图收口**：`keeping both figures in frame` / `until the horizon sinks to the lower third`

### 词汇表（勿混用）
| 词 | 语义 |
|---|---|
| pan left/right | 机身不动，镜头水平转 |
| tilt up/down | 机身不动，镜头俯仰 |
| truck left/right | 机身平移（改变视差） |
| zoom in/out | 镜头变焦（无视差，最稳） |
| push in / pull out | 机身前后移 |
| pedestal up/down | 机位升降 |
| 弧线/跟拍 | arc / tracking shot（高失败区，慎用） |

### 幅度控制
- 形容词失控时改**起止构图**：`from the boat centered to the lighthouse filling the left of the frame`
- 大幅配慢速（large amplitude at slow speed）是稳定组合

## 四、离画布大幅运镜 = FL2VA（官方钦定）

- `MiniMaxH3ImageToVideo` 可选入参 **`last_frame`**（本地 ComfyUI 已核）。
- 官方 Case 3 句式：`The camera pulls out with small amplitude at slow speed as she settles into the composition established by Picture 2.`
- **末帧来源（零生图成本）**：1920×1080 高清底图双错位 crop → 首末帧皆真实像素，模型只补中间，视点被两端钉死。
- 帧长落 17k+5 网格（8s=192），节点 134 已处理。

## 五、产线暗坑清单（动运镜镜前先清，h3.py）

| # | 位置 | 原文 | 运镜镜改法 |
|---|---|---|---|
| 1 | ~L254 Physical lock | `strictly no lateral panning ... very slight push-in` | 换运镜许可句（one continuous viewpoint move; camera movement, not subject motion） |
| 2 | opening_desc 尾 | `locked-off camera` | 剥除 |
| 3 | 书级 era lock | `nothing new is added ... same background as the first frame` | 换画外延续句（revealed content continues the same setting/lighting/style） |

## 六、验证记录

- 第一轮 ab_cam_steps（s19，步数×时长网格，8 产）：全为内容运动 → 用户判"无意义"，停。
- 第二轮 ab_cam_lang（s27，三档措辞）：panleft v1 仍内容运动 → 停。
- 第三轮 ab_camv3（本规范落地）：**结果待填**（2 运镜 × {官方从句式, JELSTUDIO 横穿式, FL2VA 末帧锚} × 1024×608）。
