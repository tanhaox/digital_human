# M0 ComfyUI 本地环境检查清单（v2.0 · 2026-07-22）

> **来源**: [执行计划清单.md](执行计划清单.md) v10 §二 / §四  
> **目的**: M0 第一周搞清算力真相，验证本地能否跑通 ComfyUI + Fish Speech / F5-TTS / ElevenLabs 的端到端工作流。  
> **配套**: `执行计划清单.md` §一（M0 工作流验证）

---

## 🛑 v10 铁律（必读）

| 铁律 | 内容 | 不遵守后果 |
|------|------|-----------|
| **T1 工具即资产** | 每个可复用的 ComfyUI workflow、prompt 模板、TTS 音色包都要版本化，存到 Git。 | M0 跑通的 demo 无法复现，M1 量产返工。 |
| **T2 先跑通，再跑量** | M0-M1 不追求爆款，追求稳定出片；跑量从 M2 开始。 | 产能没验证就堆量，成品质量崩盘。 |
| **T3 数据说话** | 所有分发决策必须依赖真实播放、完播、互动数据，不凭感觉。 | 选题和工具迭代方向错误。 |
| **T4 多语言 ≠ 多团队** | EN/ES 内容必须能从中文 workflow 一键/半自动衍生，否则不做。 | 多语言变成多项目，资源分散。 |

**核心原则**: **算力真相 → 工具选型 → M0 demo**，三者顺序不可乱。

---

## 一、M0 W1 D1: GPU-Z 自检（15 分钟）

### 1.1 下载与安装

| 工具 | 大小 | 下载 | 安装 |
|------|------|------|------|
| **GPU-Z**（TechPowerUp） | ~10 MB | <https://www.techpowerup.com/gpuz/> | 解压即用（绿色版） |

### 1.2 自检流程（5 分钟）

1. 打开 GPU-Z → **Graphics Card** 标签。
2. 记录以下字段：

| 字段 | 含义 | 达标值 | 不达标动作 |
|------|------|--------|------------|
| **Name** | 显卡型号 | RTX 3060+ / RX 6700+ | 见 §1.3 分级表 |
| **Memory Size** | 显存 | ≥8 GB 推荐 / ≥6 GB 可尝试 | <6 GB → 优先云端/API |
| **GPU Clock** | 核心频率 | 仅供参考 | — |
| **Memory Type** | 显存类型 | GDDR6 / GDDR6X | — |
| **Driver Version** | 驱动版本 | ≥531（NVIDIA） | 旧驱动 → NVIDIA GeForce Experience 更新 |
| **CUDA**（NVIDIA） | 计算能力 | ≥11.0（RTX 30 系） | <11.0 → 部分新节点无法运行 |

### 1.3 显卡分级表（M0 算力决策）

M0 目标不是“跑大量”，而是“1 分钟内连续产出 3 条 30 秒成品视频”。这要求 ComfyUI 工作流输出速度快、稳定，而非单纯跑最大模型。

| 等级 | 型号示例 | 显存 | M0 策略 | 说明 |
|------|----------|------|---------|------|
| **🟢 A 级（完美）** | RTX 4090 / 4080 / 3090 / 3080 | ≥12 GB | ComfyUI 本地跑完整 workflow；Fish Speech / F5-TTS 本地推理 | 可留足余量做高清/视频节点 |
| **🟢 B 级（可用）** | RTX 4070 / 4060 Ti / 3070 / 3060 Ti | 8-12 GB | ComfyUI 本地跑，启用 fp16 / 降低视频节点分辨率 | M0 足够 |
| **🟡 C 级（凑合）** | RTX 3060 / 4060 / RX 6700 | 8 GB | ComfyUI 本地跑轻量化 workflow；TTS 本地，复杂视频节点用低显存模式 | 需严格限制 batch 和分辨率 |
| **🟠 D 级（勉强）** | GTX 1660 Ti / RX 580 / RTX 3050 | 4-6 GB | 图像生成用 ComfyUI CPU/低精度；视频/后期优先走云端或 API | M0 仅做 workflow 验证 |
| **🔴 E 级（放弃本地）** | 无独显 / 集显 / GTX 1050 以下 | <4 GB | **全部走云端/API**: RunComfy / ComfyICU / Fish Speech API / ElevenLabs API | 不在本地部署 |

