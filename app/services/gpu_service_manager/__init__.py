"""GPU 服务生命周期管理器 — 按需启动 / 排队执行 / 空闲自动关闭.

背景:
  本地单卡 4090 要轮流跑 Fish/F5/IndexTTS2 + ComfyUI(LTX) 多个显存大户.
  TTS 服务不能常驻 — 音频生成结束后必须腾出显存给后续视频渲染.

策略:
  - session(backend): 全局 GPU 锁(排队) + 确保目标服务健康(不在线则拉起)
  - 拉起某服务前, 先停掉本管理器启动的其他 TTS 服务(腾显存)
  - 外部手动启动的服务只用不杀(managed=False), 避免误杀
  - 看门狗线程: 空闲 > idle_timeout_sec 自动 kill 进程树
  - idle_timeout_sec = 0 → session 结束立即关闭
"""
from __future__ import annotations

import threading

from app.services.gpu_service_manager._manager import GPUServiceManager
from app.services.gpu_service_manager._specs import ServiceSpec

# ── 模块级单例 ────────────────────────────────────────────────
# 定义在本命名空间: main.py 以 `gpu_service_manager._manager` 直接访问同一全局.
_manager: GPUServiceManager | None = None
_manager_lock = threading.Lock()


def get_gpu_service_manager() -> GPUServiceManager:
    """惰性单例; 配置读 config/app.yaml `tts_services` 节 (可缺省)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            from ...config import get_config

            from app.services.gpu_service_manager._specs import build_specs

            raw = get_config().raw
            specs, svc_cfg = build_specs(raw)
            _manager = GPUServiceManager(
                specs=specs,
                idle_timeout_sec=float(svc_cfg.get("idle_timeout_sec", 300)),
                startup_timeout_sec=float(svc_cfg.get("startup_timeout_sec", 300)),
                auto_manage=bool(svc_cfg.get("auto_manage", True)),
            )
        return _manager


__all__ = [
    "GPUServiceManager",
    "ServiceSpec",
    "get_gpu_service_manager",
]
