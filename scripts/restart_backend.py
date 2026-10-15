# -*- coding: utf-8 -*-
"""安全重启后端 — running job 存在时拒绝重启 (0915 两次撞杀在跑任务的事故根治).

用法:
  .venv/Scripts/python.exe scripts/restart_backend.py           # 安全模式 (默认)
  .venv/Scripts/python.exe scripts/restart_backend.py --force   # 强制 (会杀掉在跑任务)

规则: 后端重启一律走本脚本, 禁止裸 powershell Stop-Process。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:54321"
GUARD_URL = f"{BASE}/api/anim/jobs/running"


def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _port_owner(port: int) -> int | None:
    """跨平台: 占用 PORT 的进程 PID; 查不到返回 None."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
             f"| Select-Object -ExpandProperty OwningProcess -Unique | Select-Object -First 1)"],
            text=True, timeout=10).strip()
        return int(out) if out else None
    except Exception:
        return None


def _parent_pid(pid: int) -> int | None:
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId"],
            text=True, timeout=10).strip()
        return int(out) if out else None
    except Exception:
        return None


def main() -> None:
    force = "--force" in sys.argv

    # 1) 守卫: 有 job 在跑就拒绝 (除非 --force)
    jobs: list = []
    try:
        jobs = _get(GUARD_URL).get("jobs") or []
    except Exception:
        print("(后端不在线或守卫端点不存在 — 旧进程, 继续重启)")
    if jobs and not force:
        for j in jobs:
            prog = j.get("progress") or {}
            print(f"⛔ 拒绝重启 — job 运行中: {j.get('phase')}{(' ' + j['arc']) if j.get('arc') else ''} "
                  f"{str(j.get('book_id') or '')[:8]} ep{j.get('ep')} "
                  f"{prog.get('done', '?')}/{prog.get('total', '?')} {prog.get('current', '')}")
        print("   等它完成或取消后再重启; 确认要杀掉在跑任务用 --force")
        sys.exit(2)

    # 2) 杀旧后端: 命令行匹配杀 + 端口归属杀 (双保险, 0920 事故: CIM 杀漏 →
    #    旧进程占 54321, 新进程 10048 绑定失败退出, 健康检查打到旧进程报假成功)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' "
         "-and $_.CommandLine -match 'uvicorn app.main' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True)
    time.sleep(2)
    stale = _port_owner(54321)
    if stale:
        print(f"→ 命令行杀漏网, 按端口归属补杀 PID={stale}")
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {stale} -Force"], capture_output=True)
    # 等端口真正释放 (大应用退出慢, 抢跑=新进程 10048; 0920 实锤 2s 不够)
    for i in range(15):
        stale = _port_owner(54321)
        if not stale:
            break
        if i == 7:  # 中途再补一刀, 处理退出卡死
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Stop-Process -Id {stale} -Force"], capture_output=True)
        time.sleep(1)
    if stale:
        print(f"⛔ 端口 54321 仍被 PID={stale} 占用且杀不掉 — 新进程必然绑定失败, 终止")
        sys.exit(3)

    # 3) 拉新后端 (日志追加)
    with open("logs/uvicorn_54321.log", "ab") as lf:
        proc = subprocess.Popen(
            [r".venv\Scripts\python.exe", "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "54321", "--log-level", "info"],
            stdout=lf, stderr=subprocess.STDOUT, cwd=".")

    # 4) 等健康 + 端口归属核验 (防假成功: 健康响应必须来自新进程)
    for _ in range(25):
        time.sleep(1)
        try:
            _get(GUARD_URL, timeout=2)
        except Exception:
            continue
        owner = _port_owner(54321)
        # venv trampoline: Popen 拿到的是跳板进程, 真监听者是其子进程 (0920 实锤:
        # 误判假成功 → 连环误杀好后端)。归属 = 新进程本人或其子进程都算接管成功。
        if owner is not None and owner != proc.pid and _parent_pid(owner) != proc.pid:
            print(f"⛔ 假成功拦截 — 健康端点来自无关旧进程 PID={owner} (新进程 {proc.pid} 未接管端口), "
                  f"手动处理: Stop-Process -Id {owner} -Force 后重跑本脚本")
            sys.exit(4)
        print(f"✅ 后端已安全重启 (PID={proc.pid}, 守卫端点在线, 端口归属已核验)")
        return
    print("⚠ 后端 25s 内未就绪 — 查 logs/uvicorn_54321.log")
    sys.exit(1)


if __name__ == "__main__":
    main()
