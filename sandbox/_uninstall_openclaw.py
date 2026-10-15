# -*- coding: utf-8 -*-
"""OpenClaw 卸载: 三处目录全部进回收站 (红线: 不直接删除)。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TARGETS = [r"E:\openclaw",
           r"C:\Users\tanhaox\.openclaw",
           r"C:\Users\tanhaox\AppData\Local\openclaw"]

try:
    from send2trash import send2trash
    send2trash(TARGETS)
    print("send2trash: 已送回收站")
except ImportError:
    print("send2trash 未装, 用 Shell.Application COM")
    import pythoncom
    pythoncom.CoInitialize()
    from win32com.client import Dispatch
    sh = Dispatch("Shell.Application")
    rb = sh.Namespace(10)
    import os.path as op
    for t in TARGETS:
        parent, name = op.split(op.normpath(t.rstrip("\\") if op.isdir(t) else t))
        item = sh.Namespace(parent).ParseName(name)
        if item is not None:
            rb.MoveHere(item)
            print(f"MoveHere: {t}")
            time.sleep(2)
        else:
            print(f"未找到: {t}")

time.sleep(3)
from pathlib import Path
for t in TARGETS:
    print(f"{t}: {'✗ 仍在' if Path(t).exists() else '✓ 已进回收站'}")
