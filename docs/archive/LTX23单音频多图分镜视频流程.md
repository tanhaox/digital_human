# LTX 2.3 单音频多图分镜数字人视频流程

> 状态：已实际跑通，作为数字人项目的 LTX 2.3 单音频多图分镜流程记录。
>
> 核心原则：一段音频决定最终视频时长；多张图片用于分镜/画面切换，不创建多段独立音频驱动。

## 1. 已验证的最小场景

- ComfyUI：`http://127.0.0.1:8188`
- 工作流源文件：`G:\下载\LTX23-单多图数字人语音驱动.json`
- 修复副本：`G:\下载\LTX23-单多图数字人语音驱动_已修复最小实验.json`
- 音频：`E:\AI\ComfyUI_windows_portable\ComfyUI\input\test_audio.wav`
- 测试音频截取：10 秒
- FPS：24
- LTX 总帧数：241
- 分镜图 1：`正视图_00002_.png`
- 分镜图 2：`侧视图+45°正视图_00001_.png`
- 图片出现帧：`0`、`120`
- 分辨率：`576 × 1024`
- 输出：H.264 MP4 + AAC 音频
- 已验证输出：`E:\AI\ComfyUI_windows_portable\ComfyUI\output\LTX23-10sec-multistory_00001_.mp4`
- 实测输出时长：`9.959 秒`
- 实测视频帧率：`24 FPS`
- 实测视频帧数：约 `241`
- 实测音频流：存在，编码为 AAC

## 2. 完整业务流程

```text
单段音频
  → 获取总时长
  → 按用户设置截取音频
  → 计算视频总帧数
  → Multi Image Loader 加载多张分镜图
  → 分段控制设置 max_frames 和多个分段块
  → LTX Sequencer 设置图片出现帧
  → LTX 2.3 单音频+多图图像引导采样
  → 解码视频与音频
  → 合并为 MP4
  → 校验视频时长、帧率、音轨和文件
```

## 3. 音频规则

整个视频只使用一段音频。音频总时长决定输出视频时长，不支持多段音频分别驱动同一视频的流程。

逻辑上可以使用 `Load Audio UI`：

1. 选择音频；
2. 获取音频总时长；
3. 通过 `duration` 设置截取时长；
4. 点击播放/预览仅用于人工确认，可被自动流程忽略；
5. 输出单一 `AUDIO`。

API 实际验证时使用等价链路：

```text
LoadAudio → TrimAudioDuration → LTXVAudioVAEEncode
```

典型 API 输入：

```json
{
  "class_type": "LoadAudio",
  "inputs": {"audio": "test_audio.wav"}
}
```

```json
{
  "class_type": "TrimAudioDuration",
  "inputs": {
    "audio": ["LoadAudio节点", 0],
    "start_index": 0.0,
    "duration": 10.0
  }
}
```

WAV 时长可用 Python `wave` 获取：

```python
with wave.open(audio_path, "rb") as wav:
    duration = wav.getnframes() / wav.getframerate()
```

## 4. 总帧数规则

当前 FPS 为 24。LTX 帧数使用：

```text
total_frames = round(duration_seconds × fps) + 1
```

并对齐到 LTX 常用的 `8*n + 1`：

```python
def calculate_ltx_frames(duration_seconds: float, fps: float) -> int:
    frames = round(duration_seconds * fps) + 1
    n = round((frames - 1) / 8)
    return 8 * n + 1
```

10 秒、24 FPS：

```text
10 × 24 + 1 = 241 帧
```

因此：

```text
分段控制.max_frames = 241
EmptyLTXVLatentVideo.length = 241
```

不要把 10 秒、24 FPS 写成 240 帧。

实际输出时长按以下公式核对：

```text
actual_duration = (total_frames - 1) / fps
```

## 5. Multi Image Loader 多图分镜

`Multi Image Loader → Upload Images` 用于上传多张分镜图片。多图属于同一个音频下的视觉分段，不创建多个视频任务。

已验证图片：

```text
正视图_00002_.png
侧视图+45°正视图_00001_.png
```

默认图像配置：

```text
width = 576
height = 1024
interpolation = lanczos
resize_method = crop
multiple_of = 32
img_compression = 0
```

