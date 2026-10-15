# Hell Grind AIGC Skill v2 实施计划

> **执行要求：** 使用 `executing-plans` 按任务顺序内联实施。除非用户明确授权子代理，否则不得启用子代理。每项行为先写失败测试，再写最小实现；完成一个任务后运行指定测试并提交本地 commit。

**目标：** 将现有 `hell-grind-aigc-skill` 升级到 v2.0.0，形成完全独立、模型无关的 AIGC 视频生产操作系统，包含系统化提示词技巧、项目模板、提示词审计器、项目校验器 v2、失败诊断和可验证的安装交付。

**架构：** 保持一个可发现的父 Skill。`SKILL.md` 只负责请求分类、路由、固定输出和安全边界；详细知识放在一层 `references/` 中按需读取。项目事实使用十阶段模板作为单一数据源。`init_project.py` 生成 schema v2 项目，`validate_project.py` 兼容 v1 并支持严格 v2 校验，`audit_prompt.py` 对单条图片或视频提示词做确定性只读审计。所有工具固定 0 网络请求、0 数据库操作。

**技术栈：** Markdown、YAML、CSV、Python 3.10+ 标准库、`unittest`、Bash 安装脚本、Git。

**设计规格：** `docs/superpowers/specs/2026-08-07-hell-grind-aigc-skill-v2-design.md`

---

## 执行约束

1. 工作目录固定为 `/Users/admin/Public/Hell-Grind-AIGC-Skill`。
2. 开始每个任务前运行 `git status --short --branch`，保留所有非本任务改动。
3. 手工编辑使用 `apply_patch`；批量格式化仅限本任务文件。
4. 每个任务先观察测试失败原因与预期一致，再实现到测试通过。
5. 不把 Hell Grind 原始视频、资产、全量提示词、长篇原始提示词或远程媒体 URL 放进仓库。
6. 不调用任何图片或视频生成模型，不上传、不发布媒体。
7. 本地 commit 与 GitHub push 分开验证和报告。
8. 主 `SKILL.md` 不超过 500 行；参考文件只与主文件形成一层关系。
9. Python 脚本只使用标准库，保持 Linux/macOS 可运行。
10. 任何校验和审计命令都不得修改输入项目或提示词文件。

## 目标文件总览

### 修改

- `README.md`
- `skill/hell-grind-aigc-skill/SKILL.md`
- `skill/hell-grind-aigc-skill/agents/openai.yaml`
- `skill/hell-grind-aigc-skill/references/image-prompt-crafting.md`
- `skill/hell-grind-aigc-skill/references/video-prompt-contract.md`
- `skill/hell-grind-aigc-skill/references/production-workflow.md`
- `skill/hell-grind-aigc-skill/references/project-schemas.md`
- `skill/hell-grind-aigc-skill/references/project-qa-gates.md`
- `skill/hell-grind-aigc-skill/references/prompt-preservation.md`
- `skill/hell-grind-aigc-skill/references/prompt-quality-rubric.md`
- `skill/hell-grind-aigc-skill/references/prompt-examples.md`
- `skill/hell-grind-aigc-skill/scripts/init_project.py`
- `skill/hell-grind-aigc-skill/scripts/validate_project.py`
- `skill/hell-grind-aigc-skill/assets/project-template/**`
- `tests/test_skill_contract.py`
- `tests/test_project_tools.py`

### 新增

- `CHANGELOG.md`
- `skill/hell-grind-aigc-skill/VERSION`
- `skill/hell-grind-aigc-skill/references/methodology-evidence.md`
- `skill/hell-grind-aigc-skill/references/prompt-architecture.md`
- `skill/hell-grind-aigc-skill/references/reference-asset-control.md`
- `skill/hell-grind-aigc-skill/references/spatial-blocking.md`
- `skill/hell-grind-aigc-skill/references/camera-editing-language.md`
- `skill/hell-grind-aigc-skill/references/performance-direction.md`
- `skill/hell-grind-aigc-skill/references/action-physics-vfx.md`
- `skill/hell-grind-aigc-skill/references/lighting-color-material.md`
- `skill/hell-grind-aigc-skill/references/dialogue-audio.md`
- `skill/hell-grind-aigc-skill/references/continuity-control.md`
- `skill/hell-grind-aigc-skill/references/negative-constraints.md`
- `skill/hell-grind-aigc-skill/references/multi-shot-sequences.md`
- `skill/hell-grind-aigc-skill/references/iteration-selection.md`
- `skill/hell-grind-aigc-skill/references/failure-diagnosis.md`
- `skill/hell-grind-aigc-skill/scripts/audit_prompt.py`
- `skill/hell-grind-aigc-skill/assets/project-template/02_assets/reference-scope.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/02_assets/asset-state-matrix.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/03_scenes/spatial-map.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/04_shots/beat-sheet.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/04_shots/audio-cues.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/05_prompts/prompt-index.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/06_generations/iteration-log.csv`
- `skill/hell-grind-aigc-skill/assets/project-template/07_review/waivers.csv`
- `tests/test_prompt_audit.py`

