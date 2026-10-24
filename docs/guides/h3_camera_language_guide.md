# H3 运镜写法规范（定稿 v1.3 · 2026-10-24，含 3~5 轮验证与可控性终判）

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
- 第三轮 ab_camv3（本规范落地，s27，1024×608，8 步 8s，104~120s/镜）：
  - **w1 官方从句式 2/2 全胜**：panleft 左摇正确且干净；tiltup 上摇"几乎无问题"。
  - **w2 JELSTUDIO 横穿式 0/2 双挂**：panleft 变画内动画（镜头没动）；tiltup 变平视→仰视的视角变形。孤立运镜句无动作流锚定 = 模型解析成构图重排。**已证伪，规范不收录。**
  - **fl2v 末帧锚**：panleft 视点右→左穿越达成（两裁切同尺度仍被感知"变焦"，模型脑补副作用）；tiltup 三无（抖/闪/晃）通过，镜头方向未单独点名。
  - **六臂全部无抖动/闪/抖动**——归因三因子：①1024×608 出 24 帧抖雷区 ②运镜配慢速 ③s27 画外内容为海雾天低频纹理（脑补容易）。复杂图（硬几何/建筑/室内）待压力测试。
  - 复盘：本轮执行器曾被中途 /interrupt 打成死锁（CPU 空转/GPU 无载/事件流静默），interrupt+free 无效，重启 ComfyUI 解决——长网格中途停跑需警惕。
- 第四轮 ab_camv4（复杂图压测，ep2 s15 室内硬几何：墙诊断表格栅+人物+工具架抽屉，4 运镜 × 2 臂，101~108s/镜）：
  - **w1 从句式 1/4 干净**：panright 完全合格（持续右转补全画面，空间逻辑正确）；panleft 变环绕+墙遮挡后消失（几何瑕疵）；tiltup 变缓推镜定位右墙画框（方向偏离）+ 画框内容中文乱码；tiltdown 变两次移动（先右移墙再下聚焦）。
  - **fl2v 末帧锚 3/4 到位 / 1 挂**：panleft 合格但用"放大聚焦"实现（变焦作弊）；tiltdown 合格（先上移再顶至底，左右画面被裁）；tiltup 合格（冒出文字卡+定格+回原位+左右裁切）；panright 失败（平移重复了 1 次）。
  - **复杂图结论**：①素材复杂度假设实锤——简单图 2/2 干净，硬几何 1/4 干净；②fl2v 锚的是**终点**不是**路径**（变焦作弊/重复/回弹都是路径自由度）；③模型舒适区仍是 zoom/push 类；④画面含文字格栅 + 运镜 = 乱码风险叠加（tiltup w1 画框乱码、tiltup fl2v 冒文字卡），产线乱码判决回流闸适用。
  - **复杂图运镜建议**：减幅度 / 优先 zoom 类 / fl2v 必须配轨迹 QC（防重复、回弹、变焦作弊）；文字密集镜慎用大幅运镜。
- 第五轮 ab_camv5（可控性种子扫描，3 条件 × +2 seed，96~108s/镜）：
  - **C1 简单图 w1 左摇**：3/3 方向对；幅度 2/3 大幅 + 1/3 缩水画内。
  - **C2 复杂图 w1 右摇**：3/3 方向对（两 seed 自发画外脑补）；1/3 路径突变 pan→orbit（焦点=工具箱，合格）。
  - **C3 复杂图 fl2v 右摇**：3/3 落点对；路径 2/3 干净 + 1/3 重复（原 seed）——**重复未复现=seed 随机，换 seed 可修复**；1/3 轻微主体动作泄漏（抽屉推拉）。
  - **可控性终判**：方向=真控制（9/9）；幅度=偏置（1/3 缩水）；路径形态=掷骰（orbit 突变/重复/变焦作弊/回弹/主体泄漏皆 seed 敏感）；fl2v 锚终点不锚路径。
  - 产线实操含义：运镜失败镜**换 seed 重 roll 即可修复路径缺陷**，不必改方案；要路径确定性只能多 seed + QC 验收。