典型分镜数据：

```python
segments = [
    {
        "image": "正视图_00002_.png",
        "start_frame": 0,
        "end_frame": 120,
        "prompt": "a person speaking naturally to camera",
        "strength": 1.0,
    },
    {
        "image": "侧视图+45°正视图_00001_.png",
        "start_frame": 120,
        "end_frame": -1,
        "prompt": "the same person speaking from a slight side angle",
        "strength": 1.0,
    },
]
```

图片顺序必须稳定，并与分段索引一一对应。

## 6. 分段控制

`分段控制.max_frames` 必须与音频计算出的总帧数一致。

`Add` 可以添加多个分段块，每个块对应一张图片/一个分镜提示词。分段必须覆盖同一条音频对应的完整视频时间轴。

示例：

```text
max_frames = 241
分镜 1：0 → 120
分镜 2：120 → -1
```

规则：

- 首帧 `0` 保持不变；
- 尾帧 `-1` 保持不变；
- 中间图填写实际出现帧；
- 不允许超出总帧数；
- 默认不允许空隙或重叠；
- 图片数量和分段数量不一致时应报错；
- 每个分段可以有独立提示词和强度；
- 采样、LoRA、VAE 等其他数值默认保持工作流配置。

原始 JSON 中的分段控制节点曾丢失 `class_type` 和动态输入结构，不能直接依赖其 `UNKNOWN` 字段猜测恢复。系统实现应使用明确的内部 `segments[]` 数据模型。

## 7. LTX Sequencer / 图像引导实现

人工 UI 逻辑使用 `LTX Sequencer`：

```text
图片 1：frame 0
图片 2：frame 120
最后图片：frame -1
```

API 验证时，为规避动态节点输入结构问题，使用等价的静态节点链：

```text
LTXVAddGuide（图片 1，frame_idx=0）
  → LTXVAddGuide（图片 2，frame_idx=120）
```

第一个节点：

```json
{
  "class_type": "LTXVAddGuide",
  "inputs": {
    "positive": ["正向提示词节点", 0],
    "negative": ["负向提示词节点", 0],
    "vae": ["视频VAE节点", 0],
    "latent": ["EmptyLTXVLatentVideo节点", 0],
    "image": ["图片1节点", 0],
    "frame_idx": 0,
    "strength": 1.0
  }
}
```

第二个节点：

```json
{
  "class_type": "LTXVAddGuide",
  "inputs": {
    "positive": ["第一个LTXVAddGuide", 0],
    "negative": ["第一个LTXVAddGuide", 1],
    "vae": ["视频VAE节点", 0],
    "latent": ["第一个LTXVAddGuide", 2],
    "image": ["图片2节点", 0],
    "frame_idx": 120,
    "strength": 1.0
  }
}
```

最终视频 latent 使用第二个引导节点的 latent 输出。

## 8. 核心模型和默认配置

### 模型

```text
UNet:
LTX2.3\LTX-2.3-22B-distilled-1.1-Q4_K_M.gguf

Dual CLIP:
gemma-3-12b-it-heretic-Q4_K_M.gguf
ltx-2.3_text_projection_bf16.safetensors
type = ltxv

视频 VAE:
_backup_20260724\LTX23_video_vae_bf16.safetensors

音频 VAE:
_backup_20260724\LTX23_audio_vae_bf16.safetensors
```

### LoRA 默认值

```text
LTX-2.3\ltx2.3-transition.safetensors = 0.70
LTX-2.3\LTX-2.3-22b-AV-LoRA-talking-head-v1.safetensors = 1.00
LTX-2.3\Ltx2.3-Licon-VBVR-I2V-390K-R32.safetensors = 关闭
```

### 采样默认值

```text
sampler = euler_ancestral
scheduler = linear_quadratic
steps = 8
denoise = 1.0
cfg = 1.0
fps = 24
```

### VAE 解码默认值

```text
tile_size = 512
overlap = 64
temporal_size = 4096
temporal_overlap = 8
```

## 9. 音视频 latent 连接

音频：

```text
LoadAudio
  → TrimAudioDuration
  → LTXVAudioVAEEncode
  → SetLatentNoiseMask
```

