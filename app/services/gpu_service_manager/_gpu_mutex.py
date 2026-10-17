# -*- coding: utf-8 -*-
"""独占显卡死命令 — llama 与 ComfyUI 互斥上卡 (1017 用户死命令).

规则: ComfyUI 和 llama 只允许独享显卡, 不允许两个同时上卡。
- ComfyUI (产线主力) 起批时: llama 若在 → 让位 (杀)。
- llama 拉起时: ComfyUI 若占卡 → 拒绝上卡 (调用方 fail-open 退路)。
判定口径: 大卡 (≥32GB) 显存占用 > 6GB = 有 AI 驻留 (comfy 模型栈或 llama
30B 都远超此阈; 桌面/杂项进程远低于此)。"""
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

_OCCUPIED_MB = 6000  # 大卡占用阈值 (MB)


def big_gpu_used_mb() -> int:
    """最大一张卡的显存占用 (MB); 查询失败返回 0 (宽进, 不炸产线)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        best = 0
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2:
                total, used = int(parts[0]), int(parts[1])
                if total >= 32000 and used > best:  # 只看大卡
                    best = used
        return best
    except Exception:  # noqa: BLE001 — nvidia-smi 卡死/缺失 → 不阻断
        return 0


def comfyui_holds_gpu() -> bool:
    """ComfyUI 占卡判定: 大卡占用超阈 (产线模型栈远超 6GB)."""
    return big_gpu_used_mb() > _OCCUPIED_MB


def kill_llama_for_comfyui() -> bool:
    """ComfyUI 起批前清 llama (产线优先让位)。返回是否清了。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process llama-server -ErrorAction SilentlyContinue | "
             "Stop-Process -Force"],
            capture_output=True, timeout=30)
        killed = r.returncode == 0
        if killed:
            logger.info("[gpu-mutex] llama 已让位 (ComfyUI 独享显卡, 1017 死命令)")
        return killed
    except Exception as exc:  # noqa: BLE001
        logger.warning("[gpu-mutex] 清 llama 失败 (尽力): %s", exc)
        return False
