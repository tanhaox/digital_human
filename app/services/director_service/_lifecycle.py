"""Director Agent 2.0 — 作业生命周期 (review / 完成判定)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import DirectorJob

logger = logging.getLogger(__name__)

__all__ = ["mark_job_reviewed", "complete_job_if_slots_done"]


def mark_job_reviewed(db: Session, job_id: str) -> DirectorJob:
    job = db.get(DirectorJob, job_id)
    if job is None:
        raise ValueError(f"DirectorJob {job_id} not found")
    job.status = "reviewing"
    db.commit()
    db.refresh(job)
    return job


def complete_job_if_slots_done(db: Session, job_id: str) -> DirectorJob:
    job = db.get(DirectorJob, job_id)
    if job is None:
        raise ValueError(f"DirectorJob {job_id} not found")
    terminal = {"completed", "failed", "replaced", "skipped"}
    all_done = all(s.status in terminal for s in job.slots)
    if all_done:
        any_failed = any(s.status == "failed" for s in job.slots)
        job.status = "completed" if not any_failed else "failed"
        job.completed_at = datetime.now(timezone.utc)
        if any_failed:
            failed = [s for s in job.slots if s.status == "failed"]
            job.error_message = f"{len(failed)} slot(s) ultimately failed"
        db.commit()
        db.refresh(job)
    return job
