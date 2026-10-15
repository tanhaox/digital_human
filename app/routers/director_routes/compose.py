"""Director 合成端点: 触发后台合成 + SSE 进度透传.

原 `_compose_in_background`(70 行)拆为三个私有 helper:
`_compose_start_trace`(start trace)、`_compose_success`(done trace + 成品登记)、
`_compose_failure`(异常收敛)。端点主体与后台线程均 ≤40 行。
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import db_session, get_db
from app.routers.director_routes.common import _job_or_404

logger = logging.getLogger(__name__)

compose_router = APIRouter(tags=["director"])

__all__ = ["compose_router", "_compose_start_trace", "_compose_success",
           "_compose_failure", "_compose_in_background"]

_composing_lock = threading.Lock()
_composing_jobs: set[str] = set()


def _compose_start_trace(db, job_id: str, crossfade_sec: float, target_lufs: float) -> None:
    """Append compose start trace if the job still exists."""
    from app.models import DirectorJob as DJ
    from app.services.director_service import append_trace

    _dj = db.get(DJ, job_id)
    if _dj:
        append_trace(
            db, _dj, "compose", "start",
            f"合成开始 (crossfade={crossfade_sec}s, lufs={target_lufs})",
        )


def _compose_success(db, job_id: str, result: dict) -> None:
    """Append compose done trace and auto-register the finished video."""
    from app.models import DirectorJob as DJ
    from app.services.director_service import append_trace

    _dj = db.get(DJ, job_id)
    if _dj:
        append_trace(
            db, _dj, "compose", "done",
            f"合成完成: {result.get('duration_sec', 0):.1f}s → {result.get('output_path', '')}",
        )
    # 自动登记成品库
    try:
        from app.models import VideoOutput, DirectorJob as DJ2

        job = db.get(DJ2, job_id)
        output_path = result.get("output_path", "")
        title = ""
        if job:
            title = job.title or ""
            if not title and job.script and job.script.script_text:
                title = job.script.script_text[:30].replace("\n", " ").strip()
        if not title:
            title = f"director_{job_id[:8]}"
        vo = VideoOutput(
            job_id=job_id,
            title=title,
            file_path=output_path,
            orientation=job.video_format or "portrait" if job else "portrait",
            duration_sec=result.get("duration_sec"),
            video_format=job.video_format if job else None,
        )
        db.add(vo)
        db.commit()
    except Exception as reg_exc:
        logger.warning("[compose] VideoOutput registration failed: %s", reg_exc)


def _compose_failure(db, job_id: str) -> None:
    """Emit compose_error event and append error trace on background crash."""
    from app.services.director_events import publish as _evt

    try:
        _evt(job_id, {"type": "compose_error", "msg": "合成线程崩溃 (见服务端日志)"})
        try:
            from app.models import DirectorJob as DJ
            from app.services.director_service import append_trace

            _dj2 = db.get(DJ, job_id)
            if _dj2:
                append_trace(db, _dj2, "compose", "error", "合成线程崩溃")
        except Exception:
            pass
    except Exception:
        pass


def _compose_in_background(job_id: str, crossfade_sec: float, target_lufs: float) -> None:
    """Run compose_director_job in a background thread with SSE progress."""
    from app.services.director_events import publish as _evt
    from app.services.composition_service import compose_director_job
    from app.services.slot_executor import is_force_stopped
    from app.services.proc_registry import set_current_job_id

    # 线程子进程上下文: 绑定 job_id, 使合成 ffmpeg 子进程可被 force-stop 定位
    set_current_job_id(job_id)
    try:
        with db_session() as db:
            try:
                _compose_start_trace(db, job_id, crossfade_sec, target_lufs)
                result = compose_director_job(
                    db, job_id,
                    crossfade_sec=crossfade_sec,
                    target_lufs=target_lufs,
                    evt=lambda data: _evt(job_id, data),
                )
                # force 竞争保护: 端点已写终态, 跳过 success handler (防覆盖)
                if result.get("ok") and not is_force_stopped(job_id):
                    _compose_success(db, job_id, result)
                logger.info("[director %s] compose done, ok=%s", job_id, result.get("ok"))
            except Exception:
                if is_force_stopped(job_id):
                    # 强停后子进程被杀可能抛异常 → 跳过 failure handler
                    logger.info("[director %s] compose force-stopped, skip failure handler", job_id)
                else:
                    logger.exception("[director %s] compose background thread crashed", job_id)
                    _compose_failure(db, job_id)
            finally:
                with _composing_lock:
                    _composing_jobs.discard(job_id)
    finally:
        set_current_job_id(None)


@compose_router.post("/jobs/{job_id}/compose")
def compose_job(
    job_id: str,
    crossfade_sec: float = 0.2,
    target_lufs: float = -14.0,
    db: Session = Depends(get_db),
):
    """触发后台合成, 立即返回; 前端通过 SSE 获取进度."""
    job = _job_or_404(db, job_id)
    if not job.slots:
        raise HTTPException(status_code=409, detail="job 无 slot, 无法 compose")
    if not any(s.status == "completed" for s in job.slots):
        raise HTTPException(
            status_code=409,
            detail="至少需要 1 个 completed slot 才能 compose",
        )

    with _composing_lock:
        if job_id in _composing_jobs:
            raise HTTPException(status_code=409, detail="合成正在进行中, 请勿重复触发")
        _composing_jobs.add(job_id)

    # force 标记无天然清理点: 每个新执行入口显式清除, 防脏标记阻断本次合成
    from app.services.slot_executor import clear_force_stopped
    clear_force_stopped(job_id)

    t = threading.Thread(
        target=_compose_in_background,
        args=(job_id, crossfade_sec, target_lufs),
        daemon=True,
        name=f"compose-{job_id[:8]}",
    )
    t.start()

    return {"job_id": job_id, "status": "composing", "message": "合成已提交后台, 通过 SSE 获取进度"}
