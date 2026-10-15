"""子进程注册表 — 按 job_id 跟踪外部子进程, 供 force-stop 杀进程树。

设计要点 (2026-08-08):
- 线程局部 job_id 上下文: 执行入口 set_current_job_id, finally 清空;
  子进程创建方无需显式传 job_id, register_subprocess 自动继承当前线程的 job_id。
- dict[job_id, list[Popen]] + Lock: kill 与 unregister 并发安全 (先 pop 再杀, 摘除幂等)。
- Windows 强杀: taskkill /F /T 杀整棵进程树 (ffmpeg → 编码线程 / npx → node → chromium)。
- 零业务依赖 (仅 stdlib), 供 infrastructure/ffmpeg.py 与 services 层单点引用。
"""
from __future__ import annotations

import logging
import platform
import subprocess
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_procs: dict[str, list[subprocess.Popen]] = {}

_current_job = threading.local()


def get_current_job_id() -> str | None:
    """返回当前线程绑定的 job_id (无则 None)."""
    return getattr(_current_job, "job_id", None)


def set_current_job_id(job_id: str | None) -> None:
    """绑定当前线程的 job_id 上下文. 执行入口调用, finally 置 None 清理."""
    _current_job.job_id = job_id


def register_subprocess(proc: subprocess.Popen, job_id: str | None = None) -> None:
    """登记一个子进程. 显式 job_id 优先, 否则用线程上下文; 无 job_id 则 no-op.

    非 director 上下文 (visual_render / TTS 等) 调用时自动 no-op, 不影响既有行为。
    """
    jid = job_id or get_current_job_id()
    if not jid:
        return
    with _lock:
        _procs.setdefault(jid, []).append(proc)


def unregister_subprocess(proc: subprocess.Popen, job_id: str | None = None) -> None:
    """摘除已结束/已杀的子进程. 空列表则 pop key, 防泄漏."""
    jid = job_id or get_current_job_id()
    if not jid:
        return
    with _lock:
        lst = _procs.get(jid)
        if lst and proc in lst:
            lst.remove(proc)
        if lst is not None and not lst:
            _procs.pop(jid, None)


def kill_job_procs(job_id: str) -> int:
    """杀掉 job 的全部已注册进程树. 先 pop 再杀, 摘除幂等.

    Returns:
        实际执行了 kill 的进程数 (已退出的跳过不计数).

    Windows 用 taskkill /F /T 杀整棵进程树 (subprocess.run timeout 只杀主进程,
    子进程继承管道会死锁). taskkill 失败仅 warning, 不抛出, 不阻塞状态恢复。
    """
    with _lock:
        procs = _procs.pop(job_id, None)
    if not procs:
        return 0

    is_win = platform.system() == "Windows"
    killed = 0
    for proc in procs:
        try:
            if proc.poll() is not None:
                continue  # 已自然退出, 无需杀
            if is_win:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                proc.kill()
            killed += 1
        except Exception as exc:
            logger.warning(
                "[proc_registry] kill pid=%s (job %s) failed: %s",
                getattr(proc, "pid", "?"), job_id, exc,
            )
    return killed


def active_job_ids() -> set[str]:
    """当前仍有注册子进程的 job 集合 (供 lifespan 清理与调试)."""
    with _lock:
        return set(_procs.keys())


def clear_all() -> None:
    """清空注册表 (lifespan 重启时调用). 不杀进程, 只丢弃内存引用."""
    with _lock:
        _procs.clear()
