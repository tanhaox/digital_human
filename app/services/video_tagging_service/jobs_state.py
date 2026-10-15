"""视频打标 — 批量任务全局状态与线程内状态机 helper.

_jobs / _jobs_lock 被 start_tagging_job / get_job_status / cancel_job 与
线程主循环共享, 独立成模块避免跨模块 import 时的循环依赖。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "_jobs",
    "_jobs_lock",
    "_now_iso",
    "_mark_running",
    "_fail_startup",
    "_update_progress",
    "_inc_progress",
    "_set_current_asset",
    "_get_counts",
    "_check_cancelled",
    "_mark_final",
]


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_running(job_id: str) -> None:
    """线程启动: 置 running + started_at."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = _now_iso()


def _fail_startup(job_id: str, total: int) -> None:
    """llama-server 无法启动: 置 failed + publish complete(error)."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["failed"] = total
        _jobs[job_id]["finished_at"] = _now_iso()
    from app.services.director_events import publish

    publish(job_id, {"type": "complete", "done": 0, "failed": total, "total": total,
                     "error": "llama-server 无法启动"})


def _update_progress(job_id: str, done: int, failed: int, current_asset_no: str | None = None) -> None:
    """更新任务 done/failed (可选 current_asset_no).

    注意: 并发打标下调用方传入的是各自快照, 存在覆盖风险 —
    并发路径请优先使用 _inc_progress / _set_current_asset (原子).
    本函数保留给单线程场景与外部快照回写.
    """
    with _jobs_lock:
        payload = {"done": done, "failed": failed}
        if current_asset_no is not None:
            payload["current_asset_no"] = current_asset_no
        _jobs[job_id].update(payload)


def _inc_progress(job_id: str, done_add: int = 1, failed_add: int = 0) -> tuple[int, int]:
    """原子递增 done/failed, 返回递增后的最新值 (并发安全)."""
    with _jobs_lock:
        done = _jobs[job_id].get("done", 0) + done_add
        failed = _jobs[job_id].get("failed", 0) + failed_add
        _jobs[job_id]["done"] = done
        _jobs[job_id]["failed"] = failed
        return done, failed


def _set_current_asset(job_id: str, asset_no: str | None) -> None:
    """原子更新 current_asset_no."""
    with _jobs_lock:
        _jobs[job_id]["current_asset_no"] = asset_no


def _get_counts(job_id: str) -> tuple[int, int]:
    """原子读取当前 done/failed."""
    with _jobs_lock:
        return _jobs[job_id].get("done", 0), _jobs[job_id].get("failed", 0)


def _check_cancelled(job_id: str, done: int, failed: int, total: int) -> bool:
    """循环内取消检查. 已取消 → 置状态 + publish + 返回 True.

    返回 True 表示调用方应立即 return (已处理取消收尾).
    """
    from app.services.director_events import publish

    with _jobs_lock:
        cancelled = _jobs[job_id].get("cancelled")
        if not cancelled:
            return False
        _jobs[job_id]["status"] = "cancelled"
        _jobs[job_id]["finished_at"] = _now_iso()
    publish(job_id, {"type": "cancelled", "done": done, "failed": failed, "total": total})
    return True


def _mark_final(job_id: str, done: int, failed: int, total: int, *, cancelled: bool = False) -> None:
    """循环结束收尾: 置最终状态 (cancelled/failed/completed) + finished_at + publish.

    注意: 非取消路径即使 all_failed 也发布 complete 事件 (与旧版一致),
    仅词表包重建由调用方按 all_failed 决定。
    """
    from app.services.director_events import publish

    with _jobs_lock:
        _jobs[job_id]["status"] = "cancelled" if cancelled else ("failed" if failed >= total else "completed")
        _jobs[job_id]["finished_at"] = _now_iso()
        _jobs[job_id].update({"done": done, "failed": failed})

    if cancelled:
        publish(job_id, {
            "type": "cancelled",
            "done": done,
            "failed": failed,
            "total": total,
            "msg": f"AI 打标已取消: {done} 处理, {failed} 失败/回退",
        })
    else:
        publish(job_id, {
            "type": "complete",
            "done": done,
            "failed": failed,
            "total": total,
            "msg": f"AI 打标完成: {done} 处理, {failed} 失败/回退",
        })
