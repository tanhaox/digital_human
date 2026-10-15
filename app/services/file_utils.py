"""Filesystem helpers — safely move files to trash instead of deleting."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows 回收站删除的统一实现 (2026-09-01 合并):
# 历史上有三份并行实现 — safe_trash(send2trash, 未装则 rmtree 永久删除, 违反红线) /
# routers.scripts._recycle_file / services.library_service.recycle_file (PowerShell 文件版).
# 现统一为 recycle_file(), 唯一事实源; safe_trash 保留为兼容别名.


def recycle_file(path: str | Path) -> bool:
    """Move a file **or directory** to the Windows Recycle Bin.

    PowerShell Microsoft.VisualBasic 实现, 无第三方依赖. 满足 CLAUDE.md 红线:
    永久删除被禁止 — 失败时返回 False 且**不动文件**, 绝不 fallback 到删除.

    Args:
        path: Windows 绝对路径 (文件或目录).

    Returns:
        True 若已移入回收站 (或路径不存在视为无需处理), False 若失败 (文件保留原地).
    """
    p = str(path)
    if not p or not os.path.exists(p):
        return False
    # PowerShell 单引号字面量内唯一需要转义的字符是单引号本身 ('' 转义)
    escaped = p.replace("'", "''")
    method = "DeleteDirectory" if os.path.isdir(p) else "DeleteFile"
    cmd = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::{method}("
        f"'{escaped}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
    )
    try:
        # 列表传参 (shell=False) 不经 cmd.exe, 反斜杠/中文路径均为字面量
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("recycle failed for %s: %s", p, exc)
        return False


def safe_trash(path: Path) -> bool:
    """向后兼容别名 → :func:`recycle_file`.

    ⚠️ 2026-09-01 行为变更: 历史版本在 send2trash 未安装时 fallback
    ``shutil.rmtree`` **永久删除** (违反 CLAUDE.md 红线, 三个调用方一直
    走的就是该路径). 现统一走回收站, 失败返回 False 且文件保留原地.
    """
    return recycle_file(path)
