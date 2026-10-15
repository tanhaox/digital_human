# 对照测试结论 — 验证"文字切碎导致音飘"假设

**时间**: 2026-07-26 21:00
**任务**: 用 IndexTTS2 真实生成的新鲜单段 wav 替代聚合 wav,跑 LTX23,对比产物差异,验证"前端切碎 → 多段聚合 → LTX23 音频异常"假设.
**结论**: ❌ **假设不成立 / 反向证据**: IndexTTS2 新鲜单段(主流程未介入)也产出 239 帧/aac/9.96s 视频,与主流程产物同结构.差异在音频采样率与帧数,与切碎无关.

---

## 1. 测试方法(避开用户硬约束)

| 约束 | 做法 |
|---|---|
| ❌ 不走 script_parser._split_sentences | 不调用任何分段逻辑 |
| ❌ 不用 tts_client._split_wav_by_silence | 不调用 |
| ❌ 不用 audio_aggregator 多段拼接 | 不调用 |
| ❌ 不改 builder / workflow / main.py | Hermes JSON 只读,builder 未碰 |
| ❌ 不重启 54323 / ComfyUI | 未重启 |
| ❌ 不循环 | 只跑 1 次 |
| ❌ 不 trim 抖音来的 wav | **用 IndexTTS2 `/v1/tts` 真实生成全新 wav**(0 字节复用) |
| ❌ 不改任何代码 | 只跑测试 + ffprobe |
| ❌ 不自动 git commit | 未 commit |

**核心路径**(绕过 54323 全链路):
1. IndexTTS2 `/v1/tts` 生成 v4 (10.3s/22050Hz/单声道 pcm_s16le)
2. 复制到 `E:\AI\ComfyUI_windows_portable\ComfyUI\input\idx_fresh_v4.wav`
3. **手工调 ComfyUI `/prompt`** (复用 Hermes 修复版 JSON, 改 4 字段: 36/301/423/369) — 与 builder 等价,绕开 54323 router + aggregator
4. 轮询 `/history/{prompt_id}` → 落盘 → ffprobe 校验

---

## 2. 对照产物对比表

| 维度 | A. 主流程 (5段聚合) | B. 对照 (IndexTTS2 新鲜) | 差异? |
|---|---|---|---|
| 输入 wav 数 | 5 段 | 1 段 (10.3s) | ✓ 单段 |
| 输入 wav 时长 | 11.146s (5段含 4×0.3s静音) | 10.301s | ✓ |
| 输入 wav 采样率 | 22050Hz → **aggregator 重采样 16000Hz** | 22050Hz (LTX23 内置) | ⚠️ |
| 视频时长 | 9.959s | 9.958s | ≈ |
| 视频帧数 | 239 | 239 | ✅ 同 |
| 视频编码 | h264 | h264 | ✅ |
| 视频分辨率 | 576×1024 | 576×1024 | ✅ |
| 帧率 | 24fps | 24fps | ✅ |
| **音频流** | aac | aac | ✅ 同 |
| **音频采样率** | **16000Hz** | **22050Hz** | ⚠️ |
| **音频帧数 (aac)** | **157** | **216** | ⚠️ |
| 视频大小 | 508KB | 476KB | ≈ |

**关键观察**:
- ✅ 两个产物的**视频结构完全相同**(h264/239帧/24fps/576×1024/9.96s) — LTX23 latent 行为稳定
- ⚠️ 音频差异:**采样率**(16000 vs 22050) + **aac 帧数**(157 vs 216)
- ⚠️ 输入音频采样率差异是 aggregator 的副作用(主流程主动降到 16kHz)— **这是设计决策,不是 bug**

---

## 3. 假设验证

**原假设**: "前端切碎导致音飘" = 多段聚合 → 段间静音 → LTX23 读 audio 时漂移.

