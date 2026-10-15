"""GPU 服务启停 + 看门狗 — 生命周期 mixin.

由 GPUServiceManager 混入, 持有 self.specs / self._states / self.startup_timeout_sec.
行为逐字迁移自原 gpu_service_manager.py (2026-08-08 包化重构).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from app.services.gpu_service_manager._http import (
    http_ok,
    pids_listening_on,
    port_of,
    proc_cmdline,
)
from app.services.gpu_service_manager._specs import ServiceSpec

if TYPE_CHECKING:
    from app.services.gpu_service_manager._manager import _ServiceState

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = PROJECT_ROOT / "logs"

StatusCallback = Callable[[str], None]

__all__ = ["ServiceLifecycleMixin"]


class ServiceLifecycleMixin:
    """服务启停: 确保在线 / 启动 / 停止 / 强杀端口 / 看门狗空闲回收."""

    def _ensure_running(self, key: str, notify: StatusCallback) -> None:
        spec = self.specs[key]
        state = self._states[key]

        if http_ok(spec.health_url):
            if state.proc is None:
                logger.info("[gpu_svc] %s 已由外部启动, 直接复用", spec.display_name)
            return

        # 进程还在但健康检查失败 (启动中途/僵死) → 先杀干净
        if state.proc is not None and state.proc.poll() is None:
            self._kill_proc_tree(state.proc, spec)
            state.proc = None

        # 腾显存: 停掉本管理器启动的其他服务
        for other_key, other_state in self._states.items():
            if other_key != key and other_state.proc is not None:
                notify(f"正在关闭 {self.specs[other_key].display_name} 以腾出显存…")
                self._stop_service(other_key)

        # 霸道清场 (2026-08-07): 强杀占用目标端口的外部进程。
        # 场景: 手动启动的 api_server (PID 由外部拉起, managed=False, 管理器不认) 或残留僵尸进程
        # 占着 7862 → 若不杀, 启动会 bind 失败或直接复用旧进程。按端口杀精确且安全:
        # 人工网页端在 7861 (webui.py), 与托管 7862 不同端口, 不会误杀。
        self._force_free_port(spec)

        notify(f"正在启动 {spec.display_name} 服务 (首次加载模型约 1-2 分钟)…")
        self._await_ready(key, spec, state, notify)

    def _await_ready(
        self,
        key: str,
        spec: ServiceSpec,
        state: _ServiceState,
        notify: StatusCallback,
    ) -> None:
        """启动进程后轮询健康直到就绪; 失败抛 RuntimeError, 超时抛 TimeoutError."""
        state.proc = self._launch(spec)
        state.starting = True
        try:
            deadline = time.time() + self.startup_timeout_sec
            while time.time() < deadline:
                if state.proc.poll() is not None:
                    raise RuntimeError(
                        f"{spec.display_name} 进程启动即退出 (exit={state.proc.returncode}), "
                        f"日志: logs/gpu_svc_{key}.log"
                    )
                if http_ok(spec.health_url):
                    notify(f"{spec.display_name} 服务就绪")
                    logger.info("[gpu_svc] %s ready at %s", key, spec.base_url)
                    return
                time.sleep(2.0)
            # 超时: 杀掉半启动进程, 避免残留占显存
            self._kill_proc_tree(state.proc, spec)
            state.proc = None
            raise TimeoutError(
                f"{spec.display_name} 启动超时 ({self.startup_timeout_sec:.0f}s), "
                f"日志: logs/gpu_svc_{key}.log"
            )
        finally:
            state.starting = False

    def _launch(self, spec: ServiceSpec) -> subprocess.Popen:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"gpu_svc_{spec.key}.log"
        log_fh = open(log_path, "ab")
        env = dict(os.environ)
        # 0920 根治: 后端进程内 Whisper 钉卡 (alignment_service) 会把
        # CUDA_DEVICE_ORDER=PCI_BUS_ID 留在本进程环境; 子进程原样继承后,
        # spec 的序号锚定语义被改写 (PCI 序 0=4060) → ComfyUI 落 8GB 卡崩溃环.
        # spec.env 是 GPU 钉卡的唯一事实源, 继承链上的钉卡/排序变量一律剥离.
        for leaked in ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"):
            env.pop(leaked, None)
        env.update(spec.env)
        # 子进程 UTF-8 输出, 避免 fish 的 GBK UnicodeEncodeError
        env.setdefault("PYTHONIOENCODING", "utf-8")
        cmd = list(spec.command)
        # 相对命令基于服务 cwd 解析
        exe = Path(cmd[0])
        if not exe.is_absolute():
            cmd[0] = str(spec.cwd / exe)
        logger.info("[gpu_svc] launching %s: %s | CUDA_VISIBLE_DEVICES=%s", spec.key, cmd,
                    env.get("CUDA_VISIBLE_DEVICES", "<unset>"))
        # 0917 取证锚 → 0920 结案: "值=0 而落 4060" 的翻转源 = 后端进程被
        # Whisper 钉卡写入 CUDA_DEVICE_ORDER=PCI_BUS_ID 后被子进程继承 (已在
        # 上方剥离); comfyui spec 已改锚 GPU-UUID, 与枚举序彻底解耦.
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(
            cmd,
            cwd=str(spec.cwd),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    def _stop_service(self, key: str) -> None:
        state = self._states[key]
        if state.proc is None:
            return
        self._kill_proc_tree(state.proc, self.specs[key])
        state.proc = None
        self._force_free_port(self.specs[key])
        logger.info("[gpu_svc] %s stopped, VRAM released", key)

    @staticmethod
    def _force_free_port(spec: ServiceSpec) -> None:
        """兜底: 进程树杀完后端口仍被占用 → 按端口找 PID 强杀.

        场景: 服务派生的孙进程脱离了进程树 (taskkill /T 覆盖不到),
        或前次残留的僵尸进程占着端口导致下次启动 bind 失败.

        安全红线 (2026-08-07 立, 0917 修订"验明正身才杀"):
          - 54321 run_web.py / 7861 人工网页端 — 绝对红线, 永不强杀;
          - 8188 ComfyUI — 旧红线在本管理器要拉起 comfyui 时反而锁死自己:
            服务端被强杀后 ComfyUI 孤儿僵死占口 (监听但不响应), 健康检查
            失败 → 拉新实例 → bind 失败"启动即退出"死循环. 现改为: 命令行
            验明是 ComfyUI main.py 进程才清 (僵死孤儿), 其他进程照旧不动;
          - 托管 TTS 端口 (7860/7862/7866) 不设红线, 照旧清场.
        """
        port = port_of(spec.base_url)
        if not port or os.name != "nt":
            return

        def _killable(pid: int) -> bool:
            if port in (54321, 7861):  # 本项目/人工网页端: 绝对红线
                return False
            if port == 8188:  # ComfyUI 口: 验明正身才杀
                cmdline = proc_cmdline(pid)
                ok = ("comfyui" in cmdline.lower() and "main.py" in cmdline.lower())
                if not ok:
                    logger.error(
                        "[gpu_svc] port 8188 held by NON-ComfyUI PID %s "
                        "(cmdline: %.100s) — 红线不动, 启动可能 bind 失败需人工排查",
                        pid, cmdline or "<查询失败/已退出>")
                return ok
            return True

        pids: list[int] = []
        for _ in range(5):
            pids = pids_listening_on(port)
            pids = [p for p in pids if p not in (0, os.getpid())]
            if not pids:
                return
            killed_any = False
            for pid in pids:
                if not _killable(pid):
                    continue
                logger.warning(
                    "[gpu_svc] port %s still held by PID %s after kill, force taskkill",
                    port, pid,
                )
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, timeout=30,
                    )
                    killed_any = True
                except Exception as exc:
                    logger.warning("[gpu_svc] force kill PID %s failed: %s", pid, exc)
            if not killed_any:
                break  # 剩下的全是红线保护对象, 等待无意义
            time.sleep(1.0)
        if pids_listening_on(port):
            logger.error("[gpu_svc] port %s STILL occupied after force kill", port)

    @staticmethod
    def _kill_proc_tree(proc: subprocess.Popen, spec: ServiceSpec) -> None:
        """Windows 下用 taskkill /T 杀整棵进程树 (uv/venv 会派生子进程)."""
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=30,
                )
            else:
                proc.terminate()
            proc.wait(timeout=30)
        except Exception as exc:
            logger.warning("[gpu_svc] kill %s failed: %s", spec.key, exc)

    # ── 看门狗 ───────────────────────────────────────────────

    def _stop_if_idle(self, key: str, force: bool = False) -> None:
        state = self._states[key]
        if state.proc is None:
            return
        idle = time.time() - state.last_used
        if force or idle >= self._effective_idle(key):  # 0917: 按服务独立阈值
            # 有任务在跑/排队时不关
            if self._gpu_lock.locked() or self._waiting > 0:
                return
            self._stop_service(key)

    def _start_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return

        def _loop():
            while True:
                time.sleep(15.0)
                try:
                    for key in list(self._states):
                        self._stop_if_idle(key)
                except Exception as exc:
                    logger.warning("[gpu_svc] watchdog error: %s", exc)
                # 全部服务已停且无任务 → 线程退出
                with self._state_lock:
                    if (
                        all(s.proc is None for s in self._states.values())
                        and self._waiting == 0
                    ):
                        return

        self._watchdog = threading.Thread(
            target=_loop, daemon=True, name="gpu-svc-watchdog"
        )
        self._watchdog.start()
