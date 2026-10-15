# -*- coding: utf-8 -*-
"""空窗重启装 CSP · 三保险执行器 (Monitor 触发或人工运行均可)。

用法:
  python web/anim_rf/restart_csp.py               # enforce (正式)
  python web/anim_rf/restart_csp.py --mode report # report-only 探雷轮 (violation 只上报不拦截)
推荐首轮: --mode report 跑一轮确认 violation==0, 再默认 enforce。
回滚: 停服后 `ANIM_RF_CSP=0 python run_web.py` (中间件 env 开关, 见 app/main.py)。

三保险:
  1. 二次探活 — Monitor 通知到执行之间有窗口, 用户可能已起新任务; 有任务立即退出(重挂 Monitor)。
  2. 按当前端口持有者杀 PID — 启动时快照的 PID 可能已退出/被复用; netstat 实时取,
     并核对命令行确系 run_web.py, 只杀真正持有 54321 的进程。
  3. 浏览器实开验证 — curl -I 只证明头在, 不证明页面没被 CSP 打死; 用无头浏览器
     检测 CSP violation、跑两条交互路径、并确认旧页未误伤, 最后跑完整 smoke。

一句话结论的三条门禁 (全绿才算 pass, 缺一不是"生效+正常"):
  ① CSP 正生效: 新页带头 / 旧页无头; ② 无副作用: violation 事件数为 0 (不是"能打开");
  ③ 功能未坏: smoke PASS + 对齐试算/花字弹窗两条真实路径通。
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # digital_human/
BASE = "http://127.0.0.1:54321"
LOG = ROOT / ".tmp" / "run_web_csp.log"


def probe_running() -> int:
    """返回 running anim 任务数 (0 = 空窗)。"""
    with urllib.request.urlopen(f"{BASE}/api/anim/jobs/running", timeout=8) as r:
        import json
        return len(json.load(r).get("jobs", []))


def listener_pid() -> int | None:
    """当前持有 54321 的 PID (netstat 实时, 不信旧快照)。"""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if ":54321" in line and "LISTENING" in line:
            return int(line.split()[-1])
    return None


def pid_is_run_web(pid: int) -> bool:
    """核对命令行, 防 PID 复用误杀。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
        capture_output=True, text=True).stdout
    return "run_web.py" in out


def http_status(path: str) -> tuple[int, str, str]:
    req = urllib.request.Request(f"{BASE}{path}", method="HEAD")
    with urllib.request.urlopen(req, timeout=8) as r:
        return (r.status,
                r.headers.get("content-security-policy", ""),
                r.headers.get("content-security-policy-report-only", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["enforce", "report"], default="enforce",
                    help="enforce=正式拦截 (默认); report=只上报不拦截 (探雷轮)")
    mode = ap.parse_args().mode
    # ── 保险 1: 二次探活 ─────────────────────────────────────────
    n = probe_running()
    if n:
        print(f"[保险1] 仍有 {n} 个 running_job — 放弃本次, 请重挂 Monitor。")
        return 2
    print("[保险1] 二次探活通过: 无 running_job")

    # ── 保险 2: 实时取端口持有者并核对后按 PID 精确杀 ────────────
    pid = listener_pid()
    if pid is None:
        print("[保险2] 54321 已无人监听 (进程已自然退出) — 直接冷启动。")
    else:
        if not pid_is_run_web(pid):
            print(f"[保险2] PID {pid} 命令行不是 run_web.py — 拒绝击杀, 人工排查。")
            return 3
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        print(f"[保险2] 已按 PID 击杀 {pid} (命令行已核对)。")
        time.sleep(2)

    # ── 重启 (带模式 env, 日志落 .tmp) ──────────────────────────
    LOG.parent.mkdir(exist_ok=True)
    env = {**os.environ, "ANIM_RF_CSP": mode}
    with open(LOG, "ab") as lf:
        subprocess.Popen(
            [sys.executable, "run_web.py"], cwd=str(ROOT),
            stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    for _ in range(60):
        time.sleep(1)
        try:
            if probe_running() >= 0:
                break
        except Exception:
            pass
    else:
        print(f"[重启] 60s 未起来 — 看 {LOG}; 回滚: ANIM_RF_CSP=0 python run_web.py")
        return 4
    print(f"[重启] 后端已上线 (ANIM_RF_CSP={mode})。")

    # ── 门禁①: 头检查 (必要不充分) ─────────────────────────────
    want = "content-security-policy" if mode == "enforce" else "content-security-policy-report-only"
    st_new, h_new, h_new_ro = http_status("/web/anim.html")
    st_old, h_old, h_old_ro = http_status("/web/books.html")  # 任意非 anim 页: 证明 CSP 未外溢
    new_ok = bool(h_new if mode == "enforce" else h_new_ro)
    old_clean = not (h_old or h_old_ro)
    print(f"[门禁①] anim: {st_new} {want}={'✓' if new_ok else '缺失!'} | "
          f"旧页: {st_old} CSP={'误带!' if not old_clean else '无(正确)'}")
    if not (new_ok and old_clean):
        print("门禁① 未过 — 回滚: 停服后 ANIM_RF_CSP=0 python run_web.py")
        return 5

    # ── 门禁② + ③: 浏览器实开验证 + 完整 smoke ─────────────────
    # report 模式同样派发 securitypolicyviolation 事件 — 探雷轮即靠它确认零违规
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "verify_csp.py")],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    print(r.stdout.strip())
    if r.returncode != 0:
        print(f"[门禁②③] 浏览器验证失败 — 回滚: 停服后 ANIM_RF_CSP=0 python run_web.py; 日志 {LOG}")
        return 6

    r = subprocess.run([sys.executable, str(Path(__file__).parent / "smoke.py")],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(无输出)"
    print(f"[门禁③] {tail}")
    if r.returncode != 0:
        print(f"[门禁③] smoke 失败 — 回滚: 停服后 ANIM_RF_CSP=0 python run_web.py; 日志 {LOG}")
        return 7

    if mode == "report":
        print("结论[report 探雷轮]: violation=0 + 双页正常 — 可切正式: python web/anim_rf/restart_csp.py")
    else:
        print("结论: 三门禁全绿 (头对/violation=0/功能通) — CSP 生效 + 页面正常, 可进入替换上线阶段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