---

## Task 1：建立 v2 版本和父 Skill 路由契约

**文件：**

- 修改：`tests/test_skill_contract.py`
- 修改：`skill/hell-grind-aigc-skill/SKILL.md`
- 新增：`skill/hell-grind-aigc-skill/VERSION`

### Step 1：先写失败测试

在 `tests/test_skill_contract.py` 增加：

```python
def test_v2_version_and_router_cover_all_entry_modes(self) -> None:
    self.assertEqual((SKILL_ROOT / "VERSION").read_text().strip(), "2.0.0")
    skill = (SKILL_ROOT / "SKILL.md").read_text()
    for phrase in [
        "生产管理", "图片从零生成", "图片润色扩写",
        "视频从零生成", "视频润色扩写", "失败诊断",
        "精简版", "标准版", "导演版",
    ]:
        self.assertIn(phrase, skill)
```

增加 frontmatter 和规模检查：

```python
def test_skill_frontmatter_is_trigger_focused_and_body_is_compact(self) -> None:
    text = (SKILL_ROOT / "SKILL.md").read_text()
    description = re.search(r"(?m)^description: (.+)$", text).group(1)
    self.assertTrue(description.startswith("Use when"))
    self.assertLessEqual(len(text.splitlines()), 500)
```

### Step 2：运行测试并确认预期失败

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_v2_version_and_router_cover_all_entry_modes -v
```

预期：因 `VERSION` 不存在或路由内容缺失失败。

### Step 3：写最小实现

- 新建 `VERSION`，内容仅为 `2.0.0`。
- 重写 `SKILL.md` 的路由表，明确五类请求和按需参考入口。
- 保留默认不生成、不扣费、不上传、不发布和本地 0 网络/DB 边界。
- 主文件不展开详细技巧。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
wc -l skill/hell-grind-aigc-skill/SKILL.md
```

预期：测试通过，主文件少于 500 行。

### Step 5：提交

```bash
git add tests/test_skill_contract.py skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/VERSION
git commit -m "feat: add v2 skill routing contract"
```

---

## Task 2：建立方法证据、七层架构和约束保护

**文件：**

- 修改：`tests/test_skill_contract.py`
- 新增：`skill/hell-grind-aigc-skill/references/methodology-evidence.md`
- 新增：`skill/hell-grind-aigc-skill/references/prompt-architecture.md`
- 修改：`skill/hell-grind-aigc-skill/references/prompt-preservation.md`
- 修改：`skill/hell-grind-aigc-skill/references/prompt-quality-rubric.md`
- 修改：`skill/hell-grind-aigc-skill/SKILL.md`

### Step 1：先写失败测试

增加 `test_core_methodology_references_define_evidence_and_seven_layers`：

- 两个新文件存在。
- `prompt-architecture.md` 含 `L1` 到 `L7`。
- 含“必须保持”“本镜变化”“禁止出现”。
- 含冲突优先级和平台适配层边界。
- `methodology-evidence.md` 含 115,450、7,482、15.41，并明确规则匹配不是效果因果。
- `SKILL.md` 直接引用两个文件。

### Step 2：运行测试并确认失败

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_core_methodology_references_define_evidence_and_seven_layers -v
```

### Step 3：实现

`methodology-evidence.md` 必须：

- 解释记录数、去重数和生成次数的差异。
- 给出提示词长度、空间、镜头、动作、声音、连续性和负向约束的方向性证据。
- 明确统计只是研究样本的模式命中，不能证明更长或更多生成更好。
- 把证据逐条转换成可执行设计规则。

`prompt-architecture.md` 必须：

- 逐层写目的、必填信息、适用条件、冲突和检查问题。
- 给出“意图 → 资产 → 空间 → 动作 → 摄影 → 视听 → 连续性”的写作顺序。
- 给出信息预算：精简、标准和导演版需要保留什么。
- 给出硬约束冲突优先级。

更新 `prompt-preservation.md` 和 `prompt-quality-rubric.md`，加入约束快照、三栏约束、首轮测试建议和结构完整度而非效果预测的说明。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
rg -n '^### L[1-7] ' skill/hell-grind-aigc-skill/references/prompt-architecture.md
```

### Step 5：提交

```bash
git add tests/test_skill_contract.py skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/references/methodology-evidence.md skill/hell-grind-aigc-skill/references/prompt-architecture.md skill/hell-grind-aigc-skill/references/prompt-preservation.md skill/hell-grind-aigc-skill/references/prompt-quality-rubric.md
git commit -m "docs: define evidence-backed prompt architecture"
```

---

## Task 3：系统化图片、资产和空间提示词技巧

**文件：**

- 修改：`tests/test_skill_contract.py`
- 修改：`skill/hell-grind-aigc-skill/references/image-prompt-crafting.md`
- 新增：`skill/hell-grind-aigc-skill/references/reference-asset-control.md`
- 新增：`skill/hell-grind-aigc-skill/references/spatial-blocking.md`
- 修改：`skill/hell-grind-aigc-skill/SKILL.md`