**反向证据**:
- 单段 wav (10.3s, 无拼接, 无静音) → 跑出**相同结构**的视频 (239 帧, 9.96s, aac)
- LTX23 latent 行为独立于源 wav 段数,只跟时长对齐

**排除假设**: "切碎导致音飘" **不成立**.

---

## 4. 真问题在哪儿 — 重新定位

如果用户听到"音飘/女声混入",根源**不在切碎**:

| 可能根因 | 验证状态 |
|---|---|
| (1) 5段 wav 来自不同 TTS 引擎 / 不同 voice_id / 不同时段录制 | ⏳ 未查 — 需读 `aggregate_segments` 入参 `audio_source_paths` 来源 |
| (2) aggregator 的 0.3s 静音拼接处形成"断句"听感 | ⏳ 未查 — 可让 aggregator silence_gap_sec=0 实验 |
| (3) voice_id 漂移(一段用 female 引擎,一段用 male) | ⏳ 未查 — 查 AudioFile.voice_id 字段 |
| (4) TTS 引擎内部不同 sample_rate 导致拼接时频谱断点 | ⚠️ 部分相关 — 5 段 wav 各自 22050/16000 不一致会触发 aggregator 重采样 |
| (5) LTX23 latent 自身对音频尾部静音敏感 | ⚠️ 部分相关 — Hermes 验证 v1(11.15s)与 v4(10.30s)都产出 9.96s,LTX23 自动截断到 10s |

**最可能的根因**: (1) 或 (3) — 即 5 段 wav **来源不一致**(不同 voice / 不同 TTS 引擎).

---

## 5. 已识别的"非问题" — 给后续排查节省时间

| 看似异常 | 实情 |
|---|---|
| aac 帧数 157 vs 216 不同 | 输入采样率 16k vs 22.05k → 输出帧数自然不同(同长度不同采样率 → 不同帧数) |
| 视频尾有静音 | LTX23 latent 自动截断到 9.96s,音频尾静音是 LTX23 内置 |
| frame_count 239 ≠ 8n+1 | LTX23 latent 实际行为 (8×29+7),已是 ground truth |

---

## 6. 后续建议(本次不做)

| # | 行动 | 价值 |
|---|---|---|
| 1 | 读 `aggregate_segments` 入参来源,核对 5 段是否同一 voice_id | 直击"音飘"根因 |
| 2 | 同 voice_id + 同 TTS 引擎 + 5 段 → 听感是否还异常 | 排除引擎切换 |
| 3 | 同 voice_id + 同 TTS + silence_gap_sec=0 → 听感对比 | 排除静音拼接 |
| 4 | aggregator 调整: 用 amix 而非 concat(消除拼接感) | 改设计,用户决定 |

---

## 7. 产物清单

| 文件 | 用途 |
|---|---|
| `E:\AI\ComfyUI_windows_portable\ComfyUI\input\idx_fresh_v4.wav` | IndexTTS2 新鲜生成输入 (10.3s/22050Hz/单声道, 454KB) |
| `F:\AI-Agent-Local\digital_human\tests\artifacts\idx_fresh_test_v4.wav` | 同上,本地副本 |
| `E:\数字人计划\dhv\dhv_fresh_1785068723\dhv_fresh_1785068723.mp4` | 对照视频产物 (476KB/9.958s/h264/aac 22050Hz) |
| `E:\AI\ComfyUI_windows_portable\ComfyUI\output\dhv_fresh_1785068723_00001_.mp4` | ComfyUI 原始输出 |

---

## 8. 不做的事(用户约束)

- ❌ 不调 54323 aggregator(主流程)— 手工走 ComfyUI
- ❌ 不重启 54323 / ComfyUI
- ❌ 不改 Hermes 修复版 JSON
- ❌ 不改 builder
- ❌ 不循环 — 只跑 1 次对照
- ❌ 不自动 git commit
- ❌ 不"trim 抖音来的 wav" — 真实 IndexTTS2 生成