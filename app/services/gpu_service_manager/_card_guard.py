# -*- coding: utf-8 -*-
"""卡级预检 — 起钉卡服务前清目标卡显存 (1023 三卡产线用户令).

与族级清场 (_clear_gpu_neighborhood, 管"服务族"间让位) 互补: 本模块按
**目标卡**查 nvidia-smi compute-apps, 逐进程裁决:
  - 自家托管进程 (manager 追踪的 pid):
      属本服务 → 跳过 (ensure 层决定复用/重启);
      属其他 spec → 停 (让位, 同卡只许一个钉主)。
  - 后端进程自身 → 告警不自杀 (批E whisper 隔离前的遗留形态会自愈)。
  - 外来进程 → 告警不杀 (fail-open, 0919 "外部拉起的 ComfyUI 盲区"至少有日志)。
未钉卡 spec (env 无 CUDA_VISIBLE_DEVICES=GPU-<uuid>) → 预检跳过 (无目标卡)。
清后复测目标卡 used, 仍超阈 → notify 告警 (调用方决策继续/中止)。
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = ["card_preflight"]

_WARN_MB = 4000  # 清后目标卡仍超此值 = 有东西没让干净


def _target_uuid(spec: Any) -> str | None:
    """spec.env CUDA_VISIBLE_DEVICES 的 GPU-<uuid> 形态; 数字序号/未钉 → None."""
    if spec is None:
        return None
    v = (spec.env or {}).get("CUDA_VISIBLE_DEVICES", "")
    return v if v.startswith("GPU-") else None


def _compute_apps() -> list[tuple[str, int, str]]:
    """nvidia-smi compute-apps → [(gpu_uuid, pid, process_name)]; 失败 []."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        apps = []
        for ln in out.splitlines():
            parts = [p.strip() for p in ln.split(",", 2)]
            if len(parts) == 3 and parts[1].isdigit():
                apps.append((parts[0], int(parts[1]), parts[2]))
        return apps
    except Exception:  # noqa: BLE001 — nvidia-smi 卡死/缺失 → 不阻断 (尽力)
        return []


def _used_mb(uuid: str) -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,memory.used",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        for ln in out.splitlines():
            parts = [p.strip() for p in ln.split(",", 1)]
            if len(parts) == 2 and parts[0] == uuid:
                return int(parts[1])
    except Exception:  # noqa: BLE001
        pass
    return 0


def card_preflight(
    backend: str,
    spec: Any,
    *,
    tracked_pids: dict[int, str],
    stop_service: Callable[[str], None],
    notify: Callable[[str], None],
) -> bool:
    """目标卡预清. 返回 True=卡干净/已清完, False=清后仍被占 (已告警).

    tracked_pids: {pid: spec_key} 管理器追踪的活进程; stop_service: 优雅停指定
    spec (托管路径, 走原 _stop_service 的进程树/端口清理)。
    """
    uuid = _target_uuid(spec)
    if not uuid:
        return True  # 未钉卡 → 无预检义务 (老行为)
    apps = [(pid, name) for u, pid, name in _compute_apps() if u == uuid]
    if not apps:
        return True
    for pid, name in apps:
        owner = tracked_pids.get(pid)
        if owner == backend:
            continue  # 本服务已在岗 (健康复用归 ensure 层判断)
        if owner is not None:
            notify(f"GPU 预清: 停 {owner} (与 {backend} 同卡让位)")
            logger.info("[gpu_svc] 卡级预清: pid=%s 属 %s, 为 %s 让位 (卡 %s…)",
                        pid, owner, backend, uuid[:16])
            try:
                stop_service(owner)
            except Exception as exc:  # noqa: BLE001 — 停失败兜底强杀
                logger.warning("[gpu_svc] 停 %s 失败, 兜底强杀 pid=%s: %s",
                               owner, pid, exc)
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                    capture_output=True, timeout=30)
        elif pid == os.getpid():
            notify("⚠ 后端进程自身占用目标卡 (whisper 遗留? 批E隔离后应自愈)")
            logger.warning("[gpu_svc] 后端 pid=%s 占用目标卡 %s… — 不自杀, 告警",
                           pid, uuid[:16])
        else:
            notify(f"⚠ 目标卡有外来进程 pid={pid} ({name[-40:]}) — 未杀, 人工确认")
            logger.warning("[gpu_svc] 外来进程 pid=%s name=%s 占目标卡 %s… (fail-open)",
                           pid, name[-60:], uuid[:16])
    import time
    time.sleep(1.5)  # 进程退出回收显存需要一瞬间
    used = _used_mb(uuid)
    if used > _WARN_MB:
        notify(f"⚠ 目标卡清后仍占 {used}MB (>{_WARN_MB}MB) — 可能残留")
        logger.warning("[gpu_svc] 目标卡 %s… 清后仍占 %dMB", uuid[:16], used)
        return False
    logger.info("[gpu_svc] 卡级预清 ✓ %s → 卡 %s… 干净 (%dMB)",
                backend, uuid[:16], used)
    return True