### Step 1：先写失败测试

增加三个测试：

1. 图片文档仍严格包含 11 个编号主项。
2. 每个主项都有“写作公式、常见失败、检查问题、短例”四类内容。
3. 资产和空间文档覆盖：
   - 稳定身份与状态版本分离。
   - 多视图资产表而非假设所有素材是真三视图。
   - `inherit / exclude` 引用范围。
   - 解剖左右、屏幕左右、前中后景、轴线、唯一物体和精确人数。

### Step 2：确认测试失败

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_image_asset_and_spatial_guides_are_actionable -v
```

### Step 3：实现图片 11 类完整技巧

每一类都写：

- 该类解决的问题。
- 何时必须写、何时应省略。
- 模型无关公式。
- 失败症状和最小修复。
- 一个不超过数行的原创例子。

资产控制文档增加：

- 资产种类和稳定 ID。
- identity invariants 与 state variables。
- 角色/怪物的正侧背、面部、材质、比例、动作范围。
- 道具的结构、握持、接触与唯一性。
- 场景的空间层级、出入口、尺度和光源。
- 参考图权利和批准状态。

空间文档增加镜头轴线、屏幕方向、解剖左右的决策表和多人镜头检查表。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
python3 - <<'PY'
import re
from pathlib import Path
p = Path('skill/hell-grind-aigc-skill/references/image-prompt-crafting.md')
assert len(re.findall(r'(?m)^\d+\. ', p.read_text())) == 11
PY
```

### Step 5：提交

```bash
git add tests/test_skill_contract.py skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/references/image-prompt-crafting.md skill/hell-grind-aigc-skill/references/reference-asset-control.md skill/hell-grind-aigc-skill/references/spatial-blocking.md
git commit -m "docs: expand image asset and spatial prompting"
```

---

## Task 4：系统化视频、摄影、表演和动作物理技巧

**文件：**

- 修改：`tests/test_skill_contract.py`
- 修改：`skill/hell-grind-aigc-skill/references/video-prompt-contract.md`
- 新增：`skill/hell-grind-aigc-skill/references/camera-editing-language.md`
- 新增：`skill/hell-grind-aigc-skill/references/performance-direction.md`
- 新增：`skill/hell-grind-aigc-skill/references/action-physics-vfx.md`
- 修改：`skill/hell-grind-aigc-skill/SKILL.md`

### Step 1：先写失败测试

测试视频主文档仍严格包含 12 个编号主项，并覆盖：

```text
open_state
beat_timeline
camera_start
camera_path
camera_end
close_state
continuity_in
continuity_out
must_hold
changes_here
must_not_appear
audio_cues
risk_focus
```

专项文件测试覆盖：

- 起始构图、主运动、稳定方式、对焦和结束构图。
- 焦段视觉效果而不是只列毫米数。
- 情绪转译为视线、呼吸、停顿、面部和姿态。
- 动作公式 `准备 → 发力 → 接触 → 反作用 → 落定`。
- 投掷、撞击、打斗、爆炸和粒子反馈。
- 一个镜头一个主运镜与时长预算。

### Step 2：确认测试失败

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_video_camera_performance_and_action_guides_are_actionable -v
```

### Step 3：实现

`video-prompt-contract.md` 为每段增加：目的、输入字段、写法、冲突和验收。

`camera-editing-language.md` 至少包含：

- 景别、机位高度、视角和透视。
- 摄影机起点/路径/落点语法。
- handheld、locked、dolly、pan、tilt、orbit、crane 的使用边界。
- 自然手持和故障抖动的区别。
- 单镜与多镜的切点表达。

`performance-direction.md` 至少包含：

- 可见表演词典。
- 反应前微停顿、视线来源、呼吸和身体重心。
- 主演与背景角色不同优先级。
- “静止”角色允许和禁止的微动作。

`action-physics-vfx.md` 至少包含：

- 因果链、接触、重量、惯性、反作用和停止状态。
- 环境受力、衣料、液体、灰尘、碎片和光照反馈。
- 复杂动作拆镜与时长降级策略。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
```

### Step 5：提交

```bash
git add tests/test_skill_contract.py skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/references/video-prompt-contract.md skill/hell-grind-aigc-skill/references/camera-editing-language.md skill/hell-grind-aigc-skill/references/performance-direction.md skill/hell-grind-aigc-skill/references/action-physics-vfx.md
git commit -m "docs: expand video camera and action prompting"
```

---

## Task 5：补齐光色材质、声音、连续性、负向约束和多镜头

**文件：**

- 修改：`tests/test_skill_contract.py`
- 新增：`skill/hell-grind-aigc-skill/references/lighting-color-material.md`
- 新增：`skill/hell-grind-aigc-skill/references/dialogue-audio.md`
- 新增：`skill/hell-grind-aigc-skill/references/continuity-control.md`
- 新增：`skill/hell-grind-aigc-skill/references/negative-constraints.md`
- 新增：`skill/hell-grind-aigc-skill/references/multi-shot-sequences.md`
- 修改：`skill/hell-grind-aigc-skill/SKILL.md`

