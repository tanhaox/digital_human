"""Director Agent 2.0 — 环节级轨迹 (trace).

复用 plan_json 里的 "trace" 数组, 无需新增 DB 列。
记录每个环节的起止/状态/详情, 出问题时按 trace 顺序快速锁定出错的环节。
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import DirectorJob

logger = logging.getLogger(__name__)

__all__ = ["append_trace", "get_trace"]


def append_trace(
    db: Session,
    job: DirectorJob,
    step: str,
    status: str,
    detail: str = "",
    *,
    _json: dict | None = None,
) -> None:
    """Append one trace entry to job.plan_json["trace"] and commit.

    Args:
        step: 环节名, e.g. "alignment" / "plan_llm" / "execute_phase" / "fallback" / "compose".
        status: "start" | "done" | "error" | "skip" | "warn".
        detail: 简短说明 (保留可读性, 不塞长堆栈)。
    """
    if _json is not None:
        plan = _json
    else:
        # deepcopy 必须: plan_json 已非空时 (job.plan_json or {}) 返回的是对象本身,
        # 就地 append + 自赋值不会触发 SQLAlchemy 脏标记, commit 不 flush 导致该条丢失
        # (正是 plan_llm 埋点丢失的根因; alignment 埋点时 plan_json 为空 {} 走 or {} 新 dict 才碰巧成功).
        plan = copy.deepcopy(job.plan_json or {})
    trace = plan.setdefault("trace", [])
    trace.append({
        "n": len(trace) + 1,  # 序号基于既有 trace 长度, 幂等、跨重启不乱序
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": step,
        "status": status,
        "detail": detail[:400],
    })
    if _json is None:
        job.plan_json = plan
        db.commit()


def get_trace(db: Session, job: DirectorJob) -> list[dict[str, Any]]:
    """Read job trace (empty list if none yet)."""
    return list((job.plan_json or {}).get("trace", []) or [])
