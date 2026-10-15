"""视频打标 — 批量任务线程主循环 + 公共 API (start/get/cancel)."""
from __future__ import annotations

import logging
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import get_config
from app.database import db_session
from app.models import VideoAsset
from app.services.director_events import publish
from app.services.local_llm_client import LocalLLMClient
from app.services.video_tagging_service.jobs_state import (
    _fail_startup,
    _get_counts,
    _jobs,
    _jobs_lock,
    _mark_final,
    _mark_running,
)
from app.services.video_tagging_service.llama import _ensure_llama_server
from app.services.video_tagging_service.store import _rebuild_vocabulary_pack
from app.services.video_tagging_service.worker import _process_asset

logger = logging.getLogger(__name__)

__all__ = ["start_tagging_job", "get_job_status", "cancel_job", "cancel_all_active_jobs"]


def _resolve_asset_ids(asset_ids: list[str] | None) -> list[str]:
    """素材 ID 列表解析: None 时全量查询."""
    if asset_ids is not None:
        return asset_ids
    with db_session() as session:
        rows = session.query(VideoAsset.id).all()
        return [r[0] for r in rows]


def _spawn_tagging_thread(job_id: str, asset_ids: list[str], model_name: str) -> None:
    """启动后台打标线程 (daemon)."""
    t = threading.Thread(
        target=_run_tagging_job_thread,
        args=(job_id, asset_ids, model_name),
        name=f"tagging-{job_id}",
        daemon=True,
    )
    t.start()


def _get_cancelled(job_id: str) -> bool:
    """读取取消标志 (用于循环末尾先于 completed 判定)."""
    with _jobs_lock:
        return _jobs[job_id].get("cancelled")


def _run_tagging_job_thread(job_id: str, asset_ids: list[str], model_name: str) -> None:
    """后台线程: 4 通道流水线 4 线程并发处理素材打标.

    并发要点:
    - ThreadPoolExecutor(4) 并行处理素材, 配合 llama-server --parallel 4 (4 个 slot).
    - 计数经 jobs_state 原子 helper 递增 (worker 内), 本线程只读终值.
    - 每个素材独立帧前缀 (worker 内分配), 帧文件互不覆盖.
    - 取消: 提交前检查一次; 进行中置位不中断在飞 worker (仅边界生效),
      等待已提交任务自然结束后落 cancelled 终态.
    """
    _mark_running(job_id)

    cfg = get_config()
    total = len(asset_ids)

    # 自动拉起 llama-server
    if not _ensure_llama_server():
        logger.error("[tagging-job] llama-server 无法启动, 任务中止")
        _fail_startup(job_id, total)
        return

    client = LocalLLMClient(cfg.local_llm)

    def _run_one(aid: str) -> None:
        # 提交后任务可能已被取消, worker 内已做取消检查; 此处仅兜底忽略未命中素材
        _process_asset(job_id, aid, model_name, client, tmpdir, total=total)

    publish(job_id, {"type": "start", "total": total, "msg": f"开始 AI 打标 (4通道流水线, 4线程并发), 共 {total} 个素材"})

    with tempfile.TemporaryDirectory(prefix="tagging_frames_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)

            # 日志提示1: 所有加载完成, 开始处理第一条
        logger.info("[tagging-job] ai打标开始: llama-server 就绪, 4 线程并发启动, 共 %d 条素材", total)

        # 分批提交 (BATCH=12): 修复"取消后进程停不下来"。
        # 此前把全部素材一次 submit 进线程池 → 取消标志只对未提交的部分有意义,
        # 已 submit 的素材 (480 个) 线程池会全部跑完, "✕ 取消"形同虚设。
        # 分批后每次只 submit BATCH 个, 每批开始前检查取消标志 → 取消后至多
        # 再跑完当前批 (≤12 个), 其余不再提交。worker 内仍保留原取消检查兜底。
        BATCH = 12
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="tagging") as executor:
            for start in range(0, len(asset_ids), BATCH):
                if _get_cancelled(job_id):
                    logger.info("[tagging-job] 已取消, 停止提交后续 %d 条素材", len(asset_ids) - start)
                    break
                batch = asset_ids[start:start + BATCH]
                futures = [executor.submit(_run_one, aid) for aid in batch]
                for fut in futures:
                    fut.result()

        # 日志提示2: 全部结束, 汇报累计完成量
        done, failed = _get_counts(job_id)
        logger.info("[tagging-job] ai打标结束: %d 完成 / %d 失败 / 共 %d 条", done, failed, total)

    # 完成 (取消标志可能在处理期间被置位 — 必须先于 completed 判定)
    cancelled = _get_cancelled(job_id)
    if cancelled:
        _mark_final(job_id, done, failed, total, cancelled=True)
        return

    if not (failed >= total):
        # ID-034: 有成功打标 → 重建词表包 (新标签进入包, 导演下次规划即可命中)
        # 必须先于 complete 事件发布 — 客户端收到 complete 时词表应已重建完成
        _rebuild_vocabulary_pack()

    _mark_final(job_id, done, failed, total)


def start_tagging_job(asset_ids: list[str] | None = None) -> str:
    """启动批量 AI 打标后台任务 (4 通道流水线).

    Args:
        asset_ids: 指定素材 ID 列表；为 None 时全量打标.

    Returns:
        job_id, 用于查询进度/取消.
    """
    job_id = uuid.uuid4().hex[:12]

    asset_ids = _resolve_asset_ids(asset_ids)
    if not asset_ids:
        raise ValueError("没有可打标的素材")

    cfg = get_config()
    model_name = cfg.local_llm.model

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "pending",
            "total": len(asset_ids),
            "done": 0,
            "failed": 0,
            "current_asset_no": None,
            "started_at": None,
            "finished_at": None,
            "cancelled": False,
        }

    _spawn_tagging_thread(job_id, asset_ids, model_name)

    return job_id


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """查询任务状态."""
    with _jobs_lock:
        return _jobs.get(job_id, None).copy() if job_id in _jobs else None


def cancel_job(job_id: str) -> bool:
    """请求取消任务."""
    with _jobs_lock:
        if job_id not in _jobs or _jobs[job_id]["status"] in ("completed", "failed", "cancelled"):
            return False
        _jobs[job_id]["cancelled"] = True
        return True


def cancel_all_active_jobs() -> int:
    """取消所有尚未结束的打标任务 (供后端关闭时调用).

    uvicorn 优雅关闭只停 HTTP, 不会通知打标 daemon 线程 → 用户 Ctrl+C /
    taskkill 优雅关闭后, 已提交的素材线程池仍会全部跑完, 进程退不出
    ("Shutting down" 后几分钟停不下来)。关闭前调用本函数置 cancelled 标志,
    配合 jobs.py 的分批提交, 至多再跑完当前批 (<BATCH) 即停。
    """
    with _jobs_lock:
        active = [
            jid for jid, st in _jobs.items()
            if st.get("status") not in ("completed", "failed", "cancelled")
        ]
        for jid in active:
            _jobs[jid]["cancelled"] = True
    if active:
        logger.info("[tagging-job] 后端关闭: 已取消 %d 个进行中打标任务", len(active))
    return len(active)
