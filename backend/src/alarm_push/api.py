from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from src.alarm_push.service import validate_push_url
from src.db.database import SessionLocal
from src.db.models import AlarmPushJob, AlarmPushTarget


router = APIRouter(prefix="/alarm-push", tags=["alarm-push"])


class TargetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    push_url: str = Field(max_length=2048)
    enabled: bool = True

    @field_validator("push_url")
    @classmethod
    def validate_url(cls, value):
        return validate_push_url(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value.strip()


def _row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns
            if column.name not in {"claim_token", "lease_until"}}


@router.post("/targets", status_code=201)
def create_target(body: TargetRequest):
    now = datetime.now()
    with SessionLocal() as session:
        target = AlarmPushTarget(id=uuid4().hex, **body.model_dump(), created_at=now, updated_at=now)
        session.add(target)
        session.commit()
        session.refresh(target)
        return _row_dict(target)


@router.get("/targets")
def list_targets():
    with SessionLocal() as session:
        rows = session.scalars(select(AlarmPushTarget).order_by(AlarmPushTarget.created_at.desc())).all()
        return {"items": [_row_dict(row) for row in rows], "total": len(rows)}


@router.put("/targets/{target_id}")
def update_target(target_id: str, body: TargetRequest):
    with SessionLocal() as session:
        target = session.get(AlarmPushTarget, target_id)
        if target is None:
            raise HTTPException(404, "push target not found")
        for key, value in body.model_dump().items():
            setattr(target, key, value)
        target.updated_at = datetime.now()
        session.commit()
        session.refresh(target)
        return _row_dict(target)


@router.get("/jobs")
def list_jobs(
    status: str | None = None, target_id: str | None = None, event_id: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    with SessionLocal() as session:
        query = session.query(AlarmPushJob)
        for field, value in (("status", status), ("target_id", target_id), ("event_id", event_id)):
            if value is not None:
                query = query.filter(getattr(AlarmPushJob, field) == value)
        total = query.count()
        rows = query.order_by(AlarmPushJob.created_at.desc(), AlarmPushJob.id.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size).all()
        return {"items": [_row_dict(row) for row in rows], "total": total,
                "page": page, "page_size": page_size}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as session:
        job = session.get(AlarmPushJob, job_id)
        if job is None:
            raise HTTPException(404, "push job not found")
        return _row_dict(job)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    with SessionLocal() as session:
        job = session.scalar(select(AlarmPushJob).where(AlarmPushJob.id == job_id).with_for_update())
        if job is None:
            raise HTTPException(404, "push job not found")
        if job.status != "failed":
            raise HTTPException(409, "only failed jobs can be retried")
        target = session.get(AlarmPushTarget, job.target_id)
        if target is None or not target.enabled:
            raise HTTPException(409, "push target is disabled or missing")
        job.status, job.attempts = "pending", 0
        job.next_retry_at = job.lease_until = job.claim_token = None
        job.last_error = job.response_status = None
        job.updated_at = datetime.now()
        session.commit()
        session.refresh(job)
        return _row_dict(job)