**关键阈值**:

- **≥8 GB 显存** = 可本地跑常规 ComfyUI workflow（文生图 + 轻量图生视频 + 合成）。
- **<8 GB** = 需要精简节点、降分辨率，或部分步骤走云端。
- **<6 GB / 无独显** = 本地只做 workflow 设计，实际渲染走云端/API。

### 1.3 自检结果记录模板

```markdown
# M0 W1 D1 GPU 自检报告

**日期**: 2026-07-2X
**自检人**: ___________

## GPU-Z 截图关键数据
- 显卡型号: __________
- 显存: __________ GB
- CUDA: __________
- 驱动版本: __________

## 自检结论
- [ ] A 级（完美）/ B 级（可用）/ C 级（凑合）/ D 级（勉强）/ E 级（放弃本地）
- [ ] 决策: 本地完整 workflow / 本地+云端混合 / 全云端

## 后续动作
- [ ] 安装 ComfyUI（若 A/B/C 级）
- [ ] 部署 Fish Speech / F5-TTS（若 A/B/C 级）
- [ ] 注册 ElevenLabs API key（所有等级）
- [ ] 准备云端 fallback（若 D/E 级）
```

---

## 二、M0 工具链

| 模块 | 方案 | 说明 |
|------|------|------|
| **图像 / 视频 / 后期** | **ComfyUI** | 统一工作流底座。负责文生图、图生视频、风格迁移、抠像、合成、调色、导出。 |
| **语音 / TTS** | Fish Speech / F5-TTS / ElevenLabs | 中文首选 Fish Speech 或 F5-TTS；英文/高质感场景用 ElevenLabs；所有音色入库存档。 |
| **文案 / 翻译** | 中文文案库 + 结构化模板 | EN/ES 走 AI 翻译 + 母语抽检；M3 前不做 AR。 |
| **发布 / 数据** | 平台官方创作者后台为主 | 数据用表格/看板汇总，暂时不做复杂爬虫。 |

### 2.1 必备基础工具（全员）

| 工具 | 用途 | 安装命令 / 链接 | 验收 |
|------|------|-----------------|------|
| **Python 3.10+** | 运行环境 | <https://www.python.org/> | `python --version` |
| **FFmpeg** | 视频编码/拼接 | <https://www.gyan.dev/ffmpeg/builds/> | `ffmpeg -version` |
| **Git** | 仓库克隆 | <https://git-scm.com/> | `git --version` |
| **CUDA 11.8+**（NVIDIA） | GPU 加速 | <https://developer.nvidia.com/cuda-downloads> | `nvidia-smi` |
| **VSCode** | 编辑/调试 | <https://code.visualstudio.com/> | 启动 |

### 2.2 ComfyUI 安装（A/B/C 级 GPU）

```bash
# 1. 克隆官方仓库
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI

# 2. 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
# source venv/bin/activate

# 3. 安装 PyTorch（按 CUDA 版本选择）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# 4. 安装依赖
pip install -r requirements.txt

# 5. 启动
python main.py
```

默认访问：<http://127.0.0.1:8188>

**关键配置**:

- `extra_model_paths.yaml`：把模型目录统一外置，避免重复下载。
- `--lowvram` / `--normalvram` / `--highvram`：按显存选择启动参数。
- `--fp16-unet`：8 GB 以下显卡建议开启。

### 2.3 必备节点包（M0 建议）

| 节点包 | 作用 | 安装方式 |
|--------|------|----------|
| Comfyroll Studio | 基础工具节点 | ComfyUI Manager |
| WAS Node Suite | 图像处理/实用工具 | ComfyUI Manager |
| ComfyUI-VideoHelperSuite | 视频加载/导出 | ComfyUI Manager |
| ComfyUI-Manager | 节点管理器 | 内置或单独安装 |

> M0 不追求节点大全，先保证“文生图 → 图生视频/动画 → 合成 → 导出 30 秒 9:16”能跑通。

### 2.4 TTS 部署

#### Fish Speech（中文主力，本地）

