"""Director 任务列表/详情/slot 列表端点."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DirectorJob, DirectorSlot
from app.schemas import DirectorJobOut, DirectorSlotOut
from app.routers.director_routes.common import _job_or_404

list_router = APIRouter(tags=["director"])

__all__ = ["list_router"]


@list_router.get("/jobs", response_model=list[DirectorJobOut])
def list_jobs(limit: int = 50, status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(DirectorJob).order_by(DirectorJob.created_at.desc())
    if status:
        q = q.filter(DirectorJob.status == status)
    return q.limit(limit).all()


@list_router.get("/jobs/{job_id}", response_model=DirectorJobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    return _job_or_404(db, job_id)


@list_router.get("/jobs/{job_id}/slots", response_model=list[DirectorSlotOut])
def list_slots(job_id: str, db: Session = Depends(get_db)):
    _job_or_404(db, job_id)
    return (
        db.query(DirectorSlot)
        .filter(DirectorSlot.director_job_id == job_id)
        .order_by(DirectorSlot.slot_index, DirectorSlot.created_at)
        .all()
    )