视频：

```text
EmptyLTXVLatentVideo
  → LTXVAddGuide / LTX Sequencer
```

合并：

```text
LTXVConcatAVLatent
  → SamplerCustom
  → LTXVSeparateAVLatent
  ├── video latent → VAEDecodeTiled
  └── audio latent → LTXVAudioVAEDecode
```

输出：

```text
CreateVideo(images, fps, audio)
  → SaveVideo(format=mp4, codec=h264)
```

## 10. SageAttention 回退规则

当前环境中 `PathchSageAttentionKJ` 会因为 DLL 错误失败：

```text
DLL load failed while importing _fused
```

已验证可行方式：绕过 SageAttention patch，直接使用原始模型输出，回退到 ComfyUI native attention。

系统实现：

```python
if sageattention_available:
    use_sage_attention()
else:
    bypass_sage_attention_patch()
    log_warning("SageAttention unavailable; using native attention")
```

不要因为 SageAttention 不可用而阻止基础视频流程；不要自动重装 Torch、CUDA 或 SageAttention。

## 11. ComfyUI API

健康检查：

```http
GET http://127.0.0.1:8188/system_stats
GET http://127.0.0.1:8188/queue
GET http://127.0.0.1:8188/object_info
```

提交：

```http
POST http://127.0.0.1:8188/prompt
```

```json
{
  "prompt": {"node_id": {"class_type": "NodeType", "inputs": {}}},
  "client_id": "system-client-id"
}
```

轮询：

```http
GET http://127.0.0.1:8188/history/{prompt_id}
```

成功条件：

```json
{
  "status": {
    "status_str": "success",
    "completed": true
  }
}
```

失败时必须报告：

- `node_id`
- `node_type`
- `exception_message`
- traceback

## 12. 输出验证

生成完成后必须验证文件和媒体元数据，不得只根据 HTTP 200 判断成功。

```bash
ffprobe -v error \
  -show_entries format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate \
  -of json \
  "E:/AI/ComfyUI_windows_portable/ComfyUI/output/result.mp4"
```

必须确认：

- MP4 存在；
- 文件大小大于 0；
- 视频编码为 H.264；
- 音频流存在；
- 视频分辨率为 576×1024（或用户指定值）；
- FPS 为 24；
- 实际时长接近截取后的音频时长；
- 视频帧数与 `max_frames` 对齐。

## 13. 系统集成禁止动作

- 不覆盖原始工作流；
- 不修改 ComfyUI 核心源码；
- 不把多段音频分别驱动同一视频；
- 不让每张图片创建独立视频任务；
- 不默认启用 MelBand 人声分离；
- 不因 SageAttention 失败阻止主流程；
- 不擅自替换 Torch、CUDA、模型、LoRA、VAE 或采样器；
- 不将 240 帧当作 10 秒、24 FPS 的 LTX 帧数；
- 不无限等待 ComfyUI 任务；
- 不跳过 MP4 的音频流、时长、帧率和文件大小验证；
- 不把临时 API payload 当作项目 SSOT；
- 不开放式穷举节点类型或批量尝试工作流。

## 14. 推荐模块化数据结构

```python
video_job = {
    "audio": {
        "path": "test_audio.wav",
        "trim_start": 0.0,
        "duration": 10.0,
        "fps": 24.0,
        "total_frames": 241,
    },
    "storyboard": [
        {
            "image": "正视图_00002_.png",
            "frame_idx": 0,
            "prompt": "a person speaking naturally to camera",
            "strength": 1.0,
        },
        {
            "image": "侧视图+45°正视图_00001_.png",
            "frame_idx": 120,
            "prompt": "the same person speaking from a slight side angle",
            "strength": 1.0,
        },
    ],
    "output": {
        "format": "mp4",
        "codec": "h264",
        "fps": 24,
    },
}
```

## 15. 当前验收基线

```text
单音频：test_audio.wav
截取：10 秒
FPS：24
总帧数：241
图片：2 张
出现帧：0、120
分辨率：576×1024
采样步数：8
输出：H.264 MP4 + AAC
结果：实际成功，时长 9.959 秒
```