```bash
git clone https://github.com/fishaudio/fish-speech.git
cd fish-speech
pip install -e .
# 下载预训练模型（首次）
python tools/download_models.py
# 启动推理 API
python tools/run_webui.py
```

显存需求：约 4-6 GB（推理）。

#### F5-TTS（轻量备选，本地）

```bash
git clone https://github.com/SWivid/F5-TTS.git
cd F5-TTS
pip install -e .
# 启动 Gradio 界面
python src/f5_tts/infer/infer_gradio.py
```

显存需求：约 4 GB（推理）。

#### ElevenLabs（英文/高质感备选，API）

无需本地部署，注册获取 API key：

```bash
export ELEVENLABS_API_KEY=your_key
# 在脚本中调用 REST API 或使用 elevenlabs Python SDK
```

所有音色必须入库存档：`assets/voices/` 或 `voices.json`。

---

## 三、M0 Workflow 验收标准

M0 唯一硬指标：一个 ComfyUI workflow 可在 **1 分钟内连续产出 3 条 30 秒成品视频**。

### 3.1 输出规格

| 维度 | 标准 |
|------|------|
| 分辨率 | 1080×1920（9:16）为主；1:1 封面/图文为辅 |
| 帧率 | ≥24 fps，目标 30 fps |
| 时长 | 30 秒 ±2 秒 |
| 音频 | 双声道 48 kHz，LUFS -14 ±1 |
| 字幕 | 必备，位置不遮挡人脸关键信息 |
| AI 标识 | 按平台要求标注 |

### 3.2 质量检查项

| 检查项 | 工具/方法 | 通过标准 |
|--------|-----------|----------|
| 分辨率/帧率 | FFmpeg / 播放器 | 1080×1920 / 30fps |
| 音画同步 | 人工听看 | 无明显延迟 |
| 画面一致性 | 人工抽查 | 无闪烁/崩坏/水印错误 |
| LUFS | FFmpeg `loudnorm` | -14 ±1 |
| 无敏感内容 | 预审清单 | 无政治/医疗/金融/擦边 |

### 3.3 M0 验收流程

```markdown
# M0 demo 验收 checklist

**demo 文件**: demo-01.mp4 / demo-02.mp4 / demo-03.mp4

## 自动化/半自动检测
- [ ] 3 条均为 1080×1920 / 30fps
- [ ] 3 条 LUFS 均在 -14 ±1
- [ ] 3 条音画同步无异常

## 人工检查
- [ ] 3 条内容均符合平台合规要求
- [ ] 3 条人设/音色/视觉风格一致
- [ ] 3 条从同一 workflow 在 1 分钟内连续产出

## 任一维度不达标
- [ ] 优化 workflow 节点 / 参数
- [ ] 24 小时内复测
- [ ] 仍不达 → 降级为云端/API 方案或调整 M0 范围
```

---

## 四、M0 工具链最终决策矩阵

| GPU 等级 | ComfyUI | TTS | 云端/API fallback | M0 目标 |
|----------|---------|-----|-------------------|---------|
| **🟢 A** | 本地完整 workflow | Fish Speech + F5-TTS + ElevenLabs | 可选 | 1 分钟 3 条 30 秒 |
| **🟢 B** | 本地完整 workflow（fp16） | 同上 | 可选 | 1 分钟 3 条 30 秒 |
| **🟡 C** | 本地简化 workflow（降分辨率/batch） | 本地 Fish/F5 | 复杂节点可云端 | 1 分钟 3 条 30 秒（允许 720p 中间件） |
| **🟠 D** | 本地仅做 workflow 设计/低显存模式 | ElevenLabs API / Fish Speech CPU | 云端渲染 | 跑通 workflow 逻辑，不要求本地速度 |
| **🔴 E** | 云端 ComfyUI（RunComfy / ComfyICU） | 全 API | 全部云端/API | 跑通端到端逻辑 |

**M0 跑通 = 3 条 30 秒成品在规格内连续产出**；**M0 跑不通 = 停在 M0，优化 workflow 或切换算力方案**。

---

## 五、M0 W1 安装日程