### Step 1：先写失败测试

验证五个文件可从主 Skill 直接到达，并分别含有：

- 光源、方向、软硬、对比、曝光保护、60:30:10 的适用边界、材质光照响应。
- 逐字对白、发声时间、静默尾拍、对白不视觉化、环境底床、拟音、混音优先级。
- 身份、状态、空间、轴线、动作、光线、天气和声音连续性。
- 风险驱动负面约束、去重、正向契约优先和冲突压缩。
- 多镜头数量、每段时长、切点、接点、蒙太奇规则和最终落点。

### Step 2：确认失败

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_sensory_continuity_negative_and_multishot_guides_exist -v
```

### Step 3：实现

文档以决策表、检查表和“症状 → 原因 → 最小修复”为主要形式。特别约束：

- 60:30:10 是可选组织法，不是固定底座。
- 8K/IMAX 是视觉目标时需与真实输出规格分开。
- 对白中的过去事件、地点或人物默认只是声音内容，除非镜头明确要求视觉化。
- 负向约束不重复“不要”同义句，不用全局禁词替代清晰正向描述。
- 多镜头总时长必须等于各段时长与转场预算之和。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
```

### Step 5：提交

```bash
git add tests/test_skill_contract.py skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/references/lighting-color-material.md skill/hell-grind-aigc-skill/references/dialogue-audio.md skill/hell-grind-aigc-skill/references/continuity-control.md skill/hell-grind-aigc-skill/references/negative-constraints.md skill/hell-grind-aigc-skill/references/multi-shot-sequences.md
git commit -m "docs: add sensory continuity and sequence guidance"
```

---

## Task 6：建立迭代、失败诊断和生产质量门

**文件：**

- 修改：`tests/test_skill_contract.py`
- 新增：`skill/hell-grind-aigc-skill/references/iteration-selection.md`
- 新增：`skill/hell-grind-aigc-skill/references/failure-diagnosis.md`
- 修改：`skill/hell-grind-aigc-skill/references/production-workflow.md`
- 修改：`skill/hell-grind-aigc-skill/references/project-schemas.md`
- 修改：`skill/hell-grind-aigc-skill/references/project-qa-gates.md`
- 修改：`skill/hell-grind-aigc-skill/references/prompt-examples.md`
- 修改：`skill/hell-grind-aigc-skill/SKILL.md`

### Step 1：先写失败测试

增加：

- 六组失败分类和设计规格中的 `F-*` 错误码存在。
- 文档明确责任层：资产、镜头契约、提示词、平台适配、生成随机性、后期。
- 迭代记录必须含 batch、changed variables、hypothesis、decision 和 next action。
- 停止条件覆盖通过阈值、连续无改善、预算上限、能力边界和替代修复成本。
- 十阶段都有进入条件、核心产物、退出门和返工去向。
- `prompt-examples.md` 不出现远程 URL、Hell Grind 专有角色或超长示例。

### Step 2：确认失败

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_iteration_failure_and_production_guides_are_complete -v
```

### Step 3：实现

`failure-diagnosis.md` 为每个错误码写：

```text
症状
先检查
责任层
最小修复
只改变的复测变量
不建议做法
```

`iteration-selection.md` 明确批次内参数不变、批次间版本化、候选比较维度和停止条件。

生产文档把十阶段数据流与返工路径连接起来。示例文件增加十类镜头中的少量原创短例，但任何单例不得演变为可替代原始语料的长提示词。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
! rg -n 'https?://|Hell Grind' skill/hell-grind-aigc-skill/references/prompt-examples.md
```

### Step 5：提交

```bash
git add tests/test_skill_contract.py skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/references/iteration-selection.md skill/hell-grind-aigc-skill/references/failure-diagnosis.md skill/hell-grind-aigc-skill/references/production-workflow.md skill/hell-grind-aigc-skill/references/project-schemas.md skill/hell-grind-aigc-skill/references/project-qa-gates.md skill/hell-grind-aigc-skill/references/prompt-examples.md
git commit -m "docs: add iteration diagnosis and production gates"
```

---

## Task 7：扩展 schema v2 项目模板

**文件：**

