"""GPU 服务管理器主类 — 对外 API + 状态持有.

行为逐字迁移自原 gpu_service_manager.py (2026-08-08 包化重构).
"""
from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from app.services.gpu_service_manager._http import http_ok

# ── GPU-VPN 互斥 (2026-08-27 用户令) ─────────────────────────────────────
# 变色龙 VPN (C:\Program Files\Cham\Main.exe) 的虚拟网卡与显卡驱动冲突,
# 带 VPN 跑 GPU 会死机/重启。所有 GPU 会话启动前强制退出; 进程是 admin
# 权限, 普通 taskkill 杀不掉时抛错让用户手动断开 — 严禁带 VPN 起 GPU。
_VPN_PROC = "Main.exe"  # 变色龙主进程名


def vpn_running() -> int | None:
    """变色龙在跑 → PID; 没跑 → None.

    按进程名 Main 匹配 — admin 进程的 Path 对普通权限为空 (实测 PID 14812
    path=[] 导致 Path 过滤漏判), 宁可误判不可漏判: 名字命中即当 VPN。
    """
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name Main -ErrorAction SilentlyContinue "
         "| ForEach-Object { \"$($_.Id)|$($_.MainWindowTitle)\" }"],
        capture_output=True, text=True, timeout=30)
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if "|" in line:
            pid_s = line.split("|", 1)[0].strip()
            title = line.split("|", 1)[1].strip()
            if pid_s.isdigit() and (not title or "变色龙" in title):
                return int(pid_s)
    return None


def ensure_vpn_quiet(backend: str = "") -> None:
    """GPU 会话前置守卫: VPN 在跑则尝试强杀; 杀不掉 (admin) 硬抛错."""
    pid = vpn_running()
    if not pid:
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    import time as _t
    _t.sleep(1.5)
    if vpn_running():
        raise RuntimeError(
            f"⚠ GPU-VPN 互斥铁律: 变色龙 VPN (PID {pid}) 正在运行, 其虚拟网卡与显卡驱动"
            "冲突会死机/重启, 且进程为管理员权限无法自动退出 — 请手动断开/退出 VPN 后重试"
            f" (GPU 任务: {backend or '未知'})")

from app.services.gpu_service_manager._lifecycle import ServiceLifecycleMixin
from app.services.gpu_service_manager._specs import NO_LOCAL_SERVICE, ServiceSpec

logger = __import__("logging").getLogger(__name__)

StatusCallback = Callable[[str], None]

__all__ = ["GPUServiceManager", "_ServiceState"]


@dataclass
class _ServiceState:
    proc: subprocess.Popen | None = None  # 本管理器拉起的进程 (None=未管理)
    last_used: float = 0.0
    starting: bool = False


