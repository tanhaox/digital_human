"""HTTP 健康检查 + Windows 端口进程探测工具."""
from __future__ import annotations

import os
import subprocess
import urllib.request
from typing import Any

__all__ = ["http_ok", "port_of", "pids_listening_on", "proc_cmdline"]


def http_ok(url: str, timeout: float = 3.0) -> bool:
    """GET url, 2xx/3xx 视为健康."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def port_of(base_url: str) -> int | None:
    from urllib.parse import urlparse

    return urlparse(base_url).port


def pids_listening_on(port: int) -> list[int]:
    """Windows: netstat -ano 找出 LISTENING 在该端口上的 PID."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return []
    pids: set[int] = set()
    suffix = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        # TCP  本地地址:端口  远程地址  LISTENING  PID
        if len(parts) >= 5 and parts[0] == "TCP" and parts[1].endswith(suffix):
            if "LISTEN" in parts[3].upper():
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    return sorted(pids)


def proc_cmdline(pid: int) -> str:
    """Windows: 按 PID 取进程完整命令行 (取证/验明正身); 失败或已退出返回 ''."""
    if os.name != "nt" or pid <= 0:
        return ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, timeout=15,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""
