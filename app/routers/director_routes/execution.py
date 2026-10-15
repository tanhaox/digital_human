"""Director 执行 + 取消端点.

原 `execute_job`(53 行)拆分: 管线解码与可执行性检查下沉为私有函数。
后台执行线程 `_execute_in_background` 保持原样 (32 行 ≤40)。
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import db_session, get_db
from app.models import DirectorJob
from app.schemas import DirectorJobOut
from app.services.director_service import mark_job_reviewed
from app.services.slot_executor import execute_all_slots, request_cancel
from app.routers.director_routes.common import _decode_pipelines, _job_or_404

logger = logging.getLogger(__name__)

execution_router = APIRouter(tags=["director"])

__all__ = ["execution_router", "_decode_request_pipelines", "_check_executable",
           "_execute_in_background", "force_stop_job"]

# Track which jobs are currently executing to prevent double-trigger
_executing_jobs: set[str] = set()
_executing_lock = threading.Lock()


def _decode_request_pipelines(job: DirectorJob, pipelines: str | None) -> set[str] | None:
    """query param 优先, 缺省回退到 job.pipelines 落库约束 (修复 C)."""
    if pipelines is not None and not pipelines.strip():
        pipelines = job.pipelines
    return _decode_pipelines(pipelines)


def _check_executable(job: DirectorJob, db: Session) -> None:
    """Reject re-execution on completed jobs; revive fully-failed ones.

    fully-failed job (2026-08-26 改): 不再 409 拒绝 — 执行期全灭的 job 常因
    代码缺陷(如 hf_quote 路由/时长 clamp 时代), 修复后应可直接复活重跑,
    重新规划要再花一次 LLM。此处把全部 failed slot 重置 queued 并清错误,
    走正常执行流。completed 仍拒绝(重跑请用 slot 级重试)。"""
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="job 已完成, 不可重复执行")
    if job.status == "failed" and all(s.status in ("failed", "replaced") for s in job.slots):
        revived = 0
        for s in job.slots:
            if s.status == "failed":
                s.status = "queued"
                s.error_code = None
                s.error_message = None
                revived += 1
        job.error_message = None
        db.commit()
        logger.info("[director %s] revived failed job: %d slots -> queued", job.id[:8], revived)


def _execute_in_background(job_id: str, auto_replace: bool, enabled_pipelines: set[str] | None = None) -> None:
    """Run execute_all_slots in a dedicated thread with its own DB session."""
    with db_session() as db:
        try:
            from app.services.slot_executor import is_force_stopped
            if is_force_stopped(job_id):
                # force-stop 端点已写终态, 后台线程直接退出, 防止重复写
                logger.info("[director %s] force-stopped before background start, skip", job_id)
                return
            job = execute_all_slots(db, job_id, auto_replace=auto_replace, enabled_pipelines=enabled_pipelines)
            # 进入 reviewing 阶段
            if job.status in ("completed", "failed"):
                any_completed = any(s.status == "completed" for s in job.slots)
                if any_completed:
                    try:
                        mark_job_reviewed(db, job_id)
                    except Exception:
                        logger.warning("mark_job_reviewed failed for %s", job_id)
            logger.info(
                "[director %s] background execute done, status=%s", job_id, job.status,
            )
        except Exception:
            # force-stop 竞争: 端点先 mark_force_stopped 再杀进程, 后台线程可能
            # 因被杀子进程抛异常进入这里 → 跳过崩溃处理, 终态已由端点写入.
            from app.services.slot_executor import is_force_stopped
            if is_force_stopped(job_id):
                logger.info("[director %s] force-stopped during execute, skip crash handler", job_id)
                return
            logger.exception("[director %s] background execute_all_slots crashed", job_id)
            # Mark job as failed so frontend doesn't poll forever
            try:
                j = db.get(DirectorJob, job_id)
                if j and j.status == "executing":
                    j.status = "failed"
                    j.error_message = "execute_all_slots crashed (see server log)"
                    from app.services.director_service import append_trace
                    append_trace(db, j, "execute", "error", "execute_all_slots crashed")
                    db.commit()
            except Exception:
                pass
        finally:
            with _executing_lock:
                _executing_jobs.discard(job_id)


@execution_router.post("/jobs/{job_id}/execute", response_model=DirectorJobOut)
def execute_job(
    job_id: str,
    auto_replace: bool = True,
    pipelines: str | None = None,
    db: Session = Depends(get_db),
):
    """触发后台执行所有 queued slot. 立即返回, 前端轮询状态.

    auto_replace: 自动运行 fallback 链 (default True).
    pipelines: 逗号分隔启用管线 c/p/h (默认全开), 缺省回退 job.pipelines。
    三态: None=全启 / ""=全关 / "c,p"=部分。
    """
    job = _job_or_404(db, job_id)
    enabled: set[str] | None = _decode_request_pipelines(job, pipelines)
    logger.info("[director %s] pipelines filter: %s", job_id,
                ",".join(sorted(enabled)) if enabled else ("all" if enabled is None else "none"))
    _check_executable(job, db)

    with _executing_lock:
        if job_id in _executing_jobs:
            raise HTTPException(status_code=409, detail="job 正在执行中, 请勿重复触发")
        _executing_jobs.add(job_id)

    # force 标记无天然清理点: 每个新执行入口显式清除, 防脏标记阻断本次执行。
    # cancel 标志同理 (2026-09-04 实证): force-stop 会同时 set cancel, 若不在此
    # 清除, 重启后首次「执行」会被执行循环的 _is_cancelled 检查秒退 —
    # start→done 同秒且 0 slot 执行, 前端看似"点了没反应"。
    from app.services.slot_executor import clear_cancel, clear_force_stopped
    clear_force_stopped(job_id)
    clear_cancel(job_id)

    job.status = "executing"
    db.commit()
    db.refresh(job)

    t = threading.Thread(
        target=_execute_in_background,
        args=(job_id, auto_replace, enabled),
        daemon=True,
        name=f"exec-{job_id[:8]}",
    )
    t.start()

    logger.info("[director %s] execute dispatched to background thread", job_id)
    return job


@execution_router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """发送取消信号，后台线程在下一个 phase/检查点停止。

    planning → 规划线程在下一个检查点中止; executing → 执行循环在 phase 边界停止.
    """
    job = _job_or_404(db, job_id)
    if job.status not in ("executing", "planning"):
        raise HTTPException(status_code=409, detail=f"job 状态为 {job.status}，无法取消")
    request_cancel(job_id)
    logger.info("[director %s] cancel signal sent", job_id)
    if job.status == "planning":
        return {"status": "cancelling", "message": "已发送停止信号，规划将在当前步骤完成后中止"}
    return {"status": "cancelling", "message": "已发送停止信号，将在当前阶段完成后停止"}


def _send_comfyui_interrupt(job_id: str) -> None:
    """尽力而为地向 ComfyUI 发送 /interrupt 中断当前 job 的排队 prompt.

    ComfyUI 是长驻共享服务, 不杀进程; 只中断本 job 的队列项.
    host_comfy._send_comfyui_interrupt 内部已吞异常, 无需再包 (E2).
    """
    try:
        from app.config import get_config
        from app.services.slot_workflows.host_comfy import _send_comfyui_interrupt as _interrupt
        _interrupt(get_config(), job_id)
    except Exception as exc:
        logger.warning("[director %s] comfyui interrupt skip: %s", job_id, exc)


@execution_router.post("/jobs/{job_id}/force-stop")
def force_stop_job(job_id: str, db: Session = Depends(get_db)):
    """强制停止卡死的 job: 杀全部已注册子进程树 + 立即落终态.

    与协作式取消不同, 本端点不依赖后台线程配合:
      1. mark_force_stopped + request_cancel   (竞争保护必须先于杀进程)
      2. kill_job_procs → taskkill /F /T 杀树
      3. _send_comfyui_interrupt               (尽力而为)
      4. 直接落终态: planning→failed; 有 completed slot→reviewing; 否则→failed
      5. 摘除 _executing/_composing/_planning 集合
      6. SSE "force_stopped" 终止事件
    后台线程返回时查 is_force_stopped → 跳过状态覆盖 (双保险).

    已知局限: 纯 Python 阻塞 (LLM/TTS) 无子进程可杀, job 恢复可操作但
    后台线程迟滞占用直到自身超时 (看门狗归二版). Pexels 下载已自带 wall-clock
    总上限 (config pexels_download_total_timeout_sec, 2026-09-04 慢滴流挂死修复),
    最坏 180s 自行超时走 fallback 链, 不再无限占用.
    """
    job = _job_or_404(db, job_id)

    # 允许状态判定: executing/planning, 或 reviewing 且正在合成 (composing)
    from app.routers.director_routes.compose import _composing_jobs
    from app.routers.director_routes.planning import _planning_jobs
    composing_active = job_id in _composing_jobs
    if job.status not in ("executing", "planning") and not (
        job.status == "reviewing" and composing_active
    ):
        raise HTTPException(
            status_code=409,
            detail=f"job 状态为 {job.status}, 无需强制停止",
        )

    from app.services.slot_executor import mark_force_stopped, request_cancel
    from app.services.proc_registry import kill_job_procs

    # 1. 竞争保护必须先于杀进程: 后台线程任何状态写前查 is_force_stopped → 跳过
    mark_force_stopped(job_id)
    request_cancel(job_id)

    # 2. 杀该 job 全部已注册进程树 (ffmpeg/HF npx/...)
    killed = kill_job_procs(job_id)

    # 3. ComfyUI interrupt 尽力而为
    try:
        _send_comfyui_interrupt(job_id)
    except Exception:
        pass

    # 4. 直接落终态
    from app.models import DirectorSlot
    has_completed = (
        db.query(DirectorSlot.id)
        .filter(
            DirectorSlot.director_job_id == job_id,
            DirectorSlot.status == "completed",
        )
        .first()
        is not None
    )
    if job.status == "planning":
        job.status = "failed"
        job.error_message = "用户强制停止 (规划未完成)"
    elif has_completed:
        job.status = "reviewing"
        job.error_message = None
    else:
        job.status = "failed"
        job.error_message = "用户强制停止, 无已完成 slot"
    db.commit()

    # 5. 摘除执行/合成/规划集合
    with _executing_lock:
        _executing_jobs.discard(job_id)
    from app.routers.director_routes.compose import _composing_lock
    from app.routers.director_routes.planning import _planning_lock
    with _composing_lock:
        _composing_jobs.discard(job_id)
    with _planning_lock:
        _planning_jobs.discard(job_id)

    # 6. SSE 终止事件
    from app.services.director_events import publish as _evt
    _evt(job_id, {"type": "force_stopped", "msg": "任务已强制停止, 子进程已终止"})

    logger.info(
        "[director %s] force-stopped: killed=%d, status=%s",
        job_id, killed, job.status,
    )
    return {
        "status": job.status,
        "message": f"已强制停止, 杀掉 {killed} 个进程树",
        "killed": killed,
    }
