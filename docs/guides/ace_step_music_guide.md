# ACE-Step 1.5 本地音乐生成手册

> 建立于 2026-10-19（环境安装 + 悬疑 60s 纯器乐测试曲实测通过）。
> 定位：数字人流水线 BGM/氛围曲的**本地免费通道**，与出图同走 ComfyUI（127.0.0.1:8188），零 API 费、断网可用、Apache 协议可商用。
> 环境在 **E 盘 ComfyUI 侧，不在本仓库**；本手册是唯一权威记录。

## 一、能力边界（三问三答，2026-10 调研确认）

| 需求 | 结论 | 用法 |
|---|---|---|
| 纯器乐无人声（悬疑/搞笑/激烈/热血氛围） | ✅ | `lyrics` 留空 + tags 里写 "Pure instrumental, no vocals"（英文标签效果>中文） |
| 提示词迭代二创 | ✅ | KSampler `seed` 固定可复现；改 tags/seed 重抽即迭代。实测 60s 曲 ~30s，抽卡成本低 |
| 单乐器（笛/琵琶/吉他/钢琴） | ✅ | tags 写 `pipa solo` / `bamboo flute` / `solo piano` + `solo performance, no drums, no bass, no percussion` 压制自发伴奏。中式乐器是强项 |
| Repaint 局部重绘 / Extend 续写 / 风格改编 | ⚠️ 未开通 | 需 1.0 版模型 `ace_step_v1_3.5b.safetensors`（~7G，未下载），M2M 编辑模板暂不可用 → backlog ID-114 |

## 二、本机环境

- **ComfyUI v0.34.5**（`E:\AI\ComfyUI_windows_portable\`）**原生支持** ACE-Step 1.5（`comfy_extras/nodes_ace.py`），无需第三方节点。
- 模型四件（hf-mirror 下载，共 ~18.6G；HF 直连不通，走 hf-mirror.com）：
  - `models/diffusion_models/acestep_v1.5_xl_base_bf16.safetensors`（9.3G，主模型）
  - `models/text_encoders/qwen_0.6b_ace15.safetensors`（1.2G）+ `qwen_4b_ace15.safetensors`（7.9G，DualCLIPLoader 双载）
  - `models/vae/ace_1.5_vae.safetensors`（322M）
- 官方工作流模板 ×6：`ComfyUI/user/default/workflows/ACE-Step/`（xl_base / xl_sft / xl_turbo / 4b对比 / M2M编辑 / 纯器乐）。
- 启动：`run_nvidia_gpu.bat`（GPU-UUID 锚定 4090，双卡视角反序，勿裸启）。**与 llama 互斥（gpu_mutex 纪律）**。

## 三、使用方式

### 手动（ComfyUI 界面）
启动 ComfyUI → 工作流面板 `ACE-Step/` → 选 `audio_ace_step1_5_xl_base` → 改 tags（英文自然语言描述曲子）+ lyrics 留空 → Queue。

### 程序化（API 提交，与出图同一套）
核心节点链：`UNETLoader + DualCLIPLoader + VAELoader → TextEncodeAceStepAudio1.5 → ModelSamplingAuraFlow(shift=3) → KSampler(euler/simple/50步/cfg6) → EmptyAceStep1.5LatentAudio → VAEDecodeAudio → SaveAudioAdvanced`

`TextEncodeAceStepAudio1.5` 关键输入：

| 参数 | 说明 |
|---|---|
| `tags` | 英文自然音乐描述（流派+情绪+BPM+乐器+结构），1.5 是描述驱动非标签堆砌 |
| `lyrics` | 纯器乐留空 `""`；带唱写结构标记歌词 |
| `seed` | LLM 音频码采样种子（与 KSampler 的 seed 分开） |
| `bpm` / `duration` / `timesignature` / `keyscale` / `language` | 拍速/时长(≤2000s)/拍号/调性("D minor")/语种 |
| `generate_audio_codes` | true=LLM 预生成音频码（慢但质量高，默认开） |

最小可用提交图（悬疑器乐 60s 实测版，filename_prefix 可改）：

```json
{
  "104": {"class_type": "UNETLoader", "inputs": {"unet_name": "acestep_v1.5_xl_base_bf16.safetensors", "weight_dtype": "default"}},
  "105": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "qwen_0.6b_ace15.safetensors", "clip_name2": "qwen_4b_ace15.safetensors", "type": "ace", "device": "default"}},
  "106": {"class_type": "VAELoader", "inputs": {"vae_name": "ace_1.5_vae.safetensors"}},
  "78": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["104", 0], "shift": 3}},
  "94": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
    "clip": ["105", 0], "tags": "Dark, suspenseful cinematic instrumental at 96 BPM in 4/4. ...", "lyrics": "",
    "seed": 0, "bpm": 96, "duration": 60, "timesignature": "4", "language": "en", "keyscale": "D minor",
    "generate_audio_codes": true, "cfg_scale": 2, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0}},
  "47": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["94", 0]}},
  "98": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": 60, "batch_size": 1}},
  "3": {"class_type": "KSampler", "inputs": {"model": ["78", 0], "positive": ["94", 0], "negative": ["47", 0], "latent_image": ["98", 0], "seed": 0, "steps": 50, "cfg": 6, "sampler_name": "euler", "scheduler": "simple", "denoise": 1}},
  "18": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["3", 0], "vae": ["106", 0]}},
  "111": {"class_type": "SaveAudioAdvanced", "inputs": {"audio": ["18", 0], "filename_prefix": "audio/ace_test_suspense", "format": "mp3", "format.quality": "V0"}}
}
```

提交：`POST http://127.0.0.1:8188/api/prompt`，body `{"prompt": <上图>, "client_id": "..."}`；轮询 `GET /api/history/{prompt_id}`，产出在 `ComfyUI/output/audio/`。

## 四、实测数据（2026-10-19，4090 48G）

- 60 秒曲端到端 **~30 秒**（模型已载显存；冷启动另加模型加载 ~1-2 分钟）。
- 全套加载显存 ~19.4G。
- 产出：60.0s 整 / V0 mp3（~1.6MB）。

## 五、踩坑记录

1. **SaveAudioAdvanced 的 API 键名是点号全名 `format.quality`**——嵌套 dict `{"format":"mp3","quality":"V0"}` 会过校验但执行时被静默丢弃（报 execute missing 'format'）；平铺 `quality` 会被校验拒绝（提示 input_name `format.quality`）。
2. HF 直连超时，模型一律走 `hf-mirror.com`（`curl -C -` 断点续传）。
3. ComfyUI 官方模板仓库：`github.com/Comfy-Org/workflow_templates`（templates/ 目录，raw 直下 JSON）。