class GPUServiceManager(ServiceLifecycleMixin):
    """单 GPU 串行调度: 排队 → 确保服务在线 → 执行 → 空闲自动关停."""

    def __init__(
        self,
        specs: dict[str, ServiceSpec],
        idle_timeout_sec: float = 300.0,
        startup_timeout_sec: float = 300.0,
        auto_manage: bool = True,
    ):
        self.specs = specs
        self.idle_timeout_sec = idle_timeout_sec
        self.startup_timeout_sec = startup_timeout_sec
        self.auto_manage = auto_manage

        self._gpu_lock = threading.Lock()  # 全局串行锁 (排队)
        self._state_lock = threading.Lock()  # 保护 _states/_waiting
        self._states: dict[str, _ServiceState] = {k: _ServiceState() for k in specs}
        self._waiting = 0  # 当前排队任务数
        self._active_key: str | None = None  # 正在执行任务的服务
        self._watchdog: threading.Thread | None = None

    # ── 对外主入口 ────────────────────────────────────────────

    @contextmanager
    def session(self, backend: str, status_callback: StatusCallback | None = None):
        """排队获取 GPU → VPN 查杀 → 确保 backend 服务在线 → 执行 → 释放.

        backend 不是本地 GPU 服务 (如 elevenlabs) 时直接放行.
        服务启动失败抛 RuntimeError, 超时抛 TimeoutError.
        2026-08-27 用户令: 起 GPU 前强制退出本地 VPN(变色龙) — 虚拟网卡与
        显卡驱动冲突会死机/重启; 进程 admin 权限杀不掉时抛错让用户手动断开。
        """
        if backend in NO_LOCAL_SERVICE or backend not in self.specs:
            yield
            return
        if not self.auto_manage:
            # 未启用托管: 保持旧行为 (直接调用, 服务不在线由调用方报错)
            yield
            return

        ensure_vpn_quiet(backend)

        notify = status_callback or (lambda msg: None)

        with self._state_lock:
            self._waiting += 1
            queue_pos = self._waiting
        if queue_pos > 1 or self._gpu_lock.locked():
            notify("GPU 排队中 (前面还有任务)…")

        self._gpu_lock.acquire()
        try:
            with self._state_lock:
                self._waiting -= 1
                self._active_key = backend
            self._clear_gpu_neighborhood(backend, notify)
            self._ensure_running(backend, notify)
            self._touch(backend)
            yield
        finally:
            self._touch(backend)
            with self._state_lock:
                self._active_key = None
            self._gpu_lock.release()
            if self._effective_idle(backend) <= 0:
                # 立即释放显存模式 (本服务阈值为 0 时; 0917 起按服务独立判定)
                self._stop_if_idle(backend, force=True)
            else:
                self._start_watchdog()

    # ── 看门狗 ───────────────────────────────────────────────

    def _touch(self, key: str) -> None:
        self._states[key].last_used = time.time()

    # ── 跨族清场 (0919 ep4 实锤: K2 批结束后模型栈驻留 39G, TTS 叠上跑 10.4s/步
    # → 300s 超时整 job "timed out"; _ensure_running 的互斥只在"目标服务未起"时
    # 触发, 健康快返回 + 外部拉起的 ComfyUI 是盲区) ─────────────────────────
    # 初版双 bug 修复: specs 值是 ServiceSpec 对象不是 dict (spec.get() AttributeError
    # 被 except 静默吞 = 清场从未执行, 用户实锤"没起作用"); TTS 族从 specs 动态取
    # (漏 indextts25 同源病)。
    def _clear_gpu_neighborhood(self, backend: str, notify: Callable[[str], None]) -> None:
        """起本族服务前清掉异族驻留: TTS 起时卸 ComfyUI 模型栈 (POST /free, 进程
        保温零重启); ComfyUI 起时停托管 TTS (小栈重启廉价)。失败静默 (清场尽力)。"""
        try:
            tts_keys = set(self.specs) - {"comfyui"}
            if backend in tts_keys and "comfyui" in self.specs:
                url = self.specs["comfyui"].base_url.rstrip("/")
                if url:
                    import httpx

                    with httpx.Client(timeout=30.0) as cli:
                        r = cli.post(f"{url}/free",
                                     json={"unload_models": True, "free_memory": True})
                    if r.status_code in (200, 204):
                        notify("GPU 清场: 已卸载 ComfyUI 驻留模型栈 (腾显存)")
                        logger.info("[gpu_svc] 清场 ✓ ComfyUI /free (TTS 上卡前)")
            elif backend == "comfyui":
                for key in tts_keys:
                    st = self._states.get(key)
                    if st is not None and st.proc is not None:
                        notify(f"GPU 清场: 停 {self.specs[key].display_name} 腾显存…")
                        self._stop_service(key)
        except Exception:  # noqa: BLE001 — 清场尽力, 不挡会话
            logger.warning("[gpu_svc] 跨族清场失败 (忽略)", exc_info=True)

    def _effective_idle(self, key: str) -> float:
        """本服务的空闲关停阈值: spec 覆盖优先, 否则全局 (0917 ComfyUI 单独保温)."""
        spec_idle = self.specs.get(key).idle_timeout_sec if key in self.specs else None
        return spec_idle if spec_idle is not None else self.idle_timeout_sec

    # ── 状态与手动控制 ────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            services = {}
            for key, spec in self.specs.items():
                state = self._states[key]
                healthy = http_ok(spec.health_url, timeout=1.5)
                services[key] = {
                    "display_name": spec.display_name,
                    "base_url": spec.base_url,
                    "healthy": healthy,
                    "managed": state.proc is not None and state.proc.poll() is None,
                    "starting": state.starting,
                    "idle_sec": round(time.time() - state.last_used, 1) if state.last_used else None,
                }
            return {
                "auto_manage": self.auto_manage,
                "idle_timeout_sec": self.idle_timeout_sec,
                "busy": self._gpu_lock.locked(),
                "active_backend": self._active_key,
                "queue_length": self._waiting,
                "services": services,
            }

    def stop_all(self) -> list[str]:
        """手动停止所有托管服务 (外部启动的不动)."""
        stopped = []
        for key in list(self._states):
            if self._states[key].proc is not None:
                self._stop_service(key)
                stopped.append(key)
        return stopped

    def shutdown(self) -> None:
        """应用退出钩子: 杀掉所有托管进程, 不留孤儿占显存."""
        self.stop_all()
