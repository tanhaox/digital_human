"""视频打标 — 残留临时帧目录清理 (进程被强杀后的孤儿回收).

背景: 打标时 ffmpeg 将视频抽帧为 PNG, 落在系统临时目录
(<tempdir>/tagging_frames_*)。正常任务在 `_run_tagging_job_thread` 的
`TemporaryDirectory` with 块退出时删除, 帧文件由 worker 按前缀清理。
但进程被 taskkill //F 强杀 (daemon 线程中断, finally 未执行) 时,
临时目录和帧文件会残留为孤儿, 累积占用磁盘。

策略: 后端每次启动时扫描 <tempdir>/tagging_frames_*, 全部送回收站
(send2trash, 满足 CLAUDE.md 红线)。残留必然是孤儿——正常任务活着时
不会退出, 此刻启动说明上一轮已被杀/已结束。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from ..file_utils import safe_trash

logger = logging.getLogger(__name__)

__all__ = ["cleanup_orphan_tagging_frames"]

_ORPHAN_PREFIX = "tagging_frames_"


def _find_orphan_tagging_dirs() -> list[Path]:
    """扫描系统临时目录下残留的 tagging_frames_* 目录."""
    tmp_root = Path(tempfile.gettempdir())
    if not tmp_root.is_dir():
        return []
    return sorted(tmp_root.glob(f"{_ORPHAN_PREFIX}*"))


def cleanup_orphan_tagging_frames() -> int:
    """启动时清理残留抽帧目录, 返回清理数量.

    安全前提: 后端刚启动, 不可能有打标任务正在使用这些目录 —
    上一轮进程已退出(正常 or 被强杀)。若存在目录, 必然为孤儿, 直接回收。
    """
    orphans = _find_orphan_tagging_dirs()
    if not orphans:
        return 0

    cleaned = 0
    for d in orphans:
        try:
            if d.is_dir():
                if safe_trash(d):
                    cleaned += 1
                    logger.info("[tagging-cleanup] 回收残留抽帧目录: %s", d.name)
                else:
                    logger.warning("[tagging-cleanup] 回收失败: %s", d.name)
        except Exception as exc:
            logger.warning("[tagging-cleanup] 处理 %s 异常: %s", d.name, exc)

    logger.info("[tagging-cleanup] 启动清理: 回收 %d 个残留抽帧目录", cleaned)
    return cleaned
