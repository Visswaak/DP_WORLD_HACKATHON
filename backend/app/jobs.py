from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result: list[dict] | None = None
    error: str | None = None


_store: dict[str, Job] = {}
_TTL = timedelta(hours=1)


def _prune() -> None:
    """Remove jobs older than TTL to prevent unbounded memory growth."""
    now = datetime.now(timezone.utc)
    expired = [jid for jid, job in _store.items() if now - job.created_at > _TTL]
    for jid in expired:
        del _store[jid]


def create_job() -> str:
    _prune()
    job_id = str(uuid4())
    _store[job_id] = Job(
        id=job_id,
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
    )
    return job_id


def get_job(job_id: str) -> Job | None:
    return _store.get(job_id)


def update_job(
    job_id: str,
    status: JobStatus,
    result: list[dict] | None = None,
    error: str | None = None,
) -> None:
    job = _store.get(job_id)
    if job:
        job.status = status
        job.result = result
        job.error = error
        job.updated_at = datetime.now(timezone.utc)
