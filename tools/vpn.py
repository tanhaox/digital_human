# -*- coding: utf-8 -*-
"""tools.vpn — mihomo (Clash Meta) 代理托管 (2026-08-27 v2, 替换变色龙).

v1 教训 (变色龙): GUI 开关坐标点击/admin 杀不掉/虚拟网卡与显卡驱动冲突死机 —
用户裁决直接换内核。mihomo 优势:
  - 纯命令行: 启动=spawn, 停止=kill pid (无需 admin), 零点击
  - 代理模式无虚拟网卡 → 与 GPU 互斥问题根因消失 (仍保留检查作保险)
  - REST 控制口 9090: 节点/延迟/切换全 API 可控

部署: E:/AI/mihomo/{mihomo.exe, config.yaml}; 订阅链接填 config.yaml 的
SUB_URL_PLACEHOLDER (机场买的 Clash 订阅)。

用法 (CLI):
  python -m tools.vpn status
  python -m tools.vpn start          # 启动代理 (幂等)
  python -m tools.vpn stop           # 停止
  python -m tools.vpn restart
用法 (代码):
  from tools.vpn import ensure_proxy, PROXY, proxy_env, with_proxy
  ensure_proxy()                     # 未启动则启动, 已启动直接用
  requests.get(url, proxies=proxy_env())
  yt-dlp --proxy http://127.0.0.1:7890 ...

⚠ 旧变色龙 (Cham/Main.exe): 已弃用。若在跑且 mihomo 要起, 端口不冲突
(9876 vs 7890) 可共存, 但建议关掉省心。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MIHOMO_DIR = Path("E:/AI/mihomo")
MIHOMO_EXE = MIHOMO_DIR / "mihomo.exe"
PROXY = "http://127.0.0.1:7890"
CTRL = "http://127.0.0.1:9090"
PROBE_URL = "https://www.gstatic.com/generate_204"

# 保险: 与 GPU 大任务的粗互斥提示位 (代理模式无虚拟网卡, 风险根因已除)
GPU_PORTS = {8080: "llama-server", 8188: "comfyui"}


def proxy_env() -> dict:
    return {"http": PROXY, "https": PROXY}


def _pid() -> int | None:
    """mihomo 进程 PID (找命令行含 mihomo 的 python/exe)."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'mihomo.exe'} "
         "| ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True, timeout=30)
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def _probe(timeout: int = 8) -> bool:
    """经代理探外网."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_env()))
    try:
        with opener.open(PROBE_URL, timeout=timeout) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def _ctrl(path: str) -> dict | None:
    """REST 控制口查询 (节点/延迟)."""
    try:
        with urllib.request.urlopen(CTRL + path, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def status() -> dict:
    return {"pid": _pid(), "proxy_ok": _probe(6),
            "ctrl_up": _ctrl("/version") is not None,
            "gpu_note": [n for p, n in GPU_PORTS.items() if _ctrl(f"/../{p}") or False] or None}


def start(wait_sec: int = 20) -> dict:
    """启动 mihomo (幂等): spawn → 等控制口 → 探针."""
    if _probe(4):
        return {"ok": True, "already": True, **status()}
    if not MIHOMO_EXE.exists():
        return {"ok": False, "error": f"缺 {MIHOMO_EXE}"}
    cfg = MIHOMO_DIR / "config.yaml"
    if "SUB_URL_PLACEHOLDER" in cfg.read_text(encoding="utf-8"):
        return {"ok": False,
                "error": "config.yaml 订阅链接未填 (SUB_URL_PLACEHOLDER) — 机场订阅买好后替换"}
    subprocess.Popen([str(MIHOMO_EXE), "-d", str(MIHOMO_DIR)],
                     cwd=str(MIHOMO_DIR),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait_sec):
        time.sleep(1)
        if _ctrl("/version") and _probe(6):
            return {"ok": True, **status()}
    return {"ok": False, "error": "启动后探针失败 (节点不通? 订阅过期?)", **status()}


def stop() -> dict:
    pid = _pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(1)
    return {"ok": _pid() is None, "killed": pid}


def ensure_proxy() -> bool:
    """素材抓取前调用: 未启动则启动. 返回代理可用性."""
    return bool(start().get("ok") or _probe(4))


def nodes() -> list[dict]:
    """当前节点与延迟 (url-test 组延迟榜)."""
    proxies = _ctrl("/proxies") or {}
    grp = (proxies.get("proxies") or {}).get("PROXY") or {}
    now = grp.get("now")
    rows = []
    for name in grp.get("all") or []:
        p = (proxies.get("proxies") or {}).get(name) or {}
        hist = p.get("history") or []
        delay = hist[-1].get("delay") if hist else None
        rows.append({"name": name, "delay_ms": delay, "selected": name == now})
    return sorted(rows, key=lambda r: (r["delay_ms"] is None, r["delay_ms"] or 0))


def main() -> int:
    ap = argparse.ArgumentParser(description="mihomo 代理托管")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("restart")
    sub.add_parser("nodes")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False))
    elif args.cmd in ("start", "restart"):
        if args.cmd == "restart":
            stop()
        r = start()
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r.get("ok") else 1)
    elif args.cmd == "stop":
        print(json.dumps(stop(), ensure_ascii=False))
    else:
        for n in nodes()[:10]:
            mark = "★" if n["selected"] else " "
            print(f"{mark} {n['name'][:36]:38s} {n['delay_ms'] or '-'}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
