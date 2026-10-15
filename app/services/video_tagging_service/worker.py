"""视频打标 — 批量任务单素材处理 worker (素材上下文 → 流水线 → 写库).

被 jobs.py 的线程池 worker 调用; 独立成模块避免 jobs.py 超行数约束.

并发说明: 计数通过 jobs_state 的原子 helper (_inc_progress / _set_current_asset
/ _get_counts) 在 _jobs_lock 保护下更新, 杜绝多线程覆盖丢计数.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.database import db_session
from app.models import VideoAsset
from app.services.asset_tagging import infer_tags
from app.services.director_events import publish
from app.services.local_llm_client import LocalLLMClient
from app.services.video_tagging_service.jobs_state import (
    _get_counts,
    _inc_progress,
    _set_current_asset,
)
from app.services.video_tagging_service.pipeline import _run_pipeline_for_asset
from app.services.video_tagging_service.store import _write_asset_tags

logger = logging.getLogger(__name__)

__all__ = ["_process_asset"]


def _load_asset_context(aid: str) -> dict[str, Any] | None:
    """加载素材上下文; 素材不存在返回 None."""
    with db_session() as session:
        asset = session.query(VideoAsset).filter(VideoAsset.id == aid).first()
        if asset is None:
            return None
        return {
            "asset_no": asset.asset_no,
            "video_path": asset.file_path,
            "duration": asset.duration_sec or 10.0,
            "fallback": infer_tags(
                asset.raw_query or "",
                asset.width or 1920,
                asset.height or 1080,
            ),
        }


def _register_missing_asset(
    job_id: str,
    aid: str,
    total: int,
) -> None:
    """素材不存在: 原子计数 + publish progress (不返回值)."""
    done, failed = _inc_progress(job_id, done_add=1, failed_add=1)
    publish(job_id, {
        "type": "progress", "done": done, "failed": failed, "total": total,
        "current_asset_no": f"unknown({aid[:8]})",
        "msg": f"素材不存在: {aid}",
    })


def _cleanup_frames(tmpdir: Path, prefix: str = "frame_") -> None:
    """清理临时帧文件 (为下一个素材腾空间).

    Args:
        tmpdir: 帧临时目录.
        prefix: 文件名前缀. 并发打标时每个素材使用独立前缀
                (frame_{aid短ID}_), 只清理属于本素材的帧, 避免误删他线程在用的帧.
    """
    for f in tmpdir.glob(f"{prefix}*.png"):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _publish_asset_progress(
    job_id: str,
    asset_no: str,
    *,
    done: int,
    failed: int,
    total: int,
) -> None:
    """发布"正在处理素材"进度事件."""
    publish(job_id, {
        "type": "progress", "done": done, "failed": failed, "total": total,
        "current_asset_no": asset_no,
        "msg": f"正在处理 {asset_no} ({done + 1}/{total}) — 4通道流水线",
    })


def _finalize_asset_result(
    job_id: str,
    aid: str,
    asset_no: str,
    tags: dict[str, Any],
    model_name: str,
    tmpdir: Path,
    *,
    total: int,
) -> None:
    """写库 + 原子计数 + 清理帧 + 发布完成事件 (不返回值)."""
    ok = _write_asset_tags(aid, tags, model_name)
    done, failed = _inc_progress(job_id, done_add=1, failed_add=(0 if ok and not tags.get("_fallback") else 1))

    # 日志: 每完成一条, 已完成 +1 (用户后台可实时看到处理进度)
    logger.info("[tagging-job] %s 已完成 %d/%d 条", asset_no, done, total)

    # 清理临时帧文件 (只清理本素材前缀, 并发时不误删他线程帧)
    _cleanup_frames(tmpdir, prefix=f"frame_{aid[:8]}_")

    publish(job_id, {
        "type": "item_done", "done": done, "failed": failed, "total": total,
        "current_asset_no": asset_no,
        "msg": f"完成 {asset_no} {'✓' if not tags.get('_fallback') else '⚠ 回退规则'}",
    })


def _process_asset(
    job_id: str,
    aid: str,
    model_name: str,
    client: LocalLLMClient,
    tmpdir: Path,
    *,
    total: int,
) -> None:
    """处理单个素材: 加载上下文 → 4通道流水线 → 写库 → 原子计数 → 清理帧.

    tmpdir 参数为帧命名空间目录. 并发打标时, 帧输出由 worker 独立管理:
    给本素材分配 8 位短 ID 前缀 (frame_{aid[:8]}_NNN.png), 从该前缀开始取帧,
    结束后按同前缀清理 — 不同素材互不干扰, 不会覆盖/误删他线程帧.

    计数经 _inc_progress 原子递增, 不依赖调用方传入的快照, 杜绝多线程丢计数.
    任务已取消时直接返回 (不计数), 由 jobs.py 线程收尾.
    """
    # 取消检查: 任务已取消则跳过本素材, 由 jobs.py 线程收尾 (不重复发布取消事件)
    from app.services.video_tagging_service.jobs_state import _jobs, _jobs_lock

    with _jobs_lock:
        if _jobs[job_id].get("cancelled"):
            logger.debug("[tagging:%s] 已取消, 跳过素材 %s", job_id, aid)
            return

    # 加载素材信息
    ctx = _load_asset_context(aid)
    if ctx is None:
        _register_missing_asset(job_id, aid, total)
        return

    asset_no = ctx["asset_no"]
    done, failed = _get_counts(job_id)
    # 更新当前进度
    _set_current_asset(job_id, asset_no)
    _publish_asset_progress(job_id, asset_no, done=done, failed=failed, total=total)

    # ── 执行 4 通道流水线 ──
    tags = _run_pipeline_for_asset(
        video_path=ctx["video_path"],
        duration=ctx["duration"],
        tmpdir=tmpdir,
        client=client,
        fallback=ctx["fallback"],
        asset_no=asset_no,
        job_id=job_id,
        done=done,
        failed=failed,
        total=total,
        frame_prefix=f"frame_{aid[:8]}_",
        asset_id=aid,
    )

    _finalize_asset_result(
        job_id, aid, asset_no, tags, model_name, tmpdir,
        total=total,
    )
