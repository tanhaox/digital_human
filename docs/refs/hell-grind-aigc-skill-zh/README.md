# Hell Grind AIGC Skill v2.0.0

一套模型无关的 AIGC 视频生产管理器：将项目搭建、资产与状态、场次空间、镜头契约、图片/视频提示词、生成迭代、失败诊断、选片连续性和交付验收整合到一个 Codex Skill 中。

仓库只公开一个父 Skill：`hell-grind-aigc-skill`。图片提示词与视频提示词能力嵌套在生产工作流内，不需要分别安装。

默认不调用生成模型、不扣费、不下载媒体、不上传、不发布。本地初始化、提示词审计和项目校验均为 **0 个网络请求、0 次数据库操作**。

## v2 解决什么

v1 提供了十阶段骨架和基础提示词结构；v2 将大规模生产研究转化为可执行规则：

- 七层提示词架构：意图 → 资产 → 空间 → 动作 → 摄影 → 视听 → 连续性与风险。
- 图片 11 类信息，每类包含写作公式、常见失败、检查问题和原创短例。
- 视频 12 段镜头契约，包含 open/close state、beat timeline、camera start/path/end、audio cues 和三栏约束。
- 22 个按需加载的方法模块，不把所有知识塞进主入口。
- schema v2 的 14 张项目表，连接资产、场次、镜头、提示词、生成、迭代、选择和豁免。
- 六大失败分类与稳定错误码，优先定位责任层，再做最小修复。
- 本地确定性提示词审计器和只读项目校验器。
- v1 项目兼容模式与 v2 严格模式。

## 核心方法

### 七层提示词架构

| 层 | 解决的问题 |
|---|---|
| L1 意图与验收 | 本图/本镜为什么存在，观众要读到什么 |
| L2 资产与引用 | 同一个谁、当前什么状态、参考继承什么 |
| L3 空间与数量 | 人数、前中后景、屏幕方向、唯一物体 |
| L4 表演与物理 | 触发、重心、接触、反作用和落定 |
| L5 摄影与剪辑 | 起始构图、一个主运动、结束构图和切点 |
| L6 视听质感 | 光源、曝光、颜色、材质、对白和环境声 |
| L7 连续性与风险 | 必须保持、本镜变化、禁止出现、交付规格 |

### 迭代不是盲目抽卡

Skill 将以下对象分开：

```text
prompt version → batch → generation → selection → iteration
```

每次修复记录失败码、责任层、`changed_variables`、`hypothesis` 和 `next_action`。连续两个批次在同一错误上无改善时，回到资产/镜头契约或拆镜，而不是继续复制提示词。

## 安装

要求：macOS/Linux、Git、Python 3.10 或更高版本。没有第三方 Python 依赖。

```bash
git clone https://github.com/renmu2017/Hell-Grind-AIGC-Skill.git
cd Hell-Grind-AIGC-Skill
./scripts/install.sh
```

默认安装到 `${CODEX_HOME}/skills/hell-grind-aigc-skill`；未设置 `CODEX_HOME` 时安装到 `~/.codex/skills/hell-grind-aigc-skill`。重新打开一个 Codex 任务，让 Skill 列表刷新。

更新：

```bash
git pull --ff-only
./scripts/install.sh --update
```

`--update` 会先将旧版本移动到 Skills 根目录内的 `.backups/`，再安装新版本。

## 在 Codex 中使用

### 搭建生产项目

```text
使用 $hell-grind-aigc-skill，把这份短片 brief 搭成 schema v2 项目；先建立资产、场次、空间和镜头契约，不调用生成模型。
```

### 从零写图片提示词

```text
使用 $hell-grind-aigc-skill，为“雨夜维修站门口的疲惫女维修员”写标准版图片提示词，模型未知，保持平台无关。
```

### 润色视频提示词

