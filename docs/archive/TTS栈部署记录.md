# TTS 栈部署记录（2026-07-22，M0 W1 D3 完成）

## 部署位置
| 组件 | 路径 | 模型 | 验证 |
|---|---|---|---|
| Fish Speech（s2-pro 4B，中文主力） | `E:\AI\tts\fish-speech\`（venv py3.11） | `E:\AI\tts\models\s2-pro\` | ✅ 8.6s 中文 wav 44.1kHz |
| F5-TTS（v1 Base，轻量备选） | `E:\AI\tts\F5-TTS\`（venv py3.11） | `E:\AI\tts\models\F5TTS_v1_Base\` + `vocos-mel-24khz\` | ✅ 6.8s 中文 wav 24kHz |
| ElevenLabs（英文 API） | — | — | ⏸ 需用户注册 API key |

冒烟脚本：`E:\AI\tts\fish-speech\smoke_fish.py`；产物 `C:\Users\tanha\tts_smoke\`

## 踩坑记录（复装必读）
1. **torch 必须钉 2.5.1+cu121**：`uv pip install -e .` 会把 torch 升级成 PyPI CPU 版（2.8.0+cpu）；cu121 index 无 2.8.0，cu126 的 download-r2 CDN 国内超时 → 用 `--reinstall-package torch --reinstall-package torchaudio torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121`（uv 缓存已有，秒装）。
2. **huggingface-hub 要 <1.0**（transformers 4.57 要求），装 0.36.2。
3. **protobuf 被 uv.lock override 钉 <6.0.0**（descript-audiotools 传递依赖），uv 会静默拒绝 protobuf 6.x；冲突表现是 tensorboard 2.21 gencode 6.31.1 > runtime 5.29.6 → **降 tensorboard==2.19.0** 解决。
4. **F5 vocoder（charactr/vocos-mel-24khz）**：HF 直连不通时，手工下 config.yaml + pytorch_model.bin，塞进 `~/.cache/huggingface/hub/models--charactr--vocos-mel-24khz/{refs/main→local01, snapshots/local01/}`，跑时 `HF_HUB_OFFLINE=1`。
5. **CLI 传路径必须 Windows 格式**（`E:\AI\...`），MSYS `/e/...` 会 FileNotFoundError；F5 的 --ref_audio 给相对路径会被拼上包目录，必须绝对路径。
6. 两个 uv 进程并行装同一 torch wheel 会锁冲突 → 串行或 `UV_LOCK_TIMEOUT=900`。
7. GitHub 直连全挂：`git config --global url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"` 已设全局，git+https 依赖自动走镜像；`GIT_TERMINAL_PROMPT=0` 防凭据弹窗。