| 日 | 任务 | 工具 | 验收 |
|----|------|------|------|
| D1 | GPU-Z 自检 + 截图记录 | GPU-Z | 自检报告.md |
| D1 | 装 Python 3.10 / FFmpeg / Git / CUDA | 官网 | 命令行验证 |
| D2 | 安装 ComfyUI + 必备节点包 | Git/pip | 启动 `python main.py` 成功 |
| D3 | 部署 Fish Speech + F5-TTS；注册 ElevenLabs | Git/pip/浏览器 | TTS 能输出中文/英文样例音频 |
| D4-D5 | 搭建第一条 workflow：文生图 → 图生视频/动画 → 合成 → 导出 30 秒 9:16 | ComfyUI | 3 个 MP4 |
| D6 | 跑 §3.3 验收 checklist | FFmpeg / 人工 | 全部达标 |
| D7 | 第一次周会：检查 M0 门禁进度，调整工具链 | 会议 | M0 优化清单 |

### 5.1 安装验证脚本（M0 W1 D3 跑）

```bash
# verify_env.sh
# 保存为 verify_env.sh（Linux/macOS/Git Bash）并执行

echo "===== M0 W1 环境验证 ====="

# Python
python --version

# FFmpeg
ffmpeg -version 2>&1 | head -1

# CUDA / GPU
nvidia-smi

# PyTorch + CUDA
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# ComfyUI 目录是否存在
ls ComfyUI/main.py

# Fish Speech 目录是否存在
ls fish-speech

# F5-TTS 目录是否存在
ls F5-TTS

# ElevenLabs key 是否配置
python -c "import os; print('ELEVENLABS_API_KEY:', '已设置' if os.getenv('ELEVENLABS_API_KEY') else '未设置')"

echo "===== 验证完成 ====="
```

---

## 六、关键陷阱（踩过即死）

| 陷阱 | 后果 |
|------|------|
| ❌ 跳过 GPU-Z 自检直接装 ComfyUI | D 级/E 级 GPU 本地跑不动，M0 报废。 |
| ❌ 用集显跑视频生成节点 | 显存 OOM + 死机。 |
| ❌ 不固定 ComfyUI 节点版本 | 节点升级导致 workflow 崩坏，无法复现。 |
| ❌ 生产 workflow 直接升级节点 | 未在副本验证就动生产，demo 全废。 |
| ❌ TTS 音色与人设不绑定 | 品牌混乱，观众记不住。 |
| ❌ M0 没跑通就追求日产 3 条 | 产能建立在沙上，M2 全返工。 |

| 必做 | 原因 |
|------|------|
| ✅ M0 W1 D1 第一件事：GPU-Z 自检 + 截图 | 算力真相 → 工具选型。 |
| ✅ 按 GPU 等级决定本地/云端混合方案 | 不踩“无 GPU + 无额度 = 月产 0 条”的坑。 |
| ✅ 生产/测试 workflow 分离 | 保护已验证资产。 |
| ✅ 音色、prompt、参数统一归档 | workflow 可复现、可迭代。 |
| ✅ M0 门禁不过，不进入 M1 | 硬截止从 M0 开始。 |

---

## 七、参考资源

| 工具 | 链接 |
|------|------|
| ComfyUI | <https://github.com/comfyanonymous/ComfyUI> |
| Fish Speech | <https://github.com/fishaudio/fish-speech> |
| F5-TTS | <https://github.com/SWivid/F5-TTS> |
| ElevenLabs | <https://elevenlabs.io/> |
| FFmpeg | <https://ffmpeg.org/> |
| GPU-Z | <https://www.techpowerup.com/gpuz/> |

### 内部参考

- [执行计划清单.md](执行计划清单.md) §二 M0 工具链、§四 M0 门禁
- [自动化数字人新闻播报 — 本地部署方案.md](自动化数字人新闻播报%20—%20本地部署方案.md)
- [什么是优秀的短视频.md](什么是优秀的短视频.md)

---

## 八、版本与维护

- **v2.0** · 2026-07-22 · 对齐 `执行计划清单.md` v10：ComfyUI 统一工作流 + Fish Speech / F5-TTS / ElevenLabs TTS 栈。
- **维护人**: 主理人本人
- **更新触发**: M0 末切换工具或 workflow 结构变更 → 升 v2.x
- **销毁条件**: 项目终结时归档 `archives/`