- 修改：`tests/test_project_tools.py`
- 修改：`skill/hell-grind-aigc-skill/scripts/validate_project.py`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/00_brief/project.yaml`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/02_assets/assets.csv`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/03_scenes/scenes.csv`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/04_shots/shots.csv`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/05_prompts/prompt-template.md`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/06_generations/generation-log.csv`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/07_review/continuity-matrix.csv`
- 修改：`skill/hell-grind-aigc-skill/assets/project-template/07_review/selection-log.csv`
- 新增：本设计规格列出的八个 CSV 模板。

### Step 1：先写失败测试

扩展 `test_init_creates_full_project_that_passes_validation` 之前，先增加一个只检查模板输出的测试：

```python
def test_init_creates_v2_single_sources_of_truth(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        project = self.init_project(Path(temporary))
        self.assertIn("schema_version: 2", (project / "00_brief/project.yaml").read_text())
        for relative in [
            "02_assets/reference-scope.csv",
            "02_assets/asset-state-matrix.csv",
            "03_scenes/spatial-map.csv",
            "04_shots/beat-sheet.csv",
            "04_shots/audio-cues.csv",
            "05_prompts/prompt-index.csv",
            "06_generations/iteration-log.csv",
            "07_review/waivers.csv",
        ]:
            self.assertTrue((project / relative).is_file(), relative)
```

再逐一断言表头与设计规格一致。

### Step 2：确认失败

```bash
python3 -m unittest tests.test_project_tools.ProjectToolsTests.test_init_creates_v2_single_sources_of_truth -v
```

### Step 3：实现模板

- 将 `schema_version` 改为 2。
- 使用 v2 资产 ID 约定，同时在文档保留 v1 ID 的兼容说明。
- `shots.csv` 增加首尾状态、连续性输入输出和三栏约束字段。
- `generation-log.csv` 使用 `prompt_id` 引用具体提示词版本，不只存松散 `prompt_version`。
- 新增八个表，表头严格使用设计规格字段。
- `prompt-template.md` 展开七层与视频 12 段契约，但保留平台适配层独立。
- 同步把 `validate_project.py` 的基础 schema 和计数表扩展到 v2 文件，使刚初始化的空 v2 项目能够通过当前结构校验；本任务只做表头和空表兼容，详细引用、状态和业务规则留给 Task 11–12。

### Step 4：验证模板和现有回归测试

```bash
python3 -m unittest tests.test_project_tools -v
```

预期：新模板测试和现有项目工具测试全部通过。不得提交已知红灯。

### Step 5：提交

```bash
git add tests/test_project_tools.py skill/hell-grind-aigc-skill/assets/project-template skill/hell-grind-aigc-skill/scripts/validate_project.py
git commit -m "feat: add v2 AIGC project templates"
```

---

## Task 8：升级初始化器并保持安全覆盖边界

**文件：**

- 修改：`tests/test_project_tools.py`
- 修改：`skill/hell-grind-aigc-skill/scripts/init_project.py`

### Step 1：先写失败测试

增加：

- 默认项目 ID 符合 v2 规则。
- 用户传入无效画幅、空画幅或无效项目 ID 时拒绝。
- 初始化输出报告 `schema_version: 2`、0 网络和 0 DB。
- 占用目标目录保持不变。
- 初始化过程异常时不会留下半成品目标。

### Step 2：确认失败

```bash
python3 -m unittest tests.test_project_tools.ProjectToolsTests.test_initializer_rejects_invalid_v2_inputs -v
```

### Step 3：实现

- 保留临时目录复制后原子 rename 的方式。
- 增加画幅格式验证，例如 `16:9`、`9:16`、`1:1`、`2.39:1`。
- 输出中加入 schema 版本。
- 不增加网络、包管理器或第三方 YAML 依赖。

### Step 4：验证

```bash
python3 -m unittest tests.test_project_tools -v
```

### Step 5：提交

```bash
git add tests/test_project_tools.py skill/hell-grind-aigc-skill/scripts/init_project.py
git commit -m "feat: initialize safe v2 AIGC projects"
```

---

## Task 9：实现提示词审计器基础结构和缺失检查

**文件：**

- 新增：`tests/test_prompt_audit.py`
- 新增：`skill/hell-grind-aigc-skill/scripts/audit_prompt.py`

### Step 1：先写失败测试

测试 CLI：

1. `--medium image|video` 必填并拒绝其他值。
2. 同时支持文件和 `-` 标准输入。
3. `--json` 输出包含：

```json
{
  "valid_for_review": false,
  "score": 0,
  "issues": [],
  "detected_modules": [],
  "assumptions": [],
  "network_requests": 0,
  "database_operations": 0
}
```

4. 空文本产生 `P-EMPTY`。
5. 图片缺主体产生 `P-MISSING-SUBJECT`。
6. 视频缺时长、摄影机结束状态和声音边界分别产生稳定错误码。
7. 输入文件哈希在审计前后不变。

使用 `subprocess.run` 调 CLI，不直接只测内部函数。

### Step 2：运行测试并确认脚本缺失失败

```bash
python3 -m unittest tests.test_prompt_audit -v
```

### Step 3：实现最小审计器

建议内部结构：

```python
@dataclass(frozen=True)
class PromptIssue:
    code: str
    severity: str
    line: int | None
    message: str
    suggestion: str

def audit_prompt(text: str, medium: str) -> dict[str, Any]: ...
def read_input(path: str) -> str: ...
def parse_args() -> argparse.Namespace: ...
```

第一阶段只实现空文本、主体、数量、时长、摄影机首尾、声音、平台分层和三栏约束的缺失检测。规则必须可解释，issue 必须带最小修复建议。

评分使用显式扣分表，范围限制在 0–100。`valid_for_review` 以是否存在 error 为准，不把分数包装成模型成功概率。

### Step 4：验证

```bash
python3 -m unittest tests.test_prompt_audit -v
python3 skill/hell-grind-aigc-skill/scripts/audit_prompt.py --help
```

### Step 5：提交

```bash
git add tests/test_prompt_audit.py skill/hell-grind-aigc-skill/scripts/audit_prompt.py
git commit -m "feat: add deterministic prompt auditor"
```

---

## Task 10：补齐提示词冲突、时间线和风险审计

**文件：**

- 修改：`tests/test_prompt_audit.py`
- 修改：`skill/hell-grind-aigc-skill/scripts/audit_prompt.py`

### Step 1：逐组写失败测试

每组单独红绿：

1. “全程完全静止”与“持续奔跑”触发 `P-CONFLICT-MOTION`。
2. 同一镜头同时要求“锁定机位、环绕、快速推近”触发 `P-CAMERA-CONFLICT`。
3. 明确秒数节拍超过总时长触发 `P-TIMELINE-OVERFLOW`。
4. 引用存在但只写“参考这张图”触发 `P-REFERENCE-SCOPE`。
5. 重复或同义负面项触发 `P-NEGATIVE-DUPLICATE` warning。
6. 主提示词混入 seed、steps、CFG、采样器触发 `P-PLATFORM-MIXED`。
7. 有逐字对白但未声明仅声音、不视觉化时触发 `P-DIALOGUE-VISUALIZATION` warning。
8. 只有“高级、电影感、震撼”等抽象形容词触发 `P-ABSTRACT-ONLY`。
9. 完整原创图片和视频样例 `valid_for_review` 为 true。

### Step 2：逐组确认失败并实现

每实现一组运行：

```bash
python3 -m unittest tests.test_prompt_audit -v
```

实现原则：

- 正则只匹配高置信度显式冲突。
- 不把自然语言审计伪装成完整语义理解。
- warning 不导致 `valid_for_review` 失败，error 才导致失败。
- issue 按严重级别、行号、错误码稳定排序。
- 重复负面项先标准化空白、标点和常见中英文否定前缀。

### Step 3：验证文本/JSON 一致性和只读性

```bash
python3 -m unittest tests.test_prompt_audit -v
```

### Step 4：提交

```bash
git add tests/test_prompt_audit.py skill/hell-grind-aigc-skill/scripts/audit_prompt.py
git commit -m "feat: detect prompt conflicts and overload"
```

---

## Task 11：建立项目校验器 v1 兼容与 v2 严格模式

**文件：**

- 修改：`tests/test_project_tools.py`
- 修改：`skill/hell-grind-aigc-skill/scripts/validate_project.py`

### Step 1：先写失败测试

新增测试构造一个最小 v1 项目：

- 默认模式 `valid: true`，v2 缺失项产生 warning。
- `--strict-v2` 将 v2 缺失项提升为 error 并返回 1。
- 新建的 v2 项目在 `--strict-v2 --json` 下通过。
- JSON issue 增加 `severity`，但保留原来的 `code/path/message` 字段。
- 输出增加 `error_count` 和 `warning_count`。

### Step 2：确认失败

```bash
python3 -m unittest tests.test_project_tools.ProjectToolsTests.test_v1_compatibility_and_strict_v2_modes -v
```

### Step 3：实现 schema 分派

建议：

```python
def parse_schema_version(config: dict[str, str]) -> int: ...
def issue(code: str, path: str, message: str, severity: str = "error") -> dict[str, str]: ...
def required_schemas(schema_version: int) -> dict[str, list[str]]: ...
```

- schema 1 接受 v1 的 `CHR/CRT/PROP/LOC/VFX` ID。
- schema 2 使用 `AST-CHAR/AST-CREA/AST-PROP/AST-LOC/AST-VFX`。
- 兼容模式只对 v1 缺少 v2 表发 warning。
- 严格模式按 v2 规则判定。
- `valid` 只取决于 error 数量。

### Step 4：验证完整项目工具测试

```bash
python3 -m unittest tests.test_project_tools -v
```

### Step 5：提交

```bash
git add tests/test_project_tools.py skill/hell-grind-aigc-skill/scripts/validate_project.py
git commit -m "feat: validate v1 and strict v2 projects"
```

---

## Task 12：实现 v2 引用完整性和状态规则

**文件：**

- 修改：`tests/test_project_tools.py`
- 修改：`skill/hell-grind-aigc-skill/scripts/validate_project.py`

### Step 1：先写引用失败测试

为以下情况各写一个最小测试或表驱动子测试：

- `reference-scope.csv` 引用不存在资产。
- `asset-state-matrix.csv` 引用不存在资产。
- `spatial-map.csv` 引用不存在场次。
- beat/audio cue 引用不存在镜头。
- generation 引用不存在 prompt ID。
- selection 引用不存在 generation。
- continuity/waiver 引用不存在镜头。
- selected generation 与 selection log 不一致。

预期错误码分别稳定为 `MISSING_REFERENCE` 或更具体的 `SELECTION_MISMATCH`。

### Step 2：确认失败并实现通用引用检查

扩展 `CSV_SCHEMAS`，使用通用：

```python
def require_references(
    relative: str,
    rows: list[dict[str, str]],
    column: str,
    valid: set[str],
    issues: list[dict[str, str]],
    *,
    split: str | None = None,
) -> None: ...
```

多值资产字段以分号拆分。不要把空字段当悬空引用。

### Step 3：先写状态和数值失败测试

覆盖：

- 非法资产/镜头/提示词状态。
- 重复 shot order。
- 非正镜头时长。
- beat 开始晚于结束或超过镜头时长。
- 负成本。
- `generated` 没有生成记录。
- `selected/locked` 没有选择记录。
- iteration 没有 `changed_variables` 或 hypothesis。
- locked 镜头仍有 error 级连续性问题且无 waiver。

### Step 4：实现业务规则

规则必须集中在命名清晰的小函数中，不在 `validate_project` 堆成一个超长分支。推荐：

```python
validate_statuses(...)
validate_positive_numbers(...)
validate_unique_order(...)
validate_timeline_bounds(...)
validate_state_requirements(...)
validate_selection_consistency(...)
validate_iteration_records(...)
```

### Step 5：验证只读性和全部项目测试

```bash
python3 -m unittest tests.test_project_tools -v
```

现有 `tree_digest` 测试必须继续通过。

### Step 6：提交

```bash
git add tests/test_project_tools.py skill/hell-grind-aigc-skill/scripts/validate_project.py
git commit -m "feat: enforce v2 project integrity rules"
```

---

## Task 13：补齐行为契约和打包边界测试

**文件：**

- 修改：`tests/test_skill_contract.py`
- 可新增：`tests/test_behavior_contract.py`

### Step 1：先写失败测试

静态和场景契约覆盖：

1. 所有 `SKILL.md` 中的 `references/*.md` 路径存在。
2. 所有预期参考都从主文件直接可到达。
3. Skill 包内没有其他 `SKILL.md`。
4. Skill 包不包含 `.mp4/.mov/.png/.jpg/.jsonl` 或远程媒体 URL。
5. 图片简单请求路由到精简/标准规则，不强制导演版。
6. 润色任务明确保护人数、时长和逐字台词。
7. 身份漂移诊断先查资产版本和引用范围。
8. “更电影感”需转译成摄影、光色、材质或声音选择，不推荐仅堆导演名。
9. 提示词任务与生成授权分开。
10. 本地三个工具均声明 0 网络和 0 DB。

### Step 2：确认预期失败并补齐路由文字

只修正真正缺失的 Skill 路由或参考内容，不为了匹配测试机械复制句子。

### Step 3：运行全部契约测试

```bash
python3 -m unittest tests.test_skill_contract -v
python3 -m unittest tests.test_behavior_contract -v
```

若没有单独创建 `test_behavior_contract.py`，第二条命令省略。

### Step 4：提交

```bash
git add tests skill/hell-grind-aigc-skill/SKILL.md skill/hell-grind-aigc-skill/references
git commit -m "test: verify v2 skill behavior and package boundaries"
```

---

## Task 14：更新 README、元数据和版本说明

**文件：**

- 修改：`README.md`
- 新增：`CHANGELOG.md`
- 修改：`skill/hell-grind-aigc-skill/agents/openai.yaml`
- 如有必要修改：`NOTICE.md`
- 修改：`tests/test_skill_contract.py`

### Step 1：先写失败测试

验证：

- README 写明 v2.0.0、七层架构、三个工具、严格校验、v1 兼容和模型无关边界。
- README 的所有命令路径存在。
- CHANGELOG 有 `2.0.0 - 2026-08-07`。
- 元数据名称、短描述和默认提示词能够触发管理、提示词和诊断能力。
- MIT 署名说明继续保留。

### Step 2：确认失败

```bash
python3 -m unittest tests.test_skill_contract -v
```

### Step 3：更新文档

README 至少提供：

- 一段 v2 简介。
- 生产搭建、图片提示词、视频提示词、失败诊断和项目审核五个调用示例。
- `init_project.py`、`validate_project.py --strict-v2`、`audit_prompt.py` 三个命令。
- v1 升级和兼容模式说明。
- 默认 0 生成调用、0 网络、0 数据库说明。
- 内容边界、第三方材料边界和 MIT 保留版权声明义务。

CHANGELOG 不写未实现功能。

### Step 4：验证

```bash
python3 -m unittest tests.test_skill_contract -v
```

### Step 5：提交

```bash
git add README.md CHANGELOG.md NOTICE.md skill/hell-grind-aigc-skill/agents/openai.yaml tests/test_skill_contract.py
git commit -m "docs: publish Hell Grind AIGC Skill v2 guidance"
```

若 `NOTICE.md` 没有变化，不要把它加入提交。

---

## Task 15：全量验证、临时安装验证与本机更新

**文件：**

- 不应新增功能文件。
- 只有验证发现真实缺陷时，按 TDD 回到相应任务文件修复。

### Step 1：运行全量单元测试

```bash
python3 -m unittest discover -s tests -v
```

预期：全部通过，且输出显示非零测试数。

### Step 2：运行格式和包边界检查

```bash
git diff --check
python3 -m py_compile skill/hell-grind-aigc-skill/scripts/init_project.py skill/hell-grind-aigc-skill/scripts/validate_project.py skill/hell-grind-aigc-skill/scripts/audit_prompt.py
find skill/hell-grind-aigc-skill -type f | sort
```

### Step 3：运行官方 Skill 快速验证

先定位当前系统 `skill-creator` 的 `quick_validate.py`，然后运行：

```bash
python3 /absolute/path/to/skill-creator/scripts/quick_validate.py skill/hell-grind-aigc-skill
```

预期：Skill 结构、frontmatter 和名称验证通过。

### Step 4：临时安装与文件一致性验证

使用 `mktemp -d` 建立临时 `CODEX_HOME`，不得覆盖真实安装：

```bash
test_codex_root="$(mktemp -d)"
CODEX_HOME="$test_codex_root" ./scripts/install.sh
diff -qr skill/hell-grind-aigc-skill "$test_codex_root/skills/hell-grind-aigc-skill"
```

验证后只移除刚创建且路径已解析确认的临时目录。

### Step 5：端到端项目验证

在另一个明确的临时目录中：

```bash
python3 skill/hell-grind-aigc-skill/scripts/init_project.py \
  --name "V2 Acceptance" \
  --output /absolute/tmp/path/v2-acceptance \
  --project-id PRJ-V2-ACCEPTANCE-001 \
  --aspect-ratio 16:9

python3 skill/hell-grind-aigc-skill/scripts/validate_project.py \
  /absolute/tmp/path/v2-acceptance --strict-v2 --json
```

再对一个完整原创视频提示词运行文本和 JSON 审计，确认两者无崩溃、0 网络、0 DB、输入哈希不变。

### Step 6：更新真实本机安装

只有前五步全部通过后执行：

```bash
./scripts/install.sh --update
```

然后比较仓库与 `/Users/admin/.codex/skills/hell-grind-aigc-skill`：

```bash
diff -qr skill/hell-grind-aigc-skill /Users/admin/.codex/skills/hell-grind-aigc-skill
```

预期：无差异；安装脚本会保留旧版备份并报告备份路径。

### Step 7：记录验证提交

若验证没有产生文件变化，不创建空 commit。若修复了真实缺陷，先补回归测试，再提交：

```bash
git add <explicit-files>
git commit -m "fix: address v2 acceptance findings"
```

---

## Task 16：最终审计、推送和交付报告

**前提：** 用户在既有任务中已要求开发完成后安装并推送到 `git@github.com:renmu2017/Hell-Grind-AIGC-Skill.git`。执行时仍需把本地提交、安装结果和远程推送分别验证。

### Step 1：检查工作树和提交范围

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git diff --stat origin/main...HEAD
```

预期：没有未提交文件；差异只属于 v2 Skill、测试和文档。

### Step 2：重新运行最终验证

```bash
python3 -m unittest discover -s tests -v
git diff --check origin/main...HEAD
```

### Step 3：核对远程目标

```bash
git remote -v
git ls-remote --heads origin main
```

确认 `origin` 指向：

```text
git@github.com:renmu2017/Hell-Grind-AIGC-Skill.git
```

### Step 4：推送

```bash
git push origin main
```

### Step 5：验证远程落点

```bash
local_head="$(git rev-parse HEAD)"
remote_head="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
test "$local_head" = "$remote_head"
git status --short --branch
```

### Step 6：最终报告

报告必须分开列出：

- v2 方法论新增范围。
- 新增模板和工具。
- 测试数量与结果。
- 官方 Skill 验证结果。
- 本机安装路径和备份路径。
- 本地最终 commit。
- GitHub push 与远程 SHA 验证。
- 默认仍未调用任何生成模型、未生成媒体、未发布外部内容。

---

## 完整验收命令清单

在仓库根目录运行：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile \
  skill/hell-grind-aigc-skill/scripts/init_project.py \
  skill/hell-grind-aigc-skill/scripts/validate_project.py \
  skill/hell-grind-aigc-skill/scripts/audit_prompt.py
git diff --check origin/main...HEAD
test "$(cat skill/hell-grind-aigc-skill/VERSION)" = "2.0.0"
test "$(find skill -name SKILL.md | wc -l | tr -d ' ')" = "1"
diff -qr skill/hell-grind-aigc-skill /Users/admin/.codex/skills/hell-grind-aigc-skill
```

最终成功标准与设计规格第 23 节一致，任何单项未通过都不得声称 v2 完成。
