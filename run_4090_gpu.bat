@echo off
REM ============================================================
REM digital_human - RTX 4090 启动脚本 (ID-002 视觉导演 Agent 2.0)
REM
REM 设计参照: E:\AI\ComfyUI_windows_portable\run_nvidia_gpu.bat
REM
REM 本机存在两套 GPU 视角反序：
REM   nvidia-smi 视角:    GPU 0=4060 (8GB) / GPU 1=4090 (48GB)
REM   应用层 CUDA 视角:   CUDA0=4090 / CUDA1=4060
REM
REM PyTorch / faster-whisper / ctranslate2 走 CUDA 视角，
REM 所以 CUDA_VISIBLE_DEVICES=0 = RTX 4090
REM
REM 不设此环境变量 = 4060 8GB 容易 OOM 静默切 CPU offload
REM 跑 Whisper large-v3 float16 需要 ~10GB VRAM，4090 唯一选项
REM ============================================================

REM === 强制锚定 RTX 4090 ===
set CUDA_VISIBLE_DEVICES=0

REM === HF 离线（防止 faster-whisper 启动时打 HuggingFace Hub） ===
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1

REM === 切到项目根目录 ===
cd /d "%~dp0"

REM === 激活虚拟环境 ===
call .venv\Scripts\activate.bat

REM === 显示 GPU 锚定信息（友好提示）===
echo ============================================================
echo   digital_human - RTX 4090 启动
echo ============================================================
echo   CUDA_VISIBLE_DEVICES=0  (应用层 CUDA0 = RTX 4090, 48GB)
echo   HF_HUB_OFFLINE=1        (禁用 HuggingFace Hub 网络访问)
echo   端口:                   54321 (ID-002 视觉导演 Agent 2.0)
echo   工作目录:               %CD%
echo ============================================================
echo.

REM === 启动 uvicorn (前台运行,Ctrl+C 终止) ===
python -m uvicorn app.main:app --host 127.0.0.1 --port 54321 --log-level info

REM === 退出提示 ===
echo.
echo   uvicorn 已退出。
echo   如遇 CUDA OOM 请检查 CUDA_VISIBLE_DEVICES 是否生效。
echo.
pause