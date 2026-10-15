# -*- coding: utf-8 -*-
"""worker 健康检查."""
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
out = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "14080,23280,27388,29136 | ForEach-Object { $p = Get-Process -Id $_ "
     "-ErrorAction SilentlyContinue; if ($p) { $_.ToString() + ' CPU=' "
     "+ [math]::Round($p.CPU) + 's' } else { $_.ToString() + ' gone' } }"],
    capture_output=True, text=True, timeout=30)
print(out.stdout)
