"""视频打标 — llama-server 自动拉起 (健康检查 / 启动 / 端口清理)."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import urllib.request
from typing import Any

from app.services.video_tagging_service.constants import (
    _LLAMA_HEALTH_URL,
    _LLAMA_HOST,
    _LLAMA_MMPROJ_PATH,
    _LLAMA_MODEL_PATH,
    _LLAMA_PORT,
    _LLAMA_READY_TIMEOUT_SEC,
    _LLAMA_SERVER_EXE,
)

logger = logging.getLogger(__name__)

__all__ = ["_ensure_llama_server"]

_LLAMA_PROC: subprocess.Popen | None = None
_LLAMA_PROC_LOCK = threading.Lock()


def _is_llama_running() -> bool:
    """快速健康检查: llama-server 是否已就绪."""
    try:
        req = urllib.request.Request(_LLAMA_HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _kill_port_owner(port: int) -> None:
    """Windows: 杀掉占用指定端口的进程."""
    if os.name != "nt":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                subprocess.run(
                    ["taskkill", "/PID", pid, "/F"],
                    capture_output=True, timeout=10,
                )
                logger.info("[llama] 已清理端口 %d 占用 (PID %s)", port, pid)
                time.sleep(1)
                break
    except Exception:
        pass


def _spawn_llama_server() -> bool:
    """启动 llama-server 子进程 (返回值仅表示 Popen 是否成功)."""
    if not _LLAMA_SERVER_EXE.is_file():
        logger.error("llama-server.exe 不存在: %s", _LLAMA_SERVER_EXE)
        return False
    if not _LLAMA_MODEL_PATH.is_file():
        logger.error("模型文件不存在: %s", _LLAMA_MODEL_PATH)
        return False

    _kill_port_owner(_LLAMA_PORT)

    cmd = [
        str(_LLAMA_SERVER_EXE),
        "-m", str(_LLAMA_MODEL_PATH),
        "--mmproj", str(_LLAMA_MMPROJ_PATH),
        "--alias", "qwen3vl-30b",
        "--temp", "0.3", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.00",
        "--port", str(_LLAMA_PORT), "--host", _LLAMA_HOST,
        "-ngl", "999", "-c", "131072", "-b", "4096", "-ub", "4096",
        # 1012 实弹坑: Qwen3-VL 视觉编码器 + FA = CUDA 非法访存崩溃, 必须 fa off;
        # 大图 non-causal 要求 n_ubatch >= 图像token数, 512 会 assert 崩 (4096 实测稳)
        "-fa", "off", "-ctk", "f16", "-ctv", "f16", "--device", "CUDA0",
        "--parallel", "4",
        "--sleep-idle-seconds", "600",
        "-lv", "1",
    ]

    logger.info("[llama] 正在启动 llama-server: %s", cmd[:4])

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        _spawn_popen(cmd, creationflags)
        return True
    except Exception as exc:
        logger.error("[llama] 启动失败: %s", exc)
        return False


def _spawn_popen(cmd: list[str], creationflags: int) -> None:
    """Popen 启动 llama-server (失败抛出由调用方收敛)."""
    global _LLAMA_PROC

    _LLAMA_PROC = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _wait_llama_ready() -> bool:
    """轮询健康检查直至就绪, 超时置 _LLAMA_PROC=None."""
    for i in range(_LLAMA_READY_TIMEOUT_SEC):
        time.sleep(1)
        if _is_llama_running():
            logger.info("[llama] llama-server 就绪 (耗时 ~%ds)", i + 1)
            return True

    logger.error("[llama] llama-server 启动超时 (%ds)", _LLAMA_READY_TIMEOUT_SEC)
    _reset_proc()
    return False


def _reset_proc() -> None:
    """清空全局 llama 进程句柄."""
    global _LLAMA_PROC

    _LLAMA_PROC = None


def _ensure_llama_server() -> bool:
    """确保 llama-server 正在运行；未运行则自动拉起.

    2026-08-27 用户令: llama 占 4090, 拉起前走 GPU-VPN 互斥守卫
    (变色龙在跑 → 尝试杀 → 杀不掉返回 False, 由调用方提示手动断开)。
    """
    if _is_llama_running():
        return True

    try:
        from app.services.gpu_service_manager._manager import ensure_vpn_quiet
        ensure_vpn_quiet("llama-server")
    except RuntimeError as exc:
        print(f"[llama] {exc}")
        return False

    with _LLAMA_PROC_LOCK:
        if _is_llama_running():
            return True
        if not _spawn_llama_server():
            return False

    return _wait_llama_ready()