```text
使用 $hell-grind-aigc-skill 润色下面的视频提示词，保留两名角色、6 秒时长和逐字台词，输出导演版、约束快照、修改摘要和首轮测试建议：……
```

### 诊断生成失败

```text
使用 $hell-grind-aigc-skill 诊断这个镜头的身份漂移、左右反转和运镜失控；先定位责任层，只给最小修复和复测变量。
```

### 审核项目

```text
使用 $hell-grind-aigc-skill 只读审核这个项目的资产版本、镜头表、生成记录、选片和连续性，不生成、不上传。
```

## 本地工具

以下命令都不会访问网络或数据库。

### 初始化 schema v2 项目

```bash
python3 skill/hell-grind-aigc-skill/scripts/init_project.py \
  --name "My AIGC Film" \
  --output /absolute/path/My-AIGC-Film \
  --project-id PRJ-MY-FILM-001 \
  --aspect-ratio 16:9
```

初始化器拒绝覆盖非空目录，并通过临时目录组装后原子移动到目标。

### 校验项目

兼容模式：

```bash
python3 skill/hell-grind-aigc-skill/scripts/validate_project.py \
  /absolute/path/My-AIGC-Film --json
```

严格 v2：

```bash
python3 skill/hell-grind-aigc-skill/scripts/validate_project.py \
  /absolute/path/My-AIGC-Film --strict-v2 --json
```

校验器检查表头、ID、状态、引用、时间线、成本、提示词/生成/选片关系和豁免；保持只读，不观看媒体。

### 审计提示词

```bash
python3 skill/hell-grind-aigc-skill/scripts/audit_prompt.py \
  /absolute/path/prompt.md --medium video --json
```

也支持标准输入：

```bash
printf '%s' '画面中恰好一人……' | \
  python3 skill/hell-grind-aigc-skill/scripts/audit_prompt.py - --medium image
```

审计器检查缺失模块和高置信显式问题，例如静止/运动冲突、多主运镜、时间线越界、引用范围缺失、重复负向约束、平台参数混入和对白视觉化风险。分数只表示结构完整度，不预测生成效果。

## schema v2 目录

```text
00_brief/        项目配置、目标、预算与授权
01_story/        世界规则、角色弧光和视觉/声音母题
02_assets/       资产、参考范围、状态版本
03_scenes/       场次和空间分区
04_shots/        镜头、动作节拍和声音线索
05_prompts/      提示词索引、主提示词和平台适配层
06_generations/  真实调用和迭代假设
07_review/       选片、连续性、质量门和豁免
08_edit/         剪辑、声音、字幕、调色与后期
09_delivery/     技术、权利、归档和发布授权
```

## v1 兼容

- `validate_project.py` 默认读取 `schema_version: 1` 项目并给出迁移 warning，不立即判坏。
- `--strict-v2` 要求 v2 表和规则。
- Skill 不自动改写旧项目；迁移必须显式执行并保留原文件。
- 新初始化项目直接使用 schema v2。

## 方法来源与内容边界

这套方法论受到 Higgsfield 公开项目 [Hell Grind](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind) 的生产结构启发，并结合对公开画布中生成记录、提示词结构、资产组织和连续性方法的归纳。

仓库只发布自行整理的模型无关工作流、模板、脚本和原创短例，不包含原视频、原始资产、全量提示词或能替代原项目的数据集。公开可访问不代表获得再分发许可。

## 许可证与署名

本仓库中由项目所有者创作的代码、文档、模板和示例采用 [MIT License](./LICENSE)：可以自由使用、复制、修改、再分发、商业使用和闭源集成，但复制或分发时必须保留 `Copyright (c) 2026 renmu2017` 和完整 MIT 许可声明。

MIT 的署名义务是保留版权与许可声明，不强制在产品界面或宣传文案中公开鸣谢。许可只适用于项目所有者有权授权的内容，不授予任何第三方原始材料权利。具体边界见 [NOTICE](./NOTICE.md)。
